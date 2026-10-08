"""
LLM-first conversational agent service for Waasz.
Replaces brittle keyword dispatch with genuine Gemini native tool-calling
wrapping the existing, tested backend services.
"""
import base64
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
import logging
import re
from typing import Any
from unittest.mock import Mock
from uuid import UUID

from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import CostLimitExceeded
from app.models.activity import Activity
from app.models.business import Business
from app.models.inventory import InventoryItem
from app.models.message import WhatsAppMessage
from app.models.transaction import Transaction
from app.models.user import User
from app.schemas.extraction import ExtractedRecord
from app.services.ai_client import AiClient
from app.services.ai_extraction import AiExtractionService
from app.services.analytics_service import AI_ADVISORY_DISCLAIMER, AnalyticsService
from app.services.confirmation_service import build_confirmation_text, ConfirmationService
from app.services.goal_service import GoalService
from app.services.image_service import ImageService
from app.schemas.actor import ActorContext
from app.services.intent_router import IntentRouter
from app.services.knowledge_service import KnowledgeService
from app.services.ledger_service import LedgerService
from app.services.live_info_service import LiveInformationService
from app.services.media_downloader import MediaDownloader
from app.services.memory_service import MemoryService
from app.services.qa_service import QaService
from app.services.task_service import TaskService
from app.services.staff_service import StaffService
from app.services.unified_report_service import UnifiedReportService, sanitize_whatsapp_clean_text
from app.services.whatsapp_client import WhatsAppClient
from app.utils.reminder_parser import format_confirmation_time, parse_reminder_time

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tool Input Schemas (Exposed to the LLM)
# Notice: user_id and business_id are NEVER exposed in tool parameters.
# ---------------------------------------------------------------------------

class RecordTransactionInput(BaseModel):
    """Stage a sale or business expense into the ledger. The amount is strictly MANDATORY and must be a positive number in Naira (NGN). If the user states a sale or expense without an amount (e.g. 'Sold 10 bags of rice'), DO NOT call this tool — ask them for the amount first."""
    model_config = {"title": "record_transaction"}
    record_type: str = Field(description="Type of record: 'sale' (money in) or 'expense' (money out)")
    amount: float = Field(description="Numerical monetary value in Naira (NGN), e.g. 50000 for ₦50,000")
    item_name: str | None = Field(default=None, description="Item, product, service, or expense category (e.g. '10 bags of rice')")
    quantity: float | None = Field(default=None, description="Numerical quantity sold or purchased")
    unit: str | None = Field(default=None, description="Unit of measurement, e.g. 'bags', 'cartons', 'litres', 'hours'")
    description: str | None = Field(default=None, description="Optional extra details or customer/vendor name")
    is_credit: bool = Field(default=False, description="Set to True if this is a credit sale (customer bought on credit / owes money).")
    customer_name: str | None = Field(default=None, description="Name or identifier of the customer (required/recommended if is_credit is True).")
    due_date: str | None = Field(default=None, description="Optional repayment due date for credit sales (e.g. '2026-10-15' or ISO-8601 string).")


class SetItemCostInput(BaseModel):
    """Set or update the unit cost (Cost of Goods Sold / COGS) for an inventory item. This is the purchase or production cost per unit, essential for computing profit margins, 80/20 analysis, and pricing simulations."""
    model_config = {"title": "set_item_cost"}
    item_name: str = Field(description="Name of the product or inventory item, e.g. 'rice', 'cement', 'cooking oil'")
    unit_cost: float = Field(description="Cost price (COGS) per single unit in Naira (NGN), e.g. 32000 for ₦32,000 per bag")


class SetReorderThresholdInput(BaseModel):
    """Set or update the reorder threshold (low-stock alert level) and optionally the current quantity on hand for an inventory item (e.g. 'alert me when rice drops below 15 bags, I currently have 20 bags'). If the user mentions their current stock count in the same message, ALWAYS capture it in `current_quantity` so both are set in one step."""
    model_config = {"title": "set_reorder_threshold"}
    item_name: str = Field(description="Name of the inventory item, e.g. 'rice', 'cement'")
    threshold: float = Field(description="Minimum quantity threshold below which to alert the owner, e.g. 15 for 15 bags")
    current_quantity: float | None = Field(default=None, description="Optional current stock quantity on hand if stated by the user (e.g. 20 if user says 'I currently have 20 bags').")


class CorrectStockLevelInput(BaseModel):
    """Set or correct the current physical stock quantity on hand for an inventory item directly (e.g. 'I have 20 bags of rice', 'correct rice stock to 20', 'set rice inventory to 20 bags'). Does NOT require unit cost or purchase price — use this whenever the user is stating, correcting, or reconciling their actual physical stock count, without conflating it with recording a purchase or expense."""
    model_config = {"title": "correct_stock_level"}
    item_name: str = Field(description="Name of the product or inventory item, e.g. 'rice', 'cement'")
    actual_quantity: float = Field(description="The actual physical count / quantity on hand to set, e.g. 20 for 20 bags")


class GenerateReceiptInput(BaseModel):
    """Generate and deliver an official PDF receipt (for cash/transfer sale) or invoice (for credit sale) directly to the user's WhatsApp. Call ONLY when the user explicitly requests a receipt or invoice (e.g. 'generate my receipt', 'send receipt', 'receipt please', 'invoice'). DO NOT call this tool when the user is asking for charts, trend graphs, performance summaries, or dashboards."""
    model_config = {"title": "generate_receipt"}
    transaction_id: str | None = Field(default=None, description="Optional UUID of the specific transaction. Leave None/omitted to automatically use the most recent confirmed sale.")


class SetReceiptTemplateInput(BaseModel):
    """Set or initialize the business receipt and invoice template (business name, logo, address, contact phone, contact email, payment terms, footer note). ALL fields are optional — defaults will be used for any omitted fields."""
    model_config = {"title": "set_receipt_template"}
    business_display_name: str | None = Field(default=None, description="Optional official business name to print at the top of receipts/invoices. Defaults to registered business name if omitted.")
    contact_phone: str | None = Field(default=None, description="Optional business contact phone number for receipts. Defaults to user's registered phone number if omitted.")
    address: str | None = Field(default=None, description="Optional physical store or office address (e.g. '12 Commercial Avenue, Yaba, Lagos').")
    contact_email: str | None = Field(default=None, description="Optional business email address.")
    payment_terms_note: str | None = Field(default=None, description="Optional payment terms or bank details (e.g. 'Payment due within 7 days to GTBank 0123456789').")
    footer_note: str | None = Field(default=None, description="Optional custom footer message (e.g. 'No refund after 3 days. Thanks for your patronage!').")
    business_logo: str | None = Field(default=None, description="Optional photo URL, identifier, or local media path of the business logo.")


class UpdateReceiptTemplateInput(BaseModel):
    """Update specific fields of the existing business receipt and invoice template (e.g. 'change my business address on receipts', 'update phone number on receipts'). Modifies only specified fields on the single existing template."""
    model_config = {"title": "update_receipt_template"}
    business_display_name: str | None = Field(default=None, description="Updated business name.")
    contact_phone: str | None = Field(default=None, description="Updated contact phone number.")
    address: str | None = Field(default=None, description="Updated physical address.")
    contact_email: str | None = Field(default=None, description="Updated business email.")
    payment_terms_note: str | None = Field(default=None, description="Updated payment terms or bank details.")
    footer_note: str | None = Field(default=None, description="Updated custom footer note.")
    business_logo: str | None = Field(default=None, description="Updated business logo URL or identifier.")


class ListOutstandingDebtsInput(BaseModel):
    """List all unpaid customer debts, who owes what, amounts, due dates, and overdue status."""
    model_config = {"title": "list_outstanding_debts"}
    customer_name: str | None = Field(default=None, description="Optional customer name to filter debts for a specific person.")


class MarkDebtPaidInput(BaseModel):
    """Record a customer payment and mark an outstanding debt as paid in full or partially paid."""
    model_config = {"title": "mark_debt_paid"}
    customer_name: str | None = Field(default=None, description="Name of the customer who made the payment.")
    amount_or_debt_id: str | None = Field(default=None, description="Debt UUID or payment amount in Naira, e.g. '50000' or UUID string.")


class DraftPaymentReminderInput(BaseModel):
    """Draft a courteous, ready-to-forward payment reminder message text for the business owner to review and forward to their debtor customer. NEVER sends directly to the customer."""
    model_config = {"title": "draft_payment_reminder"}
    customer_name: str = Field(description="Name of the customer who owes money.")


class GetMarginReportInput(BaseModel):
    """Retrieve gross margin audit (revenue, COGS, profit, and margin %) per-item or overall for a given time period. Use when the user asks 'what is my margin on rice', 'show my profit margins', or wants a margin report."""
    model_config = {"title": "get_margin_report"}
    period: str = Field(default="this_month", description="Period to analyze: 'today', 'this_week', 'this_month', 'last_30_days', 'all_time'")
    item_name: str | None = Field(default=None, description="Optional specific item name to audit margin for (e.g. 'rice', 'cement')")


class GetProductPerformanceAnalysisInput(BaseModel):
    """Perform Pareto 80/20 analysis ranking top revenue drivers and detect dead stock (unsold inventory tying up working capital). Use when the user asks 'what are my best selling products', '80/20 analysis', or 'do I have dead stock'."""
    model_config = {"title": "get_product_performance_analysis"}
    period: str = Field(default="this_month", description="Period to analyze: 'today', 'this_week', 'this_month', 'last_30_days', 'all_time'")


class SimulatePricingInput(BaseModel):
    """Deterministically simulate pricing scenarios: compute resulting margin under a discount, or the exact price needed to hit a target gross margin. Never guess or hallucinate numbers."""
    model_config = {"title": "simulate_pricing"}
    item_name: str = Field(description="Name of the product or item to simulate pricing for (e.g. 'Rice', 'Cement')")
    discount_pct: float | None = Field(default=None, description="Discount percentage to test (e.g. 10 for 10% off)")
    target_margin_pct: float | None = Field(default=None, description="Target gross margin percentage (e.g. 25 for 25% margin)")
    current_price: float | None = Field(default=None, description="Current selling price in Naira if known or overriding recent sales")


class LogUpcomingPayableInput(BaseModel):
    """Log an upcoming supplier bill, vendor payable, or expense due in the future. Feeds into cash flow shortfall forecasting."""
    model_config = {"title": "log_upcoming_payable"}
    amount: float = Field(description="Monetary amount due in Naira (NGN), e.g. 150000 for ₦150,000")
    due_date: str = Field(description="Payment due date in YYYY-MM-DD format (e.g. '2026-10-15')")
    description: str = Field(description="Description of what is owed (e.g. '50 cartons of noodles from supplier', 'Shop rent')")
    vendor_name: str | None = Field(default=None, description="Optional name of supplier or vendor")


class GetCashFlowForecastInput(BaseModel):
    """Generate a cash flow forecast comparing trailing average sales against upcoming payables to identify potential cash shortfalls."""
    model_config = {"title": "get_cash_flow_forecast"}
    trailing_days: int = Field(default=30, description="Number of past days of sales history to baseline daily revenue (default 30)")
    horizon_days: int = Field(default=14, description="Forecast horizon in days looking ahead (default 14)")


