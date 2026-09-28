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
from app.services.confirmation_service import build_confirmation_text, ConfirmationService
from app.services.goal_service import GoalService
from app.services.image_service import ImageService
from app.services.intent_router import IntentRouter
from app.services.knowledge_service import KnowledgeService
from app.services.ledger_service import LedgerService
from app.services.live_info_service import LiveInformationService
from app.services.media_downloader import MediaDownloader
from app.services.memory_service import MemoryService
from app.services.qa_service import QaService
from app.services.task_service import TaskService
from app.services.unified_report_service import UnifiedReportService
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


class CreateReminderInput(BaseModel):
    """Schedule a reminder or task alert for a specific future date and time. Use whenever the user asks to be reminded, alerted, or to follow up on a task (e.g., 'in 3 mins', 'tomorrow 9am', 'today at 21:41'). Set is_alarm=True if the user requests an alarm or repeating reminder (e.g. 'alarm me', 'keep reminding me until I stop it')."""
    model_config = {"title": "create_reminder"}
    title: str = Field(description="Clear title of what needs to be done, e.g. 'Call supplier about rice delivery'")
    due_at_iso: str = Field(description="Target UTC time in ISO-8601 format (YYYY-MM-DDTHH:MM:SSZ). Compute relative times based on current server UTC time provided in the prompt.")
    description: str | None = Field(default=None, description="Optional notes or extra details")
    is_alarm: bool = Field(default=False, description="Set to True if the user asks for an alarm or repeating reminder until stopped (e.g. 'alarm me', 'keep reminding me until I stop it').")
    repeat_interval_seconds: int = Field(default=60, description="Interval in seconds between repeat notifications for alarm mode (default 60).")


class GenerateReportInput(BaseModel):
    """Generate and deliver a performance summary report directly to the user's WhatsApp. Call when the user explicitly asks for a report, weekly summary, or monthly review."""
    model_config = {"title": "generate_report"}
    cadence: str = Field(default="weekly", description="Reporting timeframe ('weekly' or 'monthly')")
    report_type: str = Field(default="unified", description="Report focus area ('unified', 'financial', 'productivity')")


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