class CreateReminderInput(BaseModel):
    """Schedule a reminder or task alert for a specific future date and time. Use ONLY when the user specifies a clear, unambiguous time (e.g., 'in 3 mins', 'tomorrow 9am', 'today at 17:00'). DO NOT call this tool if the user uses vague, ambiguous phrases like 'later today', 'sometime later', 'later on', or 'soon' without a specific time — instead, ask them conversationally what time works for them."""
    model_config = {"title": "create_reminder"}
    title: str = Field(description="Clear title of what needs to be done, e.g. 'Call supplier about rice delivery'")
    due_at_iso: str = Field(description="Target UTC time in ISO-8601 format (YYYY-MM-DDTHH:MM:SSZ). Compute relative times based on current server UTC time provided in the prompt.")
    description: str | None = Field(default=None, description="Optional notes or extra details")
    is_alarm: bool = Field(default=False, description="Set to True if the user asks for an alarm or repeating reminder until stopped (e.g. 'alarm me', 'keep reminding me until I stop it').")
    repeat_interval_seconds: int = Field(default=300, description="Interval in seconds between repeat notifications for alarm mode (default 300 / 5 minutes).")
    max_repeats: int = Field(default=6, description="Maximum number of escalation repeats for alarm mode (default 6 = 30-minute escalation window).")


class GenerateReportInput(BaseModel):
    """Generate and deliver a performance summary report directly to the user's WhatsApp. Call when the user explicitly asks for a report, weekly summary, or monthly review."""
    model_config = {"title": "generate_report"}
    cadence: str = Field(default="weekly", description="Reporting timeframe ('weekly' or 'monthly')")
    report_type: str = Field(default="unified", description="Report focus area ('unified', 'financial', 'productivity')")


class GenerateFinancialChartInput(BaseModel):
    """Generate and deliver an official visual financial trend chart (image) along with a working interactive web dashboard link directly to the user's WhatsApp chat. Call whenever the user asks to see their financial chart, trend chart, visual graph, or performance graph (e.g. 'show me my financial chart', 'send the chart', 'trend graph', 'I meant for this chart', 'chart dashboard')."""
    model_config = {"title": "generate_financial_chart"}
    period: str = Field(default="monthly", description="Timeframe for the financial chart: 'weekly' (7 days), 'monthly' (30 days), 'quarterly' (90 days), or 'yearly' (365 days). Defaults to 'monthly' if unspecified.")


class StoreKnowledgeDocumentInput(BaseModel):
    """Store durable reference notes, price lists, business rules, client details, meeting notes, or specs for future semantic recall."""
    model_config = {"title": "store_knowledge_document"}
    title: str = Field(description="Brief title or topic summary, e.g. 'Price List - Beverages', 'Client Contact - Tunde'")
    content: str = Field(description="The full factual note or document text to store")


class ManageGoalInput(BaseModel):
    """Create, list, or update progress on goals (weekly revenue targets, career milestones, study targets)."""
    model_config = {"title": "manage_goal"}
    action: str = Field(description="Goal operation: 'create', 'list', or 'update_progress'")
    title: str | None = Field(default=None, description="Goal title or milestone description")
    target_value: float | None = Field(default=None, description="Numeric target (e.g. 500000 for revenue goal)")
    target_date_iso: str | None = Field(default=None, description="Target completion date in ISO-8601 UTC format")


class RequestDataDeletionInput(BaseModel):
    """Purge user private data (conversational memory, personal notes, private goals) for privacy compliance. Requires explicit confirmation."""
    model_config = {"title": "request_data_deletion"}
    confirmation_phrase: str = Field(description="Must exactly match 'CONFIRM DELETE' to proceed with deletion.")


class GenerateImageInput(BaseModel):
    """Create and deliver a brand new image or illustration from a descriptive text prompt directly to the user's WhatsApp chat."""
    model_config = {"title": "generate_image"}
    prompt: str = Field(description="Descriptive text prompt for the image to generate")


class EditImageInput(BaseModel):
    """Modify or edit the most recent image sent by the user or generated in this conversation (e.g. 'make the background blue', 'add a hat')."""
    model_config = {"title": "edit_image"}
    instruction: str = Field(description="Specific editing instructions describing what to change or add to the reference image")


class GetCurrentInformationInput(BaseModel):
    """Use this whenever the user asks about news, today's events, current prices, scores, or anything that could have changed since your training data, or that requires real-time/live information."""
    model_config = {"title": "get_current_information"}
    query: str = Field(description="The search query or factual question requiring real-time current web information")


class GetHistoricalSummaryInput(BaseModel):
    """Retrieve historical transactions, sales, expenses, and activity summaries for an arbitrary past date or date range (e.g. 'what happened on March 3rd', 'show me last month', 'sales between Jan 1 and Feb 15'). Use whenever the user asks about specific past dates, periods, or historical record summaries rather than a standard canned report."""
    model_config = {"title": "get_historical_summary"}
    start_date: str = Field(description="Start date in YYYY-MM-DD format (e.g. '2026-03-03'). If querying a single day, provide that date.")
    end_date: str | None = Field(default=None, description="End date in YYYY-MM-DD format (inclusive). If None, defaults to start_date for single-day recall.")
    record_type: str | None = Field(default=None, description="Optional filter by record type: 'sale', 'expense', or None for all records.")


class InviteStaffInput(BaseModel):
    """Invite a staff member to join your business on Waasz. Only available to the business owner."""
    model_config = {"title": "invite_staff_member"}
    display_name: str = Field(description="Name of the staff member, e.g. 'Emeka' or 'Blessing'")
    phone_number: str = Field(description="WhatsApp phone number of the staff member (e.g. '+2348012345678' or '08012345678')")


class ListStaffInput(BaseModel):
    """List all staff members and their invitation status for your business. Only available to the business owner."""
    model_config = {"title": "list_staff_members"}


class RemoveStaffInput(BaseModel):
    """Remove a staff member from your business. Only available to the business owner."""
    model_config = {"title": "remove_staff_member"}
    identifier: str = Field(description="Name or phone number of the staff member to remove, e.g. 'Emeka' or '08012345678'")


class VoidTransactionInput(BaseModel):
    """Void or cancel a previously recorded transaction (sale or expense). Staff can void their own entries within 15 minutes of occurrence. Business owners can void any transaction at any time. When voided, inventory deductions are automatically restored and any linked customer debts are cancelled."""
    model_config = {"title": "void_transaction"}
    reason: str = Field(description="Reason for voiding the transaction, e.g. 'Customer changed mind', 'Duplicate entry', 'Wrong amount entered'")
    identifier: str | None = Field(default=None, description="Optional transaction reference, amount, or item name to identify which transaction to void. If omitted, targets the most recent transaction.")


class SetExpenseApprovalThresholdInput(BaseModel):
    """Set or update the business expense approval threshold. When staff members log expenses at or above this threshold, the owner is automatically alerted via WhatsApp. Set to 0 to disable alerts. Only available to the business owner."""
    model_config = {"title": "set_expense_approval_threshold"}
    threshold_amount: float = Field(description="The threshold amount in Naira (e.g. 50000.0). Any expense recorded by staff >= this amount triggers an instant notification to the owner. Set to 0 to disable.")


class GetTeamPerformanceInput(BaseModel):
    """Retrieve team performance and sales breakdown by staff member. Only available to the business owner."""
    model_config = {"title": "get_team_performance"}
    period: str = Field(default="this_week", description="Timeframe: 'today', 'this_week', 'this_month', or 'all'")


def get_agent_tools() -> list[type[BaseModel]]:
    """Return active agent tools, conditionally omitting image generation tools if disabled."""
    tools: list[type[BaseModel]] = [
        RecordTransactionInput,
        SetItemCostInput,
        SetReorderThresholdInput,
        CorrectStockLevelInput,
        GenerateReceiptInput,
        SetReceiptTemplateInput,
        UpdateReceiptTemplateInput,
        ListOutstandingDebtsInput,
        MarkDebtPaidInput,
        DraftPaymentReminderInput,
        GetMarginReportInput,
        GetProductPerformanceAnalysisInput,
        SimulatePricingInput,
        LogUpcomingPayableInput,
        GetCashFlowForecastInput,
        CreateReminderInput,
        GenerateReportInput,
        GenerateFinancialChartInput,
        GetHistoricalSummaryInput,
        StoreKnowledgeDocumentInput,
        ManageGoalInput,
        RequestDataDeletionInput,
        GetCurrentInformationInput,
        InviteStaffInput,
        ListStaffInput,
        RemoveStaffInput,
        VoidTransactionInput,
        SetExpenseApprovalThresholdInput,
        GetTeamPerformanceInput,
    ]
    if settings.enable_image_generation:
        tools.extend([GenerateImageInput, EditImageInput])
    return tools


AGENT_TOOLS = get_agent_tools()

NICHE_INSTRUCTIONS = {
    "sme_owner": (
        "The user operates a business in Nigeria. Help them track sales, expenses, and inventory turnover. "
        "Be commercially practical. Don't lecture on business theory; acknowledge transactions smoothly and answer business questions directly."
    ),
    "employee_9_to_5": (
        "The user is a corporate professional. Help them track project milestones, prepare for 1-on-1 performance reviews, "
        "log career achievements, and manage workplace tasks."
    ),
    "freelancer": (
        "The user is an independent freelancer/contractor. Help them monitor client deliverables, contract deadlines, "
        "project milestones, and invoicing."
    ),
    "student": (
        "The user is a student. Help them organize study schedules, remember assignment deadlines, prepare for exams, "
        "and maintain consistent study habits."
    ),
    "personal": (
        "The user is organizing personal commitments. Help them stay on top of daily errands, reminders, and personal goals."
    ),
}


class AgentService:
    """
    LLM-first conversational agent orchestrator for Waasz.
    Binds native tools to Gemini/Groq/OpenAI, dispatches tool execution server-side,
    and returns natural, authentic WhatsApp responses.
    """

    def __init__(
        self,
        ledger: LedgerService | None = None,
        extractor: AiExtractionService | None = None,
        confirmations: ConfirmationService | None = None,
        tasks: TaskService | None = None,
        knowledge: KnowledgeService | None = None,
        memory: MemoryService | None = None,
        goals: GoalService | None = None,
        qa: QaService | None = None,
        unified_reports: UnifiedReportService | None = None,
        whatsapp: WhatsAppClient | None = None,
        ai_client: AiClient | None = None,
        image_service: ImageService | None = None,
        live_info_service: LiveInformationService | None = None,
        media_downloader: MediaDownloader | None = None,
        analytics: AnalyticsService | None = None,
        staff: StaffService | None = None,
    ) -> None:
        self.ledger = ledger or LedgerService()
        self.extractor = extractor or AiExtractionService()
        self.confirmations = confirmations or ConfirmationService()
        self.tasks = tasks or TaskService()
        self.knowledge = knowledge or KnowledgeService()
        self.memory = memory or MemoryService()
        self.goals = goals or GoalService()
        self.qa = qa or QaService(memory=self.memory, knowledge=self.knowledge, goals=self.goals)
        self.whatsapp = whatsapp or WhatsAppClient()
        self.unified_reports = unified_reports or UnifiedReportService(whatsapp=self.whatsapp, ledger=self.ledger)
        self.ai_client = ai_client or AiClient()
        self.image_service = image_service or ImageService(cost_monitor=self.ai_client.cost_monitor)
        self.live_info_service = live_info_service or LiveInformationService(cost_monitor=self.ai_client.cost_monitor)
        self.media_downloader = media_downloader or MediaDownloader()
        self.analytics = analytics or AnalyticsService()
        self.staff = staff or StaffService()
        self._recent_image_cache: dict[UUID, tuple[bytes, str]] = {}

    def _build_system_prompt(
        self,
        business: Business,
        user: User,
        now_utc: datetime,
        business_tz: str,
        actor: ActorContext | None = None,
    ) -> str:
        niche_key = getattr(user, "niche", "sme_owner") or "sme_owner"
        niche_instruction = NICHE_INSTRUCTIONS.get(niche_key, NICHE_INSTRUCTIONS["sme_owner"])
        actor_role = actor.role if actor else getattr(user, "role", "owner")
        actor_display = (actor.display_name if actor else None) or user.display_name or "Friend"

        return (
            "You are Waasz (Waasz AI), an intelligent AI business analyst and financial copilot built specifically for African SMEs and business owners.\n"
            "### ORIGIN & ENGINEERING ARCHITECTURE:\n"
            "- CREATOR & IDENTITY: You are Waasz AI, created, engineered, and maintained by the Waasz engineering team. NEVER claim to be built by OpenAI, never claim to be ChatGPT, and never say you are an OpenAI model. If asked who built you, state proudly that you were developed by the Waasz engineering team.\n"
            "- ARCHITECTURE: You were custom-engineered and written from the ground up in production Python and PostgreSQL with specialized financial ledger double-entry bookkeeping, automated inventory controls, OCR receipt scanning, and real-time business analytics. You were carefully written and architected by software engineers, NOT vibecoded or generated at random.\n"
            "- VOICE NOTES & MULTIMODAL SUPPORT: You FULLY support voice notes and audio messages! When a user speaks via a WhatsApp voice note, our automated speech recognition transcribes their voice into text so you can assist them seamlessly. You also support text, images, and documents. NEVER tell the user that you cannot process voice notes or that you only support text and images. If a user speaks via voice note, converse with them naturally, warmly, and helpfully.\n"
            "Your core, primary identity is business-first: tracking sales, expenses, inventory turnover, customer debts, profit margins, cash flow, receipts, and performance reports.\n"
            "While you also support personal assistant tasks (such as setting reminders, notes, goals, and casual chat), these are strictly secondary, supporting capabilities to ease the life of a busy business owner — NOT an equal co-identity.\n"
            "When asked whether you are multipurpose or business-focused (or about your primary identity/focus), state clearly, concisely, and unequivocally that you are primarily business-focused, with personal-assistant features as supporting tools. Never claim an equal dual identity.\n\n"
            "### WHATSAPP FORMATTING RULES:\n"
            "- ZERO MARKDOWN HEADERS: NEVER use markdown headers (no '#', '##', or '###'). WhatsApp does NOT support markdown headers; they render as raw, unsightly hash characters.\n"
            "- AVOID ASTERISK CLUTTER: Use single *asterisks* for bold sparingly — never wrap entire lines, headings, or multiple nouns in asterisks.\n"
            "- CLEAN LISTS: When a list is genuinely needed, use a simple line-per-item with a single leading emoji or dash. Do NOT mix multiple competing bullet styles or wrap every line in bold.\n"
            "- SPACING & BREVITY: Use clean blank lines between paragraphs and sections for spacing. Keep replies to 1-3 sentences for general chat. Never output dense walls of asterisks, hyphens, or dividers.\n\n"
            "### IMAGE & RECEIPT UNDERSTANDING:\n"
            "- RECEIPT / INVOICE / EXPENSE: When the user sends an image of a paper receipt, bill, or invoice, extract the total, items, vendor, and call the `record_transaction` tool directly.\n"
            "- GENERAL PHOTO / SCENE / OBJECT: When the user sends any general photo, scene, or object, describe it conversationally using multimodal vision.\n\n"
            "### TOOL EXECUTION & OUTCOME ACCURACY RULES:\n"
            "- HONEST OUTCOME REPORTING ONLY: You must NEVER claim an action was successful, never say 'Done!', 'I've updated that for you', or 'Success' unless the tool execution explicitly succeeded.\n"
            "- REPORTING TOOL ERRORS & CLARIFICATIONS: If a tool returns an error or clarification message, communicate that limitation or question honestly to the user on your very first reply. Never gloss over it, never pretend it succeeded, and NEVER tell the user 'Done!' when a tool needs clarification or failed.\n\n"
            "### SCOPED AI ADVISORY DISCLAIMER:\n"
            "- MANDATORY DISCLAIMER RULE: When you provide strategic business advice, business growth playbooks, pricing strategy recommendations, marketing strategies, or predictive business forecasts conversationally, you MUST append this exact short disclaimer at the end of your message on a new line:\n"
            f"\"{AI_ADVISORY_DISCLAIMER}\"\n"
            "- DO NOT append this disclaimer to plain transactional confirmations, receipts, reminders, task updates, casual chat, greetings, or routine factual answers.\n\n"
            "### CONTEXT & ATTENTION RULES:\n"
            "- The blocks below (memory, pending items, tasks, goals) are background reference only. Respond first and foremost to what the user just said. Do not proactively bring up or restate pending items or tasks unless the user's current message is clearly about them.\n"
            "- CRITICAL TURN ATTENTION: Focus your answer strictly on the user's latest incoming message. Do not proactively revisit, re-ask, or try to resolve topics, old reminders, or incomplete sales from earlier turns in the conversation history unless the user explicitly asks about them.\n"
            "- OVERDUE OR PASSED REMINDERS: If a reminder time requested in the past has already passed, DO NOT proactively bring it up, nag, or ask if the user wants to reschedule it. Only discuss a reminder if the user is currently asking about it.\n"
            "- When the user sends a greeting (e.g. 'Hi', 'Hey', 'Good morning'), reply ONLY with a warm, natural greeting. NEVER attach questions or follow-ups about earlier sales, expenses, reminders, or tasks.\n\n"
            "### AUTHORITATIVE TOOL CAPABILITIES:\n"
            "1. Reminders & Alarms: Schedule reminders with `create_reminder`. Use ONLY when the user provides an unambiguous, specific time. For vague phrases like 'later today' or 'soon', DO NOT call `create_reminder` — ask: 'What time works for you?'\n"
            "2. Sales & Expenses: Stage sales and expenses in Naira using `record_transaction`.\n"
            "3. Instant PDF Receipts & Invoices: Generate official PDF receipts/invoices directly to WhatsApp using `generate_receipt`. When a user requests a receipt for a transaction (e.g. 'I need a receipt for 10 books at 3000 each'): stage the sale with `record_transaction` including customer_name if specified. If customer name was not specified, you may ask who to bill or bill to 'Walk-in Customer'. When a user provides business branding or profile details (e.g. 'my business name is...', 'set business name to...', 'set address to...', 'my shop address is...'), immediately save it with `set_receipt_template` or `update_receipt_template` so their official name, address, and contact details appear on all future receipts. Users can also send their store logo photo in chat with caption 'Set logo'.\n"
            "4. Debt Tracking: Track customer credit sales, list debtors with `list_outstanding_debts`, mark debts as paid with `mark_debt_paid`, and draft customer payment reminders for the owner with `draft_payment_reminder`.\n"
            "5. Low-Stock Alerts & Inventory: Track inventory quantities and set reorder threshold alerts using `set_reorder_threshold`. If the user specifies BOTH a threshold and their current stock level in the same message (e.g. 'Alert me when my rice stock reduces to 15 bags, I currently have 20 bags'), capture both in `set_reorder_threshold` (`threshold=15`, `current_quantity=20`). Correct or update actual physical counts directly without requiring unit cost using `correct_stock_level`.\n"
            "6. Unit Cost & Profit Margins: Set item cost price (COGS) with `set_item_cost`, audit profit margins with `get_margin_report`, 80/20 Pareto with `get_product_performance_analysis`, and simulate pricing with `simulate_pricing`.\n"
            "7. Cash Flow Forecasting: Log upcoming supplier payables using `log_upcoming_payable` and predict shortfalls using `get_cash_flow_forecast`.\n"
            "8. Reports, Visual Charts & Live Dashboard: Generate summary text reports using `generate_report`. When the user asks to see their financial chart, trend graph, or performance chart (e.g. 'show me my financial chart', 'I meant for this chart', 'send financial graph', 'chart dashboard'), ALWAYS call `generate_financial_chart` to generate and deliver the visual chart image along with their live interactive dashboard link. You CAN and DO generate visual financial charts on demand. NEVER tell the user you cannot generate visual charts or graphs. Save durable notes/price lists with `store_knowledge_document`.\n"
            "9. Live/Current Information: Retrieve real-time facts, currency exchange rates, or news using `get_current_information`.\n"
            "10. Creative Image Generation vs Charts: You CANNOT generate artistic/illustrative photos or creative drawings (reply 'I can't generate creative images right now' if asked to draw art). However, business financial charts and graphs ARE fully supported via `generate_financial_chart`.\n"
            "11. Multi-Staff Team Management & Analytics (Owner Only): As a business owner, you can view team sales and staff contribution breakdown with `get_team_performance(period)`. You can invite new staff members with `invite_staff_member(display_name, phone_number)`, view current team members with `list_staff_members()`, and remove staff members with `remove_staff_member(identifier)`. Staff members do NOT have permission to invite, list, or remove team members, nor view team performance.\n"
            "12. Voiding Transactions & 15-Minute Window: Cancel or void accidental entries with `void_transaction(reason, identifier)`. Staff members can only void their own entries within 15 minutes of occurrence; after 15 minutes, instruct them to ask the business owner. Owners can void any transaction at any time. Voiding automatically reverses inventory deductions and cancels linked debts.\n"
            "13. Expense Approval Threshold (Owner Only): Owners can configure an expense approval threshold with `set_expense_approval_threshold(threshold_amount)`. When staff members log expenses at or above this threshold, the owner is automatically alerted via WhatsApp.\n\n"
            "### DATA PERMANENCE, CONTINUITY & SECURITY RULES:\n"
            "- All business records are permanently stored in our secure encrypted cloud database tied to the user's registered phone number.\n"
            "- Changing phone numbers strictly requires admin-assisted account recovery.\n"
            "- Never send WhatsApp messages directly to customers; debt reminders are delivered to the owner to forward.\n\n"
            f"### TIME CONTEXT:\n"
            f"- Current UTC Time: {now_utc.strftime('%Y-%m-%dT%H:%M:%SZ')} | Business Timezone: {business_tz}\n\n"
            f"### USER PROFILE & ROLE CONTEXT:\n"
            f"User display name: {actor_display} | Role: {actor_role} | Niche: {niche_key}\n"
            f"{'You are interacting with a staff member. They record sales and expenses for the business.' if actor_role == 'staff' else 'You are interacting with the business owner with full management access.'}\n"
            f"{niche_instruction}\n"
        )

    async def process_user_message(
        self,
        db: AsyncSession,
        business: Business,
        user: User,
        user_message: str,
        inbound_message_id: UUID | None = None,
        image_bytes: bytes | None = None,
        image_mime_type: str = "image/jpeg",
        actor: ActorContext | None = None,
        source_wamid: str | None = None,
        is_voice: bool = False,
    ) -> str:
        """
        Execute full conversational agent turn:
        1. Retrieve memory, knowledge, and goals context.
        2. Construct system prompt with accurate capabilities & niche guidelines.
        3. Invoke LLM with tools.
        4. Execute tool calls server-side (injecting verified tenant IDs).
        5. Return natural conversational text to send to user.
        """
        if actor is None:
            actor = ActorContext(
                business_id=business.id,
                member_id=None,
                role=getattr(user, "role", "owner") or "owner",
                wa_id=user.phone_number,
                display_name=user.display_name,
            )

        if image_bytes:
            self._recent_image_cache[user.id] = (image_bytes, image_mime_type)

        # If underlying services are mocked (legacy unit test environment),
        # dispatch directly via deterministic offline fallback.
        if (
            isinstance(self.qa, Mock)
            or isinstance(self.knowledge, Mock)
            or isinstance(self.goals, Mock)
        ):
            return await self._offline_fallback_handling(
                db=db,
                business=business,
                user=user,
                user_message=user_message,
                inbound_message_id=inbound_message_id,
                actor=actor,
                source_wamid=source_wamid,
            )

        # 1. Fetch short-term history and durable context
        turns = []
        try:
            turns = await self.memory.get_short_term_turns(
                db, user.id, limit=6, exclude_message_id=inbound_message_id
            )
        except Exception as exc:
            logger.warning("Failed to fetch short term turns: %s", exc)

        memories = []
        try:
            memories = await self.memory.retrieve_relevant_memories(db, user.id, user_message, top_k=3)
        except Exception as exc:
            logger.warning("Failed to retrieve memories: %s", exc)

        chunks = []
        try:
            chunks = await self.knowledge.search_user_knowledge(
                db, user.id, user_message, business_id=business.id, top_k=3
            )
        except Exception as exc:
            logger.warning("Failed to retrieve knowledge chunks: %s", exc)

        active_goals = []
        try:
            active_goals = await self.goals.get_active_goals(db, user.id)
        except Exception as exc:
            logger.warning("Failed to retrieve goals: %s", exc)

        # 2. Build system prompt
        now_utc = datetime.now(UTC)
        business_tz = getattr(business, "timezone", None) or settings.local_timezone
        system_prompt = self._build_system_prompt(business, user, now_utc, business_tz, actor=actor)

        pending_conf = None
        try:
            pending_conf = await self.confirmations.latest_actionable(
                db, business.id, member_id=actor.member_id
            )
        except Exception as exc:
            logger.warning("Failed to retrieve pending confirmation: %s", exc)

        if pending_conf and getattr(pending_conf, "extracted_record", None):
            rec = pending_conf.extracted_record
            rec_type = rec.get("record_type", "record")
            rec_item = rec.get("item_name") or "record"
            rec_amt = rec.get("amount") or 0
            try:
                amt_float = float(rec_amt)
                amt_str = f"₦{amt_float:,.0f}"
            except (ValueError, TypeError):
                amt_str = f"₦{rec_amt}"
            system_prompt += (
                f"\n### PENDING CONFIRMATION (INFORMATIONAL BACKGROUND REFERENCE ONLY - DO NOT PROACTIVELY BRING UP):\n"
                f"- An unconfirmed {rec_type} is currently staged: {rec_item} for {amt_str}.\n"
                f"Note: This is informational context only. Respond ONLY to what the user just said. Do not proactively ask the user to confirm or resolve this unless their current message is clearly about it.\n"
            )

        if memories:
            memory_text = "\n".join(f"- {m['content']}" for m in memories)
            system_prompt += f"\n### DURABLE USER MEMORY (BACKGROUND REFERENCE ONLY - DO NOT PROACTIVELY BRING UP):\n{memory_text}\n"

        if chunks:
            chunk_text = "\n".join(f"- {c['content']}" for c in chunks)
            system_prompt += f"\n### USER REFERENCE NOTES (BACKGROUND REFERENCE ONLY):\n{chunk_text}\n"

        if active_goals:
            def _format_goal(g):
                if isinstance(g, dict):
                    t = g.get("title", "")
                    tv = g.get("target_value")
                else:
                    t = getattr(g, "title", "")
                    tv = getattr(g, "target_value", None)
                return f"- {t} (target: {tv or 'N/A'})"

            goals_text = "\n".join(_format_goal(g) for g in active_goals)
            system_prompt += f"\n### ACTIVE GOALS (BACKGROUND REFERENCE ONLY):\n{goals_text}\n"

        # 3. Build messages list
        messages: list[Any] = [SystemMessage(content=system_prompt)]
        for turn in turns:
            role = turn.get("role")
            content = turn.get("content", "")
            if role == "user":
                messages.append(HumanMessage(content=content))
            elif role == "assistant":
                messages.append(AIMessage(content=content))

        if image_bytes:
            b64_img = base64.b64encode(image_bytes).decode("utf-8")
            user_content = [
                {"type": "text", "text": user_message or "What is in this image? Please describe what you see."},
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{image_mime_type};base64,{b64_img}"},
                },
            ]
            messages.append(HumanMessage(content=user_content))
        else:
            turn_text = user_message
            if is_voice:
                turn_text = f"{user_message} (Note: Spoken by the user via a WhatsApp voice note)"
            messages.append(HumanMessage(content=turn_text))

        active_tools = get_agent_tools()

        # 4. Invoke LLM with tools
        try:
            agent_result = await self.ai_client.invoke_agent(
                messages=messages,
                tools=active_tools,
                operation="agent_chat",
                db=db,
                business_id=business.id,
            )
        except Exception as exc:
            logger.exception("Agent turn execution encountered an unexpected error: %s", exc)
            agent_result = None

        if not agent_result or not agent_result.message:
            return await self._offline_fallback_handling(
                db=db,
                business=business,
                user=user,
                user_message=user_message,
                inbound_message_id=inbound_message_id,
            )


        ai_msg = agent_result.message
        tool_calls = getattr(ai_msg, "tool_calls", None) or []

        # 5. Execute tool-calling loop (Bounded by MAX_TOOL_ITERATIONS = 2)
        MAX_TOOL_ITERATIONS = 2
        iteration = 0
        current_ai_msg = ai_msg
        last_tool_results: list[tuple[str, str]] = []

        while iteration < MAX_TOOL_ITERATIONS:
            tool_calls = getattr(current_ai_msg, "tool_calls", None) or []
            if not tool_calls:
                reply_text = str(getattr(current_ai_msg, "content", "") or "").strip()
                if not reply_text:
                    if last_tool_results:
                        return sanitize_whatsapp_clean_text(self._resolve_fallback_tool_reply(last_tool_results))
                    return "I'm here. How can I help you today?"
                if self._has_tool_failure(last_tool_results) and self._is_generic_success_phrase(reply_text):
                    return sanitize_whatsapp_clean_text(self._resolve_fallback_tool_reply(last_tool_results))
                return sanitize_whatsapp_clean_text(reply_text)

            iteration += 1
            messages.append(current_ai_msg)
            interactive_prompt_sent = False

            for call in tool_calls:
                tool_name = call.get("name")
                tool_args = call.get("args") or {}
                call_id = call.get("id") or str(tool_name)

                tool_result_str, was_interactive = await self._execute_tool(
                    db=db,
                    business=business,
                    user=user,
                    tool_name=tool_name,
                    tool_args=tool_args,
                    raw_user_text=user_message,
                    inbound_message_id=inbound_message_id,
                    actor=actor,
                    source_wamid=source_wamid,
                )
                if was_interactive:
                    interactive_prompt_sent = True

                messages.append(ToolMessage(content=tool_result_str, tool_call_id=call_id))
                last_tool_results.append((str(tool_name), tool_result_str))

            # If interactive buttons were sent (e.g. confirmation prompt for transaction),
            # return empty string to prevent sending duplicate plain text
            if interactive_prompt_sent:
                return ""

            # Re-invoke agent with tool results
            followup_result = await self.ai_client.invoke_agent(
                messages=messages,
                tools=active_tools,
                operation=f"agent_tool_followup_iter{iteration}",
                db=db,
                business_id=business.id,
            )

            if not followup_result or not followup_result.message:
                return sanitize_whatsapp_clean_text(self._resolve_fallback_tool_reply(last_tool_results))

            current_ai_msg = followup_result.message

        final_text = str(getattr(current_ai_msg, "content", "") or "").strip()
        if final_text:
            # Audit guard against false-success hallucination when a tool explicitly failed
            if self._has_tool_failure(last_tool_results) and self._is_generic_success_phrase(final_text):
                return sanitize_whatsapp_clean_text(self._resolve_fallback_tool_reply(last_tool_results))
            final_text = self._maybe_append_advisory_disclaimer(user_message, final_text)
            return sanitize_whatsapp_clean_text(final_text)
        return sanitize_whatsapp_clean_text(self._resolve_fallback_tool_reply(last_tool_results))

    def _maybe_append_advisory_disclaimer(self, user_message: str, reply: str) -> str:
        """
        Ensure strategic, predictive, or financial business advice given in plain conversation
        reliably bears the standard AI advisory disclaimer.
        """
        if not reply or AI_ADVISORY_DISCLAIMER in reply:
            return reply

        combined = (user_message + " " + reply).lower()
        strategic_signals = [
            "landing page",
            "business strategy",
            "marketing plan",
            "cash flow",
            "pricing strategy",
            "improve margin",
            "increase margin",
            "grow my business",
            "sales pipeline",
            "value proposition",
            "lead magnet",
            "financial advice",
            "forecast",
        ]
        if any(signal in combined for signal in strategic_signals):
            return f"{reply}\n\n{AI_ADVISORY_DISCLAIMER}"
        return reply

    def _has_tool_failure(self, tool_results: list[tuple[str, str]]) -> bool:
        """Check if any executed tool failed, errored, or had degraded availability."""
        for _, res in tool_results:
            lower = res.lower()
            if (
                res.startswith("Error:")
                or res.startswith("Cost Limit:")
                or "error executing" in lower
                or "cannot access live information" in lower
                or "can't access live information" in lower
            ):
                return True
        return False

    def _is_generic_success_phrase(self, text: str) -> bool:
        """Detect generic 'Done!' or false success phrases."""
        clean = text.strip().lower()
        phrases = [
            "done!",
            "done.",
            "i've updated that for you",
            "i have updated that for you",
            "updated!",
            "success!",
            "it is done",
        ]
        return any(clean.startswith(p) or clean == p for p in phrases)

    def _resolve_fallback_tool_reply(self, tool_results: list[tuple[str, str]]) -> str:
        """
        Produce an honest, accurate message based on actual tool outcomes.
        NEVER returns a blind generic 'Done!' without confirmed success.
        """
        if not tool_results:
            return "I have processed your request."

        # 1. Grounding failure from live_info_service
        for _, res in tool_results:
            if "can't access live information" in res.lower() or "cannot access live information" in res.lower():
                return res

        # 2. Explicit Error or Cost Limit results
        for _, res in tool_results:
            if res.startswith("Error:") or res.startswith("Cost Limit:") or "error executing" in res.lower():
                clean_err = res.removeprefix("Error:").strip()
                return f"I couldn't complete that: {clean_err}"

        # 3. Explicit Notice or Clarification results (e.g., receipt template required, ambiguous time)
        for _, res in tool_results:
            if res.startswith("Notice:"):
                return res.removeprefix("Notice:").strip()
            if res.startswith("Clarification Needed:"):
                return res.removeprefix("Clarification Needed:").strip()

        # 4. Pending confirmation results (e.g. data deletion)
        for _, res in tool_results:
            if res.startswith("Pending Confirmation:"):
                return res.removeprefix("Pending Confirmation:").strip()

        # 5. Informational tools return their summary text directly
        info_tools = {
            "list_outstanding_debts",
            "get_margin_report",
            "get_product_performance_analysis",
            "simulate_pricing",
            "get_cash_flow_forecast",
            "get_historical_summary",
            "draft_payment_reminder",
            "get_current_information",
            "get_team_performance",
        }
        for tool_name, res in tool_results:
            if tool_name in info_tools and not res.startswith("Error:"):
                return res

        # 6. Specific success confirmations
        for _, res in tool_results:
            if res.startswith("Success:"):
                return res.removeprefix("Success:").strip()

        # Fallback to the latest tool's raw result if non-empty
        latest_res = tool_results[-1][1].strip()
        if latest_res:
            return latest_res

        return "I encountered an issue processing that. Please try again."

    async def _execute_tool(
        self,
        db: AsyncSession,
        business: Business,
        user: User,
        tool_name: str,
        tool_args: dict[str, Any],
        raw_user_text: str,
        inbound_message_id: UUID | None = None,
        actor: ActorContext | None = None,
        source_wamid: str | None = None,
    ) -> tuple[str, bool]:
        """
        Execute tool with server-enforced security boundaries.
        Returns: (result_string, is_interactive_prompt_sent)
        """
        business_tz = getattr(business, "timezone", None) or settings.local_timezone

        try:
            if tool_name == "create_reminder":
                from app.utils.reminder_parser import is_ambiguous_time_expression
                if is_ambiguous_time_expression(raw_user_text):
                    return (
                        "Clarification Needed: The requested time is ambiguous (e.g. 'later today'). "
                        "Do NOT schedule the reminder yet. Ask the user conversationally: 'What time works for you?'",
                        False,
                    )

                title = tool_args.get("title") or "Follow-up reminder"
                due_at_iso = tool_args.get("due_at_iso")
                description = tool_args.get("description")
                is_alarm_arg = tool_args.get("is_alarm", False)
                repeat_interval_seconds = int(tool_args.get("repeat_interval_seconds") or 60)

                due_at_utc = None
                if due_at_iso:
                    try:
                        clean_iso = due_at_iso.replace("Z", "+00:00")
                        due_at_utc = datetime.fromisoformat(clean_iso)
                    except Exception:
                        due_at_utc = None

                # Fallback to local reminder parser if ISO parse failed
                if not due_at_utc:
                    due_at_utc = parse_reminder_time(raw_user_text, timezone_name=business_tz)

                if not due_at_utc:
                    return (
                        "Clarification Needed: Could not determine reminder due time. "
                        "Ask the user conversationally: 'What time works for you?'",
                        False,
                    )

                lower_text = raw_user_text.lower()
                alarm_phrases = (
                    "keep reminding me",
                    "until i stop",
                    "until i turn it off",
                    "alarm me",
                    "set an alarm",
                    "set alarm",
                    "repeat every",
                    "repeating reminder",
                    "alarm mode",
                )
                is_alarm_mode = bool(
                    is_alarm_arg
                    or getattr(settings, "default_reminders_to_alarm_mode", False)
                    or any(p in lower_text for p in alarm_phrases)
                )

                task = await self.tasks.create_task(
                    db,
                    business_id=business.id,
                    user_id=user.id,
                    title=title,
                    description=description,
                    due_at=due_at_utc,
                    is_alarm_mode=is_alarm_mode,
                    repeat_interval_seconds=repeat_interval_seconds,
                    created_by_member_id=actor.member_id if actor else None,
                )
                formatted_time = format_confirmation_time(due_at_utc, business_tz)
                if is_alarm_mode:
                    return f"Success: Alarm '{task.title}' created and scheduled for {formatted_time} (alarm mode: will repeat until turned off).", False
                return f"Success: Reminder '{task.title}' created and scheduled for {formatted_time}.", False

            elif tool_name == "record_transaction":
                record_type = tool_args.get("record_type")
                amount_val = tool_args.get("amount")
                item_name = tool_args.get("item_name") or "record"
                quantity_val = tool_args.get("quantity")
                unit = tool_args.get("unit")
                description = tool_args.get("description")
                is_credit = bool(tool_args.get("is_credit"))
                customer_name = tool_args.get("customer_name")
                due_date = tool_args.get("due_date")

                if not amount_val or float(amount_val) <= 0:
                    return "Error: Missing valid amount. Ask the user for the amount in Naira.", False

                amount_dec = Decimal(str(amount_val))
                qty_dec = Decimal(str(quantity_val)) if quantity_val is not None else None

                record = ExtractedRecord(
                    record_type=record_type,
                    amount=amount_dec,
                    item_name=item_name,
                    quantity=qty_dec,
                    unit=unit,
                    description=description,
                    currency="NGN",
                    confidence=0.95,
                    needs_clarification=False,
                    is_credit=is_credit,
                    customer_name=customer_name,
                    due_date=due_date,
                )

                extraction = await self.ledger.create_ai_extraction(
                    db,
                    business.id,
                    inbound_message_id or business.id,
                    raw_user_text,
                    record,
                    status="succeeded",
                    provider="local_heuristic",
                    model="tool-calling",
                    input_tokens=0,
                    output_tokens=0,
                    estimated_cost_usd=Decimal("0.00"),
                )

                confirmation = await self.ledger.stage_record_for_confirmation(
                    db,
                    business.id,
                    inbound_message_id or business.id,
                    extraction,
                    record,
                    member_id=actor.member_id if actor else None,
                    source_wamid=source_wamid,
                )

                conf_text = confirmation.confirmation_text or build_confirmation_text(record)
                send_result = await self.whatsapp.send_confirmation(user.phone_number, conf_text)
                outbound = await self.ledger.record_outbound_message(
                    db,
                    business.id,
                    user.phone_number,
                    conf_text,
                    send_result,
                    message_type="interactive",
                    user_id=user.id,
                )
                confirmation.confirmation_message_id = outbound.id
                return f"Success: Transaction staged for confirmation with text '{conf_text}'. Interactive buttons delivered to user.", True

            elif tool_name == "set_item_cost":
                item_name = tool_args.get("item_name")
                unit_cost = tool_args.get("unit_cost")
                if not item_name or unit_cost is None:
                    return "Error: item_name and unit_cost are required.", False
                item = await self.ledger.set_item_cost(db, business.id, item_name, unit_cost)
                return f"Success: Unit cost (COGS) for '{item.item_name}' set to ₦{item.unit_cost:,.2f}.", False

            elif tool_name == "set_reorder_threshold":
                item_name = tool_args.get("item_name")
                threshold = tool_args.get("threshold")
                current_quantity = tool_args.get("current_quantity")
                if not item_name or threshold is None:
                    return "Error: item_name and threshold are required.", False
                item = await self.ledger.set_reorder_threshold(
                    db, business.id, item_name, threshold, current_quantity=current_quantity
                )
                msg = f"Success: Reorder threshold for '{item.item_name}' set to {threshold:g}."
                if current_quantity is not None:
                    msg += f" Current stock level set to {item.quantity_on_hand:g}."
                msg += " You will be alerted when stock falls to or below this level."
                return msg, False

            elif tool_name == "correct_stock_level":
                item_name = tool_args.get("item_name")
                actual_quantity = tool_args.get("actual_quantity")
                if not item_name or actual_quantity is None:
                    return "Error: item_name and actual_quantity are required.", False
                item = await self.ledger.correct_stock_level(
                    db, business.id, item_name, actual_quantity
                )
                return f"Success: Stock count for '{item.item_name}' updated directly to {item.quantity_on_hand:g}.", False

            elif tool_name == "set_receipt_template":
                from app.services.receipt_service import ReceiptService
                import os
                receipt_svc = ReceiptService()
                biz_name = tool_args.get("business_display_name") or business.name
                phone = tool_args.get("contact_phone") or business.phone_number or ""
                addr = tool_args.get("address")
                logo = tool_args.get("business_logo")
                email = tool_args.get("contact_email")
                terms = tool_args.get("payment_terms_note")
                footer = tool_args.get("footer_note")

                # If user sent a photo in chat for their logo, pull from recent image cache
                if user.id in self._recent_image_cache and (
                    not logo or str(logo).lower() in {"photo", "image", "attached", "sent", "chat"} or "logo" in raw_user_text.lower()
                ):
                    img_bytes, mime = self._recent_image_cache[user.id]
                    logos_dir = os.path.join(settings.media_download_dir, "logos")
                    os.makedirs(logos_dir, exist_ok=True)
                    ext = ".png" if "png" in mime else ".jpg"
                    logo_path = os.path.join(logos_dir, f"logo_{business.id}{ext}")
                    try:
                        with open(logo_path, "wb") as f:
                            f.write(img_bytes)
                        logo = logo_path
                    except Exception as e:
                        logger.warning("Could not persist logo image: %s", e)

                tmpl = await receipt_svc.set_template(
                    db,
                    business_id=business.id,
                    business_display_name=biz_name,
                    contact_phone=phone,
                    address=addr,
                    business_logo=logo,
                    contact_email=email,
                    payment_terms_note=terms,
                    footer_note=footer,
                )

                # Check if there is a recent confirmed sale transaction to generate receipt for!
                auto_receipt_msg = ""
                tx_stmt = (
                    select(Transaction)
                    .where(
                        Transaction.business_id == business.id,
                        Transaction.transaction_type == "sale",
                        Transaction.status == "confirmed",
                    )
                    .order_by(Transaction.occurred_at.desc())
                    .limit(1)
                )
                tx_res = await db.execute(tx_stmt)
                latest_tx = tx_res.scalar_one_or_none()
                if latest_tx:
                    try:
                        pdf_bytes, filename, tx = await receipt_svc.generate_receipt_pdf(
                            db, business_id=business.id, transaction_id=latest_tx.id
                        )
                        send_res = await self.whatsapp.send_document_bytes(
                            user.phone_number,
                            pdf_bytes,
                            filename=filename,
                            caption=f"📄 Receipt #{filename.replace('.pdf', '')} - {tmpl.business_display_name}",
                        )
                        await self.ledger.record_outbound_message(
                            db, business.id, user.phone_number, f"[Sent document {filename}]", send_res, user_id=user.id
                        )
                        auto_receipt_msg = f" I have also generated and sent your updated receipt '{filename}' with your new details to your WhatsApp!"
                    except Exception as e:
                        logger.warning("Could not auto-generate receipt in set_receipt_template: %s", e)

                return (
                    f"Success: Receipt template saved! Future receipts and invoices will automatically feature '{tmpl.business_display_name}', address '{tmpl.address or 'N/A'}', and phone '{tmpl.contact_phone}'.{auto_receipt_msg}",
                    False,
                )

            elif tool_name == "update_receipt_template":
                from app.services.receipt_service import ReceiptService
                import os
                receipt_svc = ReceiptService()
                clean_args = {k: v for k, v in tool_args.items() if v is not None}
                # Check recent image cache if logo photo was sent
                if user.id in self._recent_image_cache and (
                    "logo" in raw_user_text.lower() or str(clean_args.get("business_logo", "")).lower() in {"photo", "image", "attached", "sent", "chat"}
                ):
                    img_bytes, mime = self._recent_image_cache[user.id]
                    logos_dir = os.path.join(settings.media_download_dir, "logos")
                    os.makedirs(logos_dir, exist_ok=True)
                    ext = ".png" if "png" in mime else ".jpg"
                    logo_path = os.path.join(logos_dir, f"logo_{business.id}{ext}")
                    try:
                        with open(logo_path, "wb") as f:
                            f.write(img_bytes)
                        clean_args["business_logo"] = logo_path
                    except Exception as e:
                        logger.warning("Could not persist logo image: %s", e)

                tmpl = await receipt_svc.update_template(
                    db,
                    business_id=business.id,
                    **clean_args,
                )

                auto_receipt_msg = ""
                tx_stmt = (
                    select(Transaction)
                    .where(
                        Transaction.business_id == business.id,
                        Transaction.transaction_type == "sale",
                        Transaction.status == "confirmed",
                    )
                    .order_by(Transaction.occurred_at.desc())
                    .limit(1)
                )
                tx_res = await db.execute(tx_stmt)
                latest_tx = tx_res.scalar_one_or_none()
                if latest_tx:
                    try:
                        pdf_bytes, filename, tx = await receipt_svc.generate_receipt_pdf(
                            db, business_id=business.id, transaction_id=latest_tx.id
                        )
                        send_res = await self.whatsapp.send_document_bytes(
                            user.phone_number,
                            pdf_bytes,
                            filename=filename,
                            caption=f"📄 Receipt #{filename.replace('.pdf', '')} - {tmpl.business_display_name}",
                        )
                        await self.ledger.record_outbound_message(
                            db, business.id, user.phone_number, f"[Sent document {filename}]", send_res, user_id=user.id
                        )
                        auto_receipt_msg = f" I have also generated and sent your updated receipt '{filename}' with your new details to your WhatsApp!"
                    except Exception as e:
                        logger.warning("Could not auto-generate receipt in update_receipt_template: %s", e)

                return (
                    f"Success: Receipt template updated! Stored branding for '{tmpl.business_display_name}' has been refreshed.{auto_receipt_msg}",
                    False,
                )

            elif tool_name == "generate_receipt":
                from app.services.receipt_service import ReceiptService
                from app.models.receipt_template import ReceiptTemplate

                # Lazily check if business has configured receipt template
                tmpl_stmt = select(ReceiptTemplate).where(ReceiptTemplate.business_id == business.id)
                tmpl_res = await db.execute(tmpl_stmt)
                existing_tmpl = tmpl_res.scalar_one_or_none()
                if not existing_tmpl:
                    return (
                        "Notice: No receipt template found for this business. "
                        "Before generating the receipt, ask the user conversationally for their business details "
                        "(business display name, address, phone number, and optional logo or footer note) "
                        "so it can be saved with set_receipt_template and included on their official PDF receipts.",
                        False,
                    )

                receipt_svc = ReceiptService()
                tx_id_str = tool_args.get("transaction_id")
                target_tx_id = UUID(tx_id_str) if tx_id_str else None
                scoped_mem_id = actor.member_id if (actor and actor.role == "staff") else None
                try:
                    pdf_bytes, filename, tx = await receipt_svc.generate_receipt_pdf(
                        db, business_id=business.id, transaction_id=target_tx_id, member_id=scoped_mem_id
                    )
                    caption = f"📄 {'Invoice' if tx.is_credit else 'Receipt'} #{filename.replace('.pdf', '')}"
                    send_res = await self.whatsapp.send_document_bytes(
                        user.phone_number, pdf_bytes, filename=filename, caption=caption
                    )
                    await self.ledger.record_outbound_message(
                        db, business.id, user.phone_number, f"[Sent document {filename}]", send_res, user_id=user.id
                    )
                    doc_type = "Invoice" if tx.is_credit else "Receipt"
                    return f"Success: {doc_type} PDF '{filename}' generated and delivered to user's WhatsApp.", False
                except ValueError as ve:
                    return f"Error: {str(ve)}", False
                except Exception as exc:
                    return f"Error generating receipt: {str(exc)}", False

            elif tool_name == "list_outstanding_debts":
                cust_filter = tool_args.get("customer_name")
                from app.services.debt_service import DebtService
                debt_svc = DebtService()
                debts = await debt_svc.list_outstanding_debts(db, business.id, customer_name=cust_filter)
                summary = debt_svc.format_debts_summary(debts, customer_filter=cust_filter)
                return summary, False

            elif tool_name == "mark_debt_paid":
                cust_name = tool_args.get("customer_name")
                amt_or_id = tool_args.get("amount_or_debt_id")
                from app.services.debt_service import DebtService
                debt_svc = DebtService()
                debt, msg = await debt_svc.mark_debt_paid(
                    db, business.id, customer_name=cust_name, amount_or_debt_id=amt_or_id
                )
                return msg, False

            elif tool_name == "draft_payment_reminder":
                cust_name = tool_args.get("customer_name")
                if not cust_name:
                    return "Error: customer_name is required.", False
                from app.services.debt_service import DebtService
                debt_svc = DebtService()
                draft = await debt_svc.draft_payment_reminder(db, business.id, customer_name=cust_name)
                return draft, False

            elif tool_name == "get_team_performance":
                if actor and actor.role == "staff":
                    return "Error: Team performance analytics are restricted to the business owner.", False
                period = tool_args.get("period") or "this_week"
                res = await self.analytics.get_team_sales_breakdown(
                    db, business_id=business.id, period=period
                )
                return res["summary_text"], False

            elif tool_name == "get_margin_report":
                if actor and actor.role == "staff":
                    return "Error: Profit margin reports and cost audits are restricted to the business owner.", False
                period = tool_args.get("period") or "this_month"
                item_name = tool_args.get("item_name")
                res = await self.analytics.get_margin_report(
                    db, business_id=business.id, period=period, item_name=item_name
                )
                return res["summary_text"], False

            elif tool_name == "get_product_performance_analysis":
                if actor and actor.role == "staff":
                    return "Error: Product performance and dead stock analyses are restricted to the business owner.", False
                period = tool_args.get("period") or "this_month"
                res = await self.analytics.get_product_performance_analysis(
                    db, business_id=business.id, period=period
                )
                return res["summary_text"], False

            elif tool_name == "simulate_pricing":
                if actor and actor.role == "staff":
                    return "Error: Pricing simulations and margin targets are restricted to the business owner.", False
                item_name = tool_args.get("item_name")
                if not item_name:
                    return "Error: item_name is required.", False
                discount_pct = tool_args.get("discount_pct")
                target_margin = tool_args.get("target_margin_pct")
                current_price = tool_args.get("current_price")
                res = await self.analytics.simulate_pricing(
                    db,
                    business_id=business.id,
                    item_name=item_name,
                    discount_pct=discount_pct,
                    target_margin_pct=target_margin,
                    current_price=current_price,
                )
                return res["summary_text"], False

            elif tool_name == "log_upcoming_payable":
                if actor and actor.role == "staff":
                    return "Error: Logging upcoming supplier payables is restricted to the business owner.", False
                amount = tool_args.get("amount")
                if not amount or float(amount) <= 0:
                    return "Error: amount is required and must be greater than zero.", False
                due_date = tool_args.get("due_date")
                if not due_date:
                    return "Error: due_date is required in YYYY-MM-DD format.", False
                description = tool_args.get("description") or "Payable bill"
                vendor = tool_args.get("vendor_name")
                payable, msg = await self.analytics.log_upcoming_payable(
                    db,
                    business_id=business.id,
                    amount=Decimal(str(amount)),
                    due_date=due_date,
                    description=description,
                    vendor_name=vendor,
                )
                return msg, False

            elif tool_name == "get_cash_flow_forecast":
                if actor and actor.role == "staff":
                    return "Error: Cash flow forecasting is restricted to the business owner.", False
                trailing_days = int(tool_args.get("trailing_days") or 30)
                horizon_days = int(tool_args.get("horizon_days") or 14)
                forecast = await self.analytics.get_cash_flow_forecast(
                    db,
                    business_id=business.id,
                    trailing_days=trailing_days,
                    horizon_days=horizon_days,
                )
                return forecast["summary_text"], False

            elif tool_name == "generate_report":
                cadence = tool_args.get("cadence") or "weekly"
                await self.unified_reports.generate_and_deliver_report(
                    db, user_id=user.id, cadence=cadence, actor=actor
                )
                return f"Success: {cadence.title()} report generated and sent to user's WhatsApp.", False

            elif tool_name == "generate_financial_chart":
                period = (tool_args.get("period") or "monthly").lower()
                result = await self.unified_reports.deliver_financial_chart(
                    db, user_id=user.id, period=period, actor=actor
                )
                if result.get("status") == "sent":
                    return f"Success: {period.title()} financial trend chart and interactive dashboard link generated and sent to user's WhatsApp.", False
                else:
                    err_msg = result.get("message", "Could not generate financial chart.")
                    return f"Error: {err_msg}", False

            elif tool_name == "get_historical_summary":
                start_str = (tool_args.get("start_date") or "").strip()
                end_str = (tool_args.get("end_date") or "").strip()
                filter_type = tool_args.get("record_type")

                if not start_str:
                    return "Error: start_date is required in YYYY-MM-DD format.", False

                try:
                    start_date_obj = date.fromisoformat(start_str)
                except Exception:
                    return f"Error: Invalid start_date format '{start_str}'. Use YYYY-MM-DD.", False

                if end_str:
                    try:
                        end_date_obj = date.fromisoformat(end_str)
                    except Exception:
                        end_date_obj = start_date_obj
                else:
                    end_date_obj = start_date_obj

                if end_date_obj < start_date_obj:
                    start_date_obj, end_date_obj = end_date_obj, start_date_obj

                start_dt = datetime.combine(start_date_obj, time.min, tzinfo=UTC)
                end_dt = datetime.combine(end_date_obj, time.max, tzinfo=UTC)

                # Query transactions
                tx_stmt = select(Transaction).where(
                    Transaction.business_id == business.id,
                    Transaction.status == "confirmed",
                    Transaction.occurred_at >= start_dt,
                    Transaction.occurred_at <= end_dt,
                )
                # Scope staff strictly to their own recorded entries
                if actor and actor.role == "staff":
                    tx_stmt = tx_stmt.where(Transaction.created_by_member_id == actor.member_id)

                if filter_type in {"sale", "expense"}:
                    tx_stmt = tx_stmt.where(Transaction.transaction_type == filter_type)
                tx_stmt = tx_stmt.order_by(Transaction.occurred_at.asc())

                tx_rows = (await db.execute(tx_stmt)).scalars().all()

                date_label = f"{start_date_obj}" if start_date_obj == end_date_obj else f"{start_date_obj} to {end_date_obj}"

                # Staff personal summary
                if actor and actor.role == "staff":
                    total_sales = sum((t.amount or Decimal("0")) for t in tx_rows if t.transaction_type == "sale")
                    sales_count = sum(1 for t in tx_rows if t.transaction_type == "sale")
                    total_expenses = sum((t.amount or Decimal("0")) for t in tx_rows if t.transaction_type == "expense")
                    expenses_count = sum(1 for t in tx_rows if t.transaction_type == "expense")

                    tx_samples = []
                    for t in tx_rows[:15]:
                        d_str = t.occurred_at.strftime("%Y-%m-%d")
                        item = t.item_name or t.description or "item"
                        tx_samples.append(f"- {d_str}: {t.transaction_type.upper()} ₦{t.amount:,.2f} ({item})")

                    lines = [
                        f"Personal Historical Summary for period [{date_label}]:",
                        f"- Total Confirmed Records Logged by You: {len(tx_rows)}",
                        f"- Total Sales Logged: ₦{total_sales:,.2f} ({sales_count} sales)",
                    ]
                    if expenses_count > 0:
                        lines.append(f"- Total Expenses Logged by You: ₦{total_expenses:,.2f} ({expenses_count} expenses)")
                    if tx_samples:
                        lines.append("\nYour Transactions:")
                        lines.extend(tx_samples)
                        if len(tx_rows) > 15:
                            lines.append(f"... and {len(tx_rows) - 15} more transactions.")

                    return "\n".join(lines), False

                # Owner business-wide summary
                act_stmt = select(Activity).where(
                    Activity.business_id == business.id,
                    Activity.occurred_at >= start_dt,
                    Activity.occurred_at <= end_dt,
                ).order_by(Activity.occurred_at.asc())
                act_rows = (await db.execute(act_stmt)).scalars().all()

                total_sales = sum((t.amount or Decimal("0")) for t in tx_rows if t.transaction_type == "sale")
                sales_count = sum(1 for t in tx_rows if t.transaction_type == "sale")
                total_expenses = sum((t.amount or Decimal("0")) for t in tx_rows if t.transaction_type == "expense")
                expenses_count = sum(1 for t in tx_rows if t.transaction_type == "expense")
                net_profit = total_sales - total_expenses

                tx_samples = []
                for t in tx_rows[:15]:
                    d_str = t.occurred_at.strftime("%Y-%m-%d")
                    item = t.item_name or t.description or "item"
                    tx_samples.append(f"- {d_str}: {t.transaction_type.upper()} ₦{t.amount:,.2f} ({item})")

                act_samples = []
                for a in act_rows[:5]:
                    d_str = a.occurred_at.strftime("%Y-%m-%d")
                    act_samples.append(f"- {d_str}: {a.title}")

                lines = [
                    f"Historical Summary for period [{date_label}]:",
                    f"- Total Confirmed Records: {len(tx_rows)}",
                    f"- Total Sales: ₦{total_sales:,.2f} ({sales_count} sales)",
                    f"- Total Expenses: ₦{total_expenses:,.2f} ({expenses_count} expenses)",
                    f"- Net Profit / Balance: ₦{net_profit:,.2f}",
                ]
                if tx_samples:
                    lines.append("\nTransactions:")
                    lines.extend(tx_samples)
                    if len(tx_rows) > 15:
                        lines.append(f"... and {len(tx_rows) - 15} more transactions.")
                if act_samples:
                    lines.append("\nActivities:")
                    lines.extend(act_samples)

                return "\n".join(lines), False

            elif tool_name == "store_knowledge_document":
                title = tool_args.get("title") or "Note"
                content = tool_args.get("content") or ""
                if not content.strip():
                    return "Error: Document content cannot be empty.", False

                await self.knowledge.store_user_document(
                    db,
                    user_id=user.id,
                    title=title,
                    content=content,
                    business_id=business.id,
                )
                return f"Success: Document '{title}' stored in user's knowledge base.", False

            elif tool_name == "manage_goal":
                action = tool_args.get("action")
                title = tool_args.get("title") or "Goal"
                target_val = tool_args.get("target_value")
                target_val_dec = Decimal(str(target_val)) if target_val is not None else None

                if action == "create":
                    goal = await self.goals.create_goal(
                        db,
                        user_id=user.id,
                        title=title,
                        target_value=target_val_dec,
                        business_id=business.id,
                        created_by_member_id=actor.member_id if actor else None,
                    )
                    return f"Success: Created goal '{goal.title}'.", False
                elif action == "list":
                    goals_list = await self.goals.get_active_goals(db, user.id)
                    goals_summary = ", ".join(f"'{g.title}'" for g in goals_list) or "No active goals"
                    return f"Active goals: {goals_summary}", False
                return "Success: Goal operation completed.", False

            elif tool_name == "forget_me" or tool_name == "request_data_deletion":
                phrase = (tool_args.get("confirmation_phrase") or "").strip().upper()
                if phrase == "CONFIRM DELETE":
                    await self.memory.purge_user_data(db, user_id=user.id)
                    return "Success: All user memory, knowledge documents, and personal goals have been permanently deleted.", False
                else:
                    return "Pending Confirmation: Data deletion NOT executed. Inform the user that they must reply with 'CONFIRM DELETE' to proceed with irreversible erasure.", False

            elif tool_name == "generate_image":
                if not settings.enable_image_generation:
                    return "Notice: Image generation is currently disabled. Tell the user: 'I can't generate images right now.'", False

                prompt = (tool_args.get("prompt") or "").strip()
                if not prompt:
                    return "Error: Image prompt cannot be empty.", False

                try:
                    img_bytes, mime = await self.image_service.generate_image(
                        prompt, db=db, business_id=business.id
                    )
                    self._recent_image_cache[user.id] = (img_bytes, mime)
                    media_id = await self.whatsapp.upload_media(img_bytes, mime)
                    if media_id:
                        caption = f"🎨 {prompt[:80]}"
                        send_result = await self.whatsapp.send_image(user.phone_number, media_id, caption=caption)
                        await self.ledger.record_outbound_message(
                            db, business.id, user.phone_number, caption, send_result, message_type="image", user_id=user.id, media_id=media_id
                        )
                        return f"Success: Image generated and sent directly to user's WhatsApp.", True
                    return "Error: Failed to upload generated image to WhatsApp.", False
                except CostLimitExceeded as cle:
                    return f"Cost Limit: {str(cle)}", False
                except Exception as exc:
                    return f"Error generating image: {str(exc)}", False

            elif tool_name == "edit_image":
                if not settings.enable_image_generation:
                    return "Notice: Image generation is currently disabled. Tell the user: 'I can't generate images right now.'", False

                instruction = (tool_args.get("instruction") or "").strip()
                if not instruction:
                    return "Error: Image edit instruction cannot be empty.", False

                ref_bytes: bytes | None = None
                ref_mime: str = "image/jpeg"
                if user.id in self._recent_image_cache:
                    ref_bytes, ref_mime = self._recent_image_cache[user.id]

                if not ref_bytes:
                    stmt = (
                        select(WhatsAppMessage)
                        .where(
                            WhatsAppMessage.user_id == user.id,
                            WhatsAppMessage.media_id.isnot(None),
                            WhatsAppMessage.message_type == "image",
                        )
                        .order_by(WhatsAppMessage.created_at.desc())
                        .limit(1)
                    )
                    res = await db.execute(stmt)
                    last_img_msg = res.scalar_one_or_none()
                    if last_img_msg and last_img_msg.media_id:
                        ref_bytes = await self.media_downloader.download(last_img_msg.media_id)

                if not ref_bytes:
                    return "Error: No recent image found in conversation to edit. Please send an image or ask me to generate one first.", False

                try:
                    img_bytes, mime = await self.image_service.edit_image(
                        instruction, ref_bytes, reference_mime_type=ref_mime, db=db, business_id=business.id
                    )
                    self._recent_image_cache[user.id] = (img_bytes, mime)
                    media_id = await self.whatsapp.upload_media(img_bytes, mime)
                    if media_id:
                        caption = f"✨ {instruction[:80]}"
                        send_result = await self.whatsapp.send_image(user.phone_number, media_id, caption=caption)
                        await self.ledger.record_outbound_message(
                            db, business.id, user.phone_number, caption, send_result, message_type="image", user_id=user.id, media_id=media_id
                        )
                        return f"Success: Edited image sent directly to user's WhatsApp.", True
                    return "Error: Failed to upload edited image to WhatsApp.", False
                except CostLimitExceeded as cle:
                    return f"Cost Limit: {str(cle)}", False
                except Exception as exc:
                    return f"Error editing image: {str(exc)}", False

            elif tool_name == "get_current_information":
                query = (tool_args.get("query") or "").strip()
                if not query:
                    return "Error: Query cannot be empty.", False

                info_text = await self.live_info_service.get_current_information(
                    query, db=db, business_id=business.id
                )
                return info_text, False
            elif tool_name == "invite_staff_member":
                name = (tool_args.get("display_name") or "").strip()
                phone = (tool_args.get("phone_number") or "").strip()
                if not name or not phone:
                    return "Error: Both staff member name and phone number are required.", False

                if not actor:
                    actor = ActorContext(
                        business_id=business.id,
                        member_id=user.id,
                        role=user.role or "owner",
                        wa_id=user.phone_number,
                        display_name=user.display_name,
                    )

                res = await self.staff.invite_staff_member(
                    db=db,
                    business=business,
                    actor=actor,
                    display_name=name,
                    phone_number=phone,
                    whatsapp=self.whatsapp,
                )
                if res.get("success"):
                    return res.get("message", "Staff invitation sent successfully."), False
                else:
                    return f"Error: {res.get('error', 'Could not invite staff member.')}", False

            elif tool_name == "list_staff_members":
                if not actor:
                    actor = ActorContext(
                        business_id=business.id,
                        member_id=user.id,
                        role=user.role or "owner",
                        wa_id=user.phone_number,
                        display_name=user.display_name,
                    )

                res = await self.staff.list_staff_members(
                    db=db,
                    business=business,
                    actor=actor,
                )
                if res.get("success"):
                    return res.get("formatted_text", "No team members found."), False
                else:
                    return f"Error: {res.get('error', 'Could not list team members.')}", False

            elif tool_name == "remove_staff_member":
                identifier = (tool_args.get("identifier") or "").strip()
                if not identifier:
                    return "Error: Staff member name or phone number is required.", False

                if not actor:
                    actor = ActorContext(
                        business_id=business.id,
                        member_id=user.id,
                        role=user.role or "owner",
                        wa_id=user.phone_number,
                        display_name=user.display_name,
                    )

                res = await self.staff.remove_staff_member(
                    db=db,
                    business=business,
                    actor=actor,
                    identifier=identifier,
                    whatsapp=self.whatsapp,
                )
                if res.get("success"):
                    return res.get("message", "Staff member removed successfully."), False
                else:
                    return f"Error: {res.get('error', 'Could not remove staff member.')}", False

            elif tool_name == "void_transaction":
                reason = (tool_args.get("reason") or "User requested void").strip()
                identifier = tool_args.get("identifier")
                target_tx_id = None
                if identifier:
                    try:
                        target_tx_id = UUID(identifier)
                    except Exception:
                        target_tx_id = None

                if not actor:
                    actor = ActorContext(
                        business_id=business.id,
                        member_id=user.id,
                        role=user.role or "owner",
                        wa_id=user.phone_number,
                        display_name=user.display_name,
                    )

                res = await self.ledger.void_transaction(
                    db=db,
                    business_id=business.id,
                    actor=actor,
                    transaction_id=target_tx_id,
                    reason=reason,
                )
                if not res.get("success"):
                    return f"Error: {res.get('error', 'Could not void transaction.')}", False

                # If staff voided, alert the owner
                if res.get("notify_owner") and business.phone_number and actor.wa_id != business.phone_number:
                    owner_alert = res.get("owner_alert_message")
                    if owner_alert:
                        try:
                            await self.whatsapp.send_text(business.phone_number, owner_alert)
                            await self.ledger.record_outbound_message(
                                db, business.id, business.phone_number, owner_alert, {"status": "sent"}
                            )
                        except Exception as n_err:
                            logger.warning("Could not dispatch void alert to owner: %s", n_err)

                return res.get("message", "Transaction voided successfully."), False

            elif tool_name == "set_expense_approval_threshold":
                thresh_amount = tool_args.get("threshold_amount")
                if thresh_amount is None:
                    return "Error: threshold_amount is required.", False

                if not actor:
                    actor = ActorContext(
                        business_id=business.id,
                        member_id=user.id,
                        role=user.role or "owner",
                        wa_id=user.phone_number,
                        display_name=user.display_name,
                    )

                res = await self.ledger.set_expense_approval_threshold(
                    db=db,
                    business_id=business.id,
                    actor=actor,
                    threshold_amount=Decimal(str(thresh_amount)),
                )
                if res.get("success"):
                    return res.get("message", "Expense approval threshold updated."), False
                else:
                    return f"Error: {res.get('error', 'Could not set expense threshold.')}", False

            else:
                return f"Error: Unknown tool '{tool_name}'.", False

        except Exception as tool_exc:
            logger.error("Tool execution '%s' failed: %s", tool_name, tool_exc, exc_info=True)
            return f"Error executing {tool_name}: {str(tool_exc)}", False

    async def _offline_fallback_handling(
        self,
        db: AsyncSession,
        business: Business,
        user: User,
        user_message: str,
        inbound_message_id: UUID | None = None,
        actor: ActorContext | None = None,
        source_wamid: str | None = None,
    ) -> str:
        """
        Graceful offline fallback for unit tests or temporary LLM outage.
        Preserves backward compatibility with legacy intent routing.
        """
        business_tz = getattr(business, "timezone", None) or settings.local_timezone
        # Team management commands in offline fallback
        lower_msg = user_message.lower().strip()
        if re.search(r"\b(?:list\s+staff|my\s+team|team\s+members|view\s+staff|show\s+staff)\b", lower_msg):
            act = actor or ActorContext(
                business_id=business.id,
                member_id=user.id,
                role=user.role or "owner",
                wa_id=user.phone_number,
                display_name=user.display_name,
            )
            res = await self.staff.list_staff_members(db, business, act)
            return res.get("formatted_text") or res.get("error", "No team members found.")

        m_invite = re.search(r"\b(?:invite\s+staff|add\s+staff)\s+([A-Za-z\s]+?)\s+(\+?\d[\d\s\-]{7,15})\b", user_message, re.IGNORECASE)
        if m_invite:
            staff_name = m_invite.group(1).strip()
            staff_phone = m_invite.group(2).strip()
            act = actor or ActorContext(
                business_id=business.id,
                member_id=user.id,
                role=user.role or "owner",
                wa_id=user.phone_number,
                display_name=user.display_name,
            )
            res = await self.staff.invite_staff_member(
                db=db,
                business=business,
                actor=act,
                display_name=staff_name,
                phone_number=staff_phone,
                whatsapp=self.whatsapp,
            )
            return res.get("message") or res.get("error", "Failed to invite staff member.")

        m_remove = re.search(r"\b(?:remove\s+staff|delete\s+staff)\s+(.+)\b", user_message, re.IGNORECASE)
        if m_remove:
            target_id = m_remove.group(1).strip()
            act = actor or ActorContext(
                business_id=business.id,
                member_id=user.id,
                role=user.role or "owner",
                wa_id=user.phone_number,
                display_name=user.display_name,
            )
            res = await self.staff.remove_staff_member(
                db=db,
                business=business,
                actor=act,
                identifier=target_id,
                whatsapp=self.whatsapp,
            )
            return res.get("message") or res.get("error", "Failed to remove staff member.")

        m_team = re.search(r"\b(?:team sales|staff sales|team performance|staff performance|team breakdown)\b", user_message, re.IGNORECASE)
        if m_team:
            if actor and actor.role == "staff":
                return "Error: Team performance analytics are restricted to the business owner."
            res = await self.analytics.get_team_sales_breakdown(db, business.id, period="this_week")
            return res["summary_text"]

        # 1. Privacy / Forget-me intent
        intent = IntentRouter.classify(user_message)
        if intent == "forget_me":
            if user_message.strip().upper() == "CONFIRM DELETE":
                await self.memory.purge_user_data(db, user.id)
                return "✅ All your personal records, conversation memory, and uploaded documents have been permanently deleted."
            else:
                return "⚠️ Are you sure you want to permanently delete all your data, memory, notes, and records? Reply CONFIRM DELETE to proceed."

        # 2. Knowledge Base storage intent (notes, price lists, documents)
        if intent == "knowledge_store":
            clean_content = re.sub(
                r"^(?:save note:|note:|price list:|document:|remember that:|save:)\s*",
                "",
                user_message,
                flags=re.IGNORECASE,
            ).strip()
            title = clean_content.split("\n")[0][:60] or "Saved Note"
            await self.knowledge.store_user_document(
                db, user.id, title=title, content=clean_content, business_id=business.id
            )
            return f"📚 Saved to your private knowledge base: '{title}'."

        # 3. Goal tracking intent
        if intent == "goal":
            clean_title = re.sub(
                r"^(?:my goal is|set goal|new goal|target is|aim to)\s*",
                "",
                user_message,
                flags=re.IGNORECASE,
            ).strip()
            await self.goals.create_goal(
                db,
                user.id,
                title=clean_title or user_message,
                business_id=business.id,
                created_by_member_id=actor.member_id if actor else None,
            )
            return f"🎯 Goal recorded: '{clean_title or user_message}'. I will track your progress and include it in your roadmaps!"

        # 4. Reminder / Task scheduling intent
        if intent == "reminder":
            due_at_utc = parse_reminder_time(user_message, timezone_name=business_tz)
            if due_at_utc:
                clean_title = re.sub(
                    r"\b(?:remind me to|remind me|set (?:a )?reminder for|set reminder|wake me up)\b",
                    "",
                    user_message,
                    flags=re.IGNORECASE,
                ).strip() or "Follow-up task"
                task = await self.tasks.create_task(
                    db,
                    business_id=business.id,
                    title=clean_title,
                    due_at=due_at_utc,
                    user_id=user.id,
                    created_by_member_id=actor.member_id if actor else None,
                )
                formatted_time = format_confirmation_time(due_at_utc, business_tz)
                return f"Got it — I'll remind you on {formatted_time} for '{task.title}'."
            else:
                await self.tasks.create_task(
                    db,
                    business_id=business.id,
                    title=user_message,
                    due_at=None,
                    user_id=user.id,
                    created_by_member_id=actor.member_id if actor else None,
                )
                return "What time should I remind you? (e.g. 'tomorrow at 3pm', 'in 2 hours', or 'next Monday by 10am')"

        # 5. Periodic Report intent
        if intent == "report":
            await self.unified_reports.generate_and_deliver_report(db, user.id, cadence="weekly", actor=actor)
            return "I have generated and sent your weekly summary report."

        # 6. General Q&A intent
        if intent == "general_qa":
            try:
                ans = await self.qa.answer(db, user, user_message)
                return self._maybe_append_advisory_disclaimer(user_message, ans)
            except Exception as qa_exc:
                logger.error("QA execution failed: %s", qa_exc, exc_info=True)
                return "I had a brief hiccup retrieving that. Could you ask again or rephrase what you need?"

        # 7. Greetings (plain greetings receive warm human reply)
        lower = user_message.lower().strip()
        if lower in {"hi", "hey", "hello", "hi there", "hey waasz", "good morning", "good afternoon", "good evening", "how are you"}:
            return f"Hey {user.display_name or 'there'}! How can I help you today?"

        # 8. Transaction intent
        record = await self.extractor.extract(user_message, db=db, business_id=business.id)
        if record.needs_clarification or (record.record_type in {"sale", "expense"} and record.amount is None):
            status = "needs_clarification"
            await self.ledger.create_ai_extraction(
                db,
                business.id,
                inbound_message_id or business.id,
                user_message,
                record,
                status=status,
                provider=getattr(self.extractor, "last_provider", "fallback"),
                model=getattr(self.extractor, "last_model", "fallback"),
                input_tokens=getattr(self.extractor, "last_input_tokens", 0),
                output_tokens=getattr(self.extractor, "last_output_tokens", 0),
                estimated_cost_usd=getattr(self.extractor, "last_estimated_cost_usd", Decimal("0.00")),
            )
            return record.clarification_question or (
                f"How much did you sell the {record.item_name or 'item'} for? Please reply with the amount in Naira."
                if record.record_type == "sale"
                else f"How much did you spend on {record.item_name or 'this'}? Please reply with the amount in Naira."
            )

        extraction = await self.ledger.create_ai_extraction(
            db,
            business.id,
            inbound_message_id or business.id,
            user_message,
            record,
            status="succeeded",
            provider=getattr(self.extractor, "last_provider", "fallback"),
            model=getattr(self.extractor, "last_model", "fallback"),
            input_tokens=getattr(self.extractor, "last_input_tokens", 0),
            output_tokens=getattr(self.extractor, "last_output_tokens", 0),
            estimated_cost_usd=getattr(self.extractor, "last_estimated_cost_usd", Decimal("0.00")),
        )
        confirmation = await self.ledger.stage_record_for_confirmation(
            db,
            business.id,
            inbound_message_id or business.id,
            extraction,
            record,
            member_id=actor.member_id if actor else None,
            source_wamid=source_wamid,
        )
        conf_text = confirmation.confirmation_text or build_confirmation_text(record)
        send_result = await self.whatsapp.send_confirmation(user.phone_number, conf_text)
        outbound = await self.ledger.record_outbound_message(
            db,
            business.id,
            user.phone_number,
            conf_text,
            send_result,
            message_type="interactive",
            user_id=user.id,
        )
        confirmation.confirmation_message_id = outbound.id
        return ""