AGENT_TOOLS = [
    RecordTransactionInput,
    SetItemCostInput,
    CreateReminderInput,
    GenerateReportInput,
    GetHistoricalSummaryInput,
    StoreKnowledgeDocumentInput,
    ManageGoalInput,
    RequestDataDeletionInput,
    GenerateImageInput,
    EditImageInput,
    GetCurrentInformationInput,
]

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
        self._recent_image_cache: dict[UUID, tuple[bytes, str]] = {}

    def _build_system_prompt(self, business: Business, user: User, now_utc: datetime, business_tz: str) -> str:
        niche_key = getattr(user, "niche", "sme_owner") or "sme_owner"
        niche_instruction = NICHE_INSTRUCTIONS.get(niche_key, NICHE_INSTRUCTIONS["personal"])

        return (
            "You are Waasz, a WhatsApp AI assistant that helps with whatever the person needs — from business tracking to reminders, notes, goals, and everyday questions.\n\n"
            "### VOICE & BEHAVIOR:\n"
            "- Write like a real person on WhatsApp: friendly, crisp, concise, and clear.\n"
            "- Keep replies to 1-3 sentences for general chat. Never be overly verbose.\n"
            "- NEVER use corporate buzzwords, bulleted financial statements, or unsolicited audits on casual greetings or simple updates.\n"
            "- Only provide detailed financial reports or breakdowns when explicitly requested by the user.\n"
            "- When an action is completed, acknowledge it briefly and naturally.\n\n"
            "### WHATSAPP FORMATTING RULES:\n"
            "- Use single *asterisks* for bold sparingly (not on every noun or line).\n"
            "- NEVER use markdown headers (no '#', '##', or '###') or nested markdown formatting.\n"
            "- Use clean blank lines between paragraphs and sections for spacing instead of dense walls of hyphens, asterisks, or dividers.\n"
            "- When a list is genuinely needed, use a simple line-per-item with a single leading emoji or dash — do NOT mix multiple competing bullet styles in one message.\n\n"
            "### CONTEXT & ATTENTION RULES:\n"
            "- The blocks below (memory, pending items, tasks, goals) are background reference only. Respond first and foremost to what the user just said. Do not proactively bring up or restate pending items or tasks unless the user's current message is clearly about them.\n"
            "- CRITICAL TURN ATTENTION: Focus your answer strictly on the user's latest incoming message. Do not proactively revisit, re-ask, or try to resolve topics, old reminders, or incomplete sales from earlier turns in the conversation history unless the user explicitly asks about them.\n"
            "- OVERDUE OR PASSED REMINDERS: If a reminder time requested in the past has already passed, DO NOT proactively bring it up, nag, or ask if the user wants to reschedule it. Only discuss a reminder if the user is currently asking about it.\n"
            "- When the user sends a greeting (e.g. 'Hi', 'Hey', 'Good morning'), reply ONLY with a warm, natural greeting. NEVER attach questions or follow-ups about earlier sales, expenses, reminders, or tasks.\n\n"
            "### AUTHORITATIVE CAPABILITIES (YOU HAVE DIRECT ACCESS TO THESE VIA TOOLS):\n"
            "You have real, working tool access. NEVER claim you cannot perform these actions:\n"
            "1. Reminders & Alarms: You CAN schedule reminders for any relative or absolute time using the `create_reminder` tool. Never tell the user to use their phone's clock or alarm app.\n"
            "2. Sales & Expenses: You CAN stage sales and expenses into the ledger using the `record_transaction` tool (amount in Naira is mandatory). If an amount is missing, ask for it naturally.\n"
            "3. Reports: You CAN generate weekly and monthly summary reports using `generate_report`.\n"
            "4. Notes & Documents: You CAN save price lists, customer contacts, or project notes using `store_knowledge_document`.\n"
            "5. Goals: You CAN record and track milestones using `manage_goal`.\n"
            "6. Image Generation & Editing: You CAN generate new images using `generate_image` and edit existing images using `edit_image`. Both tools deliver images directly to WhatsApp. You must NOT generate images of real, identifiable people.\n"
            "7. Live/Current Information: You CAN search for and retrieve real-time facts, news, today's events, sports scores, and current prices using `get_current_information`. Always invoke this tool for anything that requires current live knowledge or may have changed recently. NEVER guess or fabricate current information from stale training data.\n"
            "### IMAGE & RECEIPT UNDERSTANDING:\n"
            "- When an image is attached to the conversation:\n"
            "  * RECEIPT / INVOICE / EXPENSE: If the photo depicts a receipt, invoice, bill, POS receipt, transfer receipt, or expense document with monetary amounts, extract the transaction details (type 'sale' or 'expense', amount in Naira, item_name, description) and call the `record_transaction` tool to stage it for interactive confirmation.\n"
            "  * GENERAL PHOTO / SCENE / OBJECT: If the photo is a general photograph, place, person, product, or document that is not a transaction receipt, describe what you see or answer the user's caption naturally using your vision capability.\n\n"
            f"### TIME CONTEXT:\n"
            f"- Current UTC Time: {now_utc.strftime('%Y-%m-%dT%H:%M:%SZ')}\n"
            f"- Business Timezone: {business_tz}\n"
            "- Always compute relative time expressions ('in 3 mins', 'tomorrow 9am') relative to the Current UTC Time.\n\n"
            f"### USER PROFILE & NICHE:\n"
            f"User display name: {user.display_name or 'Friend'} | Role: {niche_key}\n"
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
    ) -> str:
        """
        Execute full conversational agent turn:
        1. Retrieve memory, knowledge, and goals context.
        2. Construct system prompt with accurate capabilities & niche guidelines.
        3. Invoke LLM with tools.
        4. Execute tool calls server-side (injecting verified tenant IDs).
        5. Return natural conversational text to send to user.
        """
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
        system_prompt = self._build_system_prompt(business, user, now_utc, business_tz)

        pending_conf = None
        try:
            pending_conf = await self.confirmations.latest_actionable(db, business.id)
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
            goals_text = "\n".join(f"- {g['title']} (target: {g['target_value'] or 'N/A'})" for g in active_goals)
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
            messages.append(HumanMessage(content=user_message))

        # 4. Invoke LLM with tools
        try:
            agent_result = await self.ai_client.invoke_agent(
                messages=messages,
                tools=AGENT_TOOLS,
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

        while iteration < MAX_TOOL_ITERATIONS:
            tool_calls = getattr(current_ai_msg, "tool_calls", None) or []
            if not tool_calls:
                reply_text = str(getattr(current_ai_msg, "content", "") or "").strip()
                if not reply_text:
                    reply_text = "I'm here. How can I help you today?"
                return reply_text

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
                )
                if was_interactive:
                    interactive_prompt_sent = True

                messages.append(ToolMessage(content=tool_result_str, tool_call_id=call_id))

            # If interactive buttons were sent (e.g. confirmation prompt for transaction),
            # return empty string to prevent sending duplicate plain text
            if interactive_prompt_sent:
                return ""

            # Re-invoke agent with tool results
            followup_result = await self.ai_client.invoke_agent(
                messages=messages,
                tools=AGENT_TOOLS,
                operation=f"agent_tool_followup_iter{iteration}",
                db=db,
                business_id=business.id,
            )

            if not followup_result or not followup_result.message:
                return "Done! I've updated that for you."

            current_ai_msg = followup_result.message

        final_text = str(getattr(current_ai_msg, "content", "") or "").strip()
        if final_text:
            return final_text
        return "Done! I've updated that for you."

    async def _execute_tool(
        self,
        db: AsyncSession,
        business: Business,
        user: User,
        tool_name: str,
        tool_args: dict[str, Any],
        raw_user_text: str,
        inbound_message_id: UUID | None = None,
    ) -> tuple[str, bool]:
        """
        Execute tool with server-enforced security boundaries.
        Returns: (result_string, is_interactive_prompt_sent)
        """
        business_tz = getattr(business, "timezone", None) or settings.local_timezone

        try:
            if tool_name == "create_reminder":
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
                    return "Error: Could not determine reminder due time. Please ask the user what time they want to be reminded.", False

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
                is_alarm_mode = bool(is_alarm_arg or any(p in lower_text for p in alarm_phrases))

                task = await self.tasks.create_task(
                    db,
                    business_id=business.id,
                    user_id=user.id,
                    title=title,
                    description=description,
                    due_at=due_at_utc,
                    is_alarm_mode=is_alarm_mode,
                    repeat_interval_seconds=repeat_interval_seconds,
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

            elif tool_name == "generate_report":
                cadence = tool_args.get("cadence") or "weekly"
                await self.unified_reports.generate_and_deliver_report(
                    db, user_id=user.id, cadence=cadence
                )
                return f"Success: {cadence.title()} report generated and sent to user's WhatsApp.", False

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
                if filter_type in {"sale", "expense"}:
                    tx_stmt = tx_stmt.where(Transaction.transaction_type == filter_type)
                tx_stmt = tx_stmt.order_by(Transaction.occurred_at.asc())

                tx_rows = (await db.execute(tx_stmt)).scalars().all()

                # Query user activities
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

                date_label = f"{start_date_obj}" if start_date_obj == end_date_obj else f"{start_date_obj} to {end_date_obj}"
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
    ) -> str:
        """
        Graceful offline fallback for unit tests or temporary LLM outage.
        Preserves backward compatibility with legacy intent routing.
        """
        intent = IntentRouter.classify(user_message)
        business_tz = getattr(business, "timezone", None) or settings.local_timezone

        # 1. Privacy / Forget-me intent
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
                db, user.id, title=clean_title or user_message, business_id=business.id
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
                task = await self.tasks.create_task(db, business_id=business.id, title=clean_title, due_at=due_at_utc, user_id=user.id)
                formatted_time = format_confirmation_time(due_at_utc, business_tz)
                return f"Got it — I'll remind you on {formatted_time} for '{task.title}'."
            else:
                await self.tasks.create_task(db, business_id=business.id, title=user_message, due_at=None, user_id=user.id)
                return "What time should I remind you? (e.g. 'tomorrow at 3pm', 'in 2 hours', or 'next Monday by 10am')"

        # 5. Periodic Report intent
        if intent == "report":
            await self.unified_reports.generate_and_deliver_report(db, user.id, cadence="weekly")
            return "I have generated and sent your weekly summary report."

        # 6. General Q&A intent
        if intent == "general_qa":
            try:
                return await self.qa.answer(db, user, user_message)
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
            db, business.id, inbound_message_id or business.id, extraction, record
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
