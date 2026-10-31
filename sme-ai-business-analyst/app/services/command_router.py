"""
Waasz AI Command Router & Interactive Menu Service
--------------------------------------------------
Provides instant shortcut access (via WhatsApp slash commands and native interactive list drawers)
to all core features: receipts, invoices, statements, credit scores, IOUs, debtors, stock, and cloud backups.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
import logging
import re
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.business import Business
from app.models.customer import Customer
from app.models.debt import Debt
from app.models.inventory import InventoryItem
from app.models.member import Member
from app.models.transaction import Transaction
from app.models.user import User
from app.schemas.actor import ActorContext
from app.services.credit_score_service import CreditScoreService
from app.services.health_profile_pdf_service import BusinessHealthProfilePdfService
from app.services.ledger_service import LedgerService
from app.services.receipt_service import ReceiptService
from app.services.whatsapp_client import WhatsAppClient
from app.utils.currency import format_naira

logger = logging.getLogger(__name__)


class CommandRouter:
    """
    Routes slash commands and interactive quick menu selections directly to their
    action handlers, guaranteeing sub-second execution without LLM latency.
    """

    def __init__(self, whatsapp: WhatsAppClient | None = None, ledger: LedgerService | None = None) -> None:
        self.whatsapp = whatsapp or WhatsAppClient()
        self.ledger = ledger or LedgerService()

    async def handle_command(
        self,
        db: AsyncSession,
        business: Business,
        user: User,
        phone: str,
        source_text: str,
        interactive_id: str | None = None,
    ) -> bool:
        """
        Attempts to match and execute a slash command or interactive menu button.
        Returns True if a command was handled, False if it should fall through to normal processing.
        """
        raw_cmd = (interactive_id or source_text or "").strip().lower()

        # Normalize slash command triggers
        cmd_name = ""
        cmd_args = ""
        if raw_cmd.startswith("cmd_"):
            cmd_name = raw_cmd
        elif raw_cmd.startswith("/"):
            parts = raw_cmd[1:].split(maxsplit=1)
            cmd_name = parts[0].lower() if parts else ""
            cmd_args = parts[1].strip() if len(parts) > 1 else ""
        elif raw_cmd in {"menu", "help", "commands", "shortcuts", "quick actions"}:
            cmd_name = "menu"

        if not cmd_name:
            return False

        logger.info("CommandRouter triggered for business %s: %s (args=%s)", business.id, cmd_name, cmd_args)

        # If user provided transaction details after the command (e.g. "/receipt sold 2 bags 10k"),
        # fall through to LLM extraction so the new transaction is recorded and receipt/invoice offered!
        if cmd_args and cmd_name in {"receipt", "invoice", "iou"}:
            return False

        # -------------------------------------------------------------------
        # 1. /menu -> Native WhatsApp Interactive List Drawer
        # -------------------------------------------------------------------
        if cmd_name in {"menu", "help", "cmd_menu"}:
            await self._send_quick_menu(db, business, user, phone)
            return True

        # -------------------------------------------------------------------
        # 2. /receipt -> Instant Cash PDF Receipt
        # -------------------------------------------------------------------
        if cmd_name in {"receipt", "rcpt", "cmd_receipt"}:
            await self._handle_receipt(db, business, user, phone, is_credit_only=False)
            return True

        # -------------------------------------------------------------------
        # 3. /invoice -> Instant Credit Sale Invoice PDF
        # -------------------------------------------------------------------
        if cmd_name in {"invoice", "inv", "cmd_invoice"}:
            await self._handle_receipt(db, business, user, phone, is_credit_only=True)
            return True

        # -------------------------------------------------------------------
        # 4. /iou -> Latest Customer Credit IOU & 1-Tap Forward Link
        # -------------------------------------------------------------------
        if cmd_name in {"iou", "cmd_iou"}:
            await self._handle_iou(db, business, user, phone)
            return True

        # -------------------------------------------------------------------
        # 5. /debtors -> Active Debtors & Balances Table
        # -------------------------------------------------------------------
        if cmd_name in {"debtors", "debts", "whoowesme", "cmd_debtors"}:
            await self._handle_debtors(db, business, user, phone)
            return True

        # -------------------------------------------------------------------
        # -------------------------------------------------------------------
        # 6. /statement -> 30-Day Official Verified Business Statement PDF
        # -------------------------------------------------------------------
        if cmd_name in {"statement", "stmt", "downloadstatement", "accountstatement", "cmd_statement"}:
            await self._handle_statement(db, business, user, phone)
            return True

        # -------------------------------------------------------------------
        # 6b. /profile -> 2-Page Institutional Business Health Profile PDF
        # -------------------------------------------------------------------
        if cmd_name in {"profile", "healthprofile", "businessprofile", "cmd_profile"}:
            await self._handle_profile(db, business, user, phone)
            return True

        # -------------------------------------------------------------------
        # 7. /score -> Credit Underwriting Score & 30-Day Record Streak
        # -------------------------------------------------------------------
        if cmd_name in {"score", "creditscore", "streak", "cmd_score"}:
            await self._handle_credit_score(db, business, user, phone)
            return True

        # -------------------------------------------------------------------
        # 8. /sales -> Today's Sales, Margins & Cashflow
        # -------------------------------------------------------------------
        if cmd_name in {"sales", "revenue", "profit", "cmd_sales"}:
            await self._handle_sales_summary(db, business, user, phone)
            return True

        # -------------------------------------------------------------------
        # 9. /expenses -> Today's Operating Expenses
        # -------------------------------------------------------------------
        if cmd_name in {"expenses", "costs"}:
            await self._handle_expenses_summary(db, business, user, phone)
            return True

        # -------------------------------------------------------------------
        # 10. /stock -> Current Inventory & Valuation
        # -------------------------------------------------------------------
        if cmd_name in {"stock", "inventory", "cmd_stock"}:
            await self._handle_stock(db, business, user, phone, low_only=False)
            return True

        # -------------------------------------------------------------------
        # 11. /lowstock -> Reorder Level Alerts
        # -------------------------------------------------------------------
        if cmd_name in {"lowstock", "reorder"}:
            await self._handle_stock(db, business, user, phone, low_only=True)
            return True

        # -------------------------------------------------------------------
        # 12. /chart -> Visual Trend Chart & Dashboard
        # -------------------------------------------------------------------
        if cmd_name in {"chart", "graph", "cmd_chart"}:
            await self._handle_chart(db, business, user, phone)
            return True

        # -------------------------------------------------------------------
        # 13. /drive -> Google Drive Cloud Backup Setup
        # -------------------------------------------------------------------
        if cmd_name in {"drive", "connectdrive", "backup", "cmd_drive"}:
            await self._handle_drive_connect(db, business, user, phone)
            return True

        if cmd_name in {"disconnectdrive", "unlinkdrive"}:
            await self._handle_drive_disconnect(db, business, user, phone)
            return True

        # -------------------------------------------------------------------
        # 14. /report -> On-Demand Daily Summary Report
        # -------------------------------------------------------------------
        if cmd_name in {"report", "daily", "todayreport", "cmd_report"}:
            await self._handle_report(db, business, user, phone)
            return True

        # -------------------------------------------------------------------
        # 15. /void or /delete -> Void Last Confirmed Transaction
        # -------------------------------------------------------------------
        if cmd_name in {"void", "cancel", "delete", "deletelast", "undolast", "undo", "cmd_void"}:
            await self._handle_void(db, business, user, phone)
            return True

        # -------------------------------------------------------------------
        # 16. Staff Management: /staff, /addstaff, /removestaff
        # -------------------------------------------------------------------
        if cmd_name in {"staff", "team", "members", "cmd_staff"}:
            await self._handle_list_staff(db, business, user, phone)
            return True

        if cmd_name in {"addstaff", "invitestaff", "newstaff"}:
            await self._handle_add_staff(db, business, user, phone, cmd_args)
            return True

        if cmd_name in {"removestaff", "deletestaff"}:
            await self._handle_remove_staff(db, business, user, phone, cmd_args)
            return True

        return False

    # -----------------------------------------------------------------------
    # Action Implementations
    # -----------------------------------------------------------------------

    async def _send_quick_menu(self, db: AsyncSession, business: Business, user: User, phone: str) -> None:
        """Sends the native WhatsApp interactive list drawer (10 options)."""
        sections = [
            {
                "title": "🧾 Invoices & Receipts",
                "rows": [
                    {
                        "id": "cmd_receipt",
                        "title": "📄 Cash Receipt",
                        "description": "Download PDF for latest cash sale",
                    },
                    {
                        "id": "cmd_invoice",
                        "title": "📑 Credit Invoice",
                        "description": "Download credit sale invoice with terms",
                    },
                    {
                        "id": "cmd_iou",
                        "title": "🤝 Customer IOU Link",
                        "description": "1-tap WhatsApp debt forward link",
                    },
                ],
            },
            {
                "title": "📑 Reports & Statements",
                "rows": [
                    {
                        "id": "cmd_statement",
                        "title": "🏛️ Business Statement",
                        "description": "30-day verified official PDF ledger",
                    },
                    {
                        "id": "cmd_profile",
                        "title": "📑 Health Profile PDF",
                        "description": "2-page institutional health report",
                    },
                    {
                        "id": "cmd_report",
                        "title": "📊 Daily Summary",
                        "description": "Sales, profit & margin report",
                    },
                ],
            },
            {
                "title": "👥 Team & Ledger",
                "rows": [
                    {
                        "id": "cmd_staff",
                        "title": "👥 Staff & Team",
                        "description": "View team members & seat capacity",
                    },
                    {
                        "id": "cmd_debtors",
                        "title": "📋 Debtors Table",
                        "description": "Active customer debt balances",
                    },
                    {
                        "id": "cmd_void",
                        "title": "🗑️ Void Last Entry",
                        "description": "Undo/cancel last confirmed transaction",
                    },
                ],
            },
            {
                "title": "📦 Stock & Backup",
                "rows": [
                    {
                        "id": "cmd_stock",
                        "title": "📦 Stock Inventory",
                        "description": "Stock on hand & low-stock alerts",
                    },
                ],
            },
        ]

        body_text = (
            f"👋 *Welcome to {business.name or 'Waasz AI'} Quick Actions*\n\n"
            "Tap *'Select Action'* below for instant 1-tap navigation, or type any shortcut:\n"
            "• `/receipt` • `/invoice` • `/iou` • `/statement`\n"
            "• `/profile` • `/score` • `/report` • `/void`\n"
            "• `/staff` • `/addstaff [name] [phone]` • `/removestaff [phone]`\n"
            "• `/stock` • `/sales` • `/debtors` • `/drive`"
        )

        send_res = await self.whatsapp.send_interactive_list(
            to_phone=phone,
            body=body_text,
            button_label="Select Action",
            sections=sections,
            header="⚡ WAASZ SHORTCUT MENU",
            footer="Smart bookkeeping on WhatsApp",
        )
        await self.ledger.record_outbound_message(db, business.id, phone, "[Sent Quick Menu]", send_res, user_id=user.id)

    async def _handle_receipt(self, db: AsyncSession, business: Business, user: User, phone: str, is_credit_only: bool = False) -> None:
        """Generates and delivers PDF receipt or credit invoice."""
        receipt_svc = ReceiptService()
        try:
            # Look for appropriate latest transaction
            t_stmt = (
                select(Transaction)
                .where(
                    Transaction.business_id == business.id,
                    Transaction.transaction_type == "sale",
                    Transaction.status == "confirmed",
                )
            )
            if is_credit_only:
                t_stmt = t_stmt.where(Transaction.is_credit.is_(True))
            else:
                t_stmt = t_stmt.where(Transaction.is_credit.is_(False))
            t_stmt = t_stmt.order_by(Transaction.occurred_at.desc()).limit(1)

            t_res = await db.execute(t_stmt)
            target_tx = t_res.scalar_one_or_none()

            if not target_tx:
                if is_credit_only:
                    msg = "ℹ️ No confirmed credit sales found to generate an invoice for. (Record one by texting e.g. *'Emeka bought 2 bags on credit for ₦30k'*)."
                else:
                    msg = "ℹ️ No confirmed cash sales found to generate a receipt for. (Your latest sale may be a credit sale — use *'/invoice'* or select *Credit Invoice* to download it, or text *'Sold 1 item for ₦5,000 cash'*)."
                send_res = await self.whatsapp.send_text(phone, msg)
                await self.ledger.record_outbound_message(db, business.id, phone, msg, send_res, user_id=user.id)
                return

            pdf_bytes, filename, tx = await receipt_svc.generate_receipt_pdf(
                db, business_id=business.id, transaction_id=target_tx.id, credit_only=is_credit_only
            )
            doc_type = "Credit Invoice" if tx.is_credit else "Sales Receipt"
            caption = f"📄 {doc_type} #{filename.replace('.pdf', '')} - {business.name}"
            send_res = await self.whatsapp.send_document_bytes(phone, pdf_bytes, filename=filename, caption=caption)
            await self.ledger.record_outbound_message(db, business.id, phone, f"[Sent document {filename}]", send_res, user_id=user.id)

            followup = (
                f"💡 *Need to issue a receipt for a new sale right now?*\n"
                f"Just text or send a voice note: *'Sold [Item] for [Amount]'* (or *'/receipt [Item] [Amount]'*), and I'll generate and send your new receipt immediately!"
            )
            f_res = await self.whatsapp.send_text(phone, followup)
            await self.ledger.record_outbound_message(db, business.id, phone, followup, f_res, user_id=user.id)
        except Exception as exc:
            logger.error("Failed to generate PDF document: %s", exc)
            err_msg = "⚠️ Could not generate PDF right now. Please record a confirmed sale first."
            send_res = await self.whatsapp.send_text(phone, err_msg)
            await self.ledger.record_outbound_message(db, business.id, phone, err_msg, send_res, user_id=user.id)

    async def _handle_iou(self, db: AsyncSession, business: Business, user: User, phone: str) -> None:
        """Pulls latest credit sale, provides institutional Component 1 notification with 1-tap forward link, and delivers Component 2 A4 PDF Debt Receipt."""
        import urllib.parse
        from app.services.debt_notifier_service import DebtNotifierService
        from app.services.debt_receipt_pdf_service import DebtReceiptPdfService
        notifier = DebtNotifierService()
        debt_pdf_svc = DebtReceiptPdfService()

        # Find latest credit transaction
        stmt = (
            select(Transaction)
            .where(
                Transaction.business_id == business.id,
                Transaction.is_credit.is_(True),
                Transaction.status == "confirmed",
            )
            .order_by(Transaction.occurred_at.desc())
            .limit(1)
        )
        res = await db.execute(stmt)
        tx = res.scalar_one_or_none()
        if not tx or not tx.customer_id:
            msg = "ℹ️ You do not have any active credit sales recorded yet. Record one anytime by texting e.g. *'Emeka bought 2 bags on credit for ₦30k'*."
            send_res = await self.whatsapp.send_text(phone, msg)
            await self.ledger.record_outbound_message(db, business.id, phone, msg, send_res, user_id=user.id)
            return

        cust = await db.get(Customer, tx.customer_id)
        cust_name = cust.name if cust else "Customer"
        cust_phone = cust.phone_number if cust else ""
        ref_code = f"WZ-{str(tx.id).replace('-', '')[:8].upper()}"
        amt_fmt = format_naira(tx.amount)

        # Dynamic Net-14 terms calculation
        tx_dt = tx.occurred_at or datetime.now(UTC)
        maturity_dt = tx_dt + timedelta(days=14)
        due_date_str = f"{maturity_dt.strftime('%d %b %Y')} (Net-14)"

        # Component 1: Overhauled Mobile Notification (WhatsApp/SMS text for debtor)
        debtor_notice_text = (
            f"🏛️ WAASZ LEDGER REGISTRY // REF: #{ref_code}\n\n"
            f"ATTENTION: {cust_name.upper()}\n\n"
            f"This instrument confirms the formal registration of a commercial credit obligation "
            f"under the Waasz Enterprise Ledger Registry.\n\n"
            f"📊 *CORE OBLIGATION METRICS:*\n"
            f"• Creditor: {business.name}\n"
            f"• *Balance Due:* *{amt_fmt}*\n"
            f"• *Maturity / Due Date:* *{due_date_str}*\n"
            f"• Interest Structure: 0.0% APR (Fixed Default)\n"
            f"• Facility Type: Commercial Accounts Receivable\n\n"
            f"💳 *Online Payment Gateway Checkout:*\n"
            f"👉 https://pay.waasz.com/settle/{ref_code}\n\n"
            f"📄 *Download Official PDF Debt Receipt:*\n"
            f"👉 https://docs.waasz.com/receipts/iou/{ref_code}.pdf\n\n"
            f"🔒 *Security Note:* This ledger entry is cryptographically timestamped and immutable "
            f"under the Waasz Enterprise Registry."
        )

        merchant_summary = (
            f"🏛️ *OFFICIAL DEBT ACKNOWLEDGMENT (IOU) — #{ref_code}*\n\n"
            f"• *Debtor:* {cust_name}\n"
            f"• *Balance Due:* *{amt_fmt}*\n"
            f"• *Maturity Date:* *{due_date_str}*\n"
            f"• *Status:* Active Accounts Receivable\n\n"
        )

        if cust_phone:
            from app.utils.url_helper import get_short_iou_url
            short_link = get_short_iou_url(ref_code)
            merchant_summary += f"📲 *Forward to {cust_name} on WhatsApp (1-Tap):*\n👉 {short_link}\n\n"
        else:
            merchant_summary += f"💡 *Tip:* Text *'{cust_name} phone is 070...' to generate an instant 1-tap WhatsApp link.\n\n"

        merchant_summary += "📄 *Official A4 PDF Debt Receipt attached below:* 👇"

        send_res = await self.whatsapp.send_text(phone, merchant_summary)
        await self.ledger.record_outbound_message(db, business.id, phone, merchant_summary, send_res, user_id=user.id)

        # Generate & deliver Component 2: A4 PDF Debt Receipt
        try:
            pdf_bytes, filename = await debt_pdf_svc.generate_debt_receipt_pdf(
                db, business_id=business.id, transaction_id=tx.id
            )
            caption = f"📄 Official Debt Receipt (IOU) #{ref_code} - {cust_name}"
            doc_res = await self.whatsapp.send_document_bytes(phone, pdf_bytes, filename=filename, caption=caption)
            await self.ledger.record_outbound_message(db, business.id, phone, f"[Sent IOU PDF {filename}]", doc_res, user_id=user.id)
        except Exception as exc:
            logger.error("Failed to generate or deliver Debt Receipt PDF: %s", exc)

    async def _handle_debtors(self, db: AsyncSession, business: Business, user: User, phone: str) -> None:
        """Lists active debtors and balances."""
        debts_stmt = (
            select(Debt, Customer)
            .join(Customer, Debt.customer_id == Customer.id)
            .where(Debt.business_id == business.id, Debt.status.in_(["unpaid", "partially_paid"]))
            .order_by(Debt.created_at.desc())
        )
        res = await db.execute(debts_stmt)
        rows = res.all()
        if not rows:
            msg = "🎉 *Clear Ledger:* You have no outstanding debtors! All customer accounts are fully settled."
            send_res = await self.whatsapp.send_text(phone, msg)
            await self.ledger.record_outbound_message(db, business.id, phone, msg, send_res, user_id=user.id)
            return

        total_owed = sum(d.amount for d, _ in rows)
        lines = [
            f"📋 *Outstanding Customer Debtors ({len(rows)})*",
            f"💰 *Total Outstanding:* {format_naira(total_owed)}",
            "---------------------------------------",
        ]
        for d, c in rows[:15]:
            due_str = d.due_date.strftime("%d %b") if d.due_date else "On demand"
            lines.append(f"• *{c.name}:* {format_naira(d.amount)} (Due: {due_str})")

        lines.append("\n💡 Text *'Send debt notice to [Name]'* to dispatch an automated IOU reminder.")
        summary = "\n".join(lines)
        send_res = await self.whatsapp.send_text(phone, summary)
        await self.ledger.record_outbound_message(db, business.id, phone, summary, send_res, user_id=user.id)

    async def _handle_statement(self, db: AsyncSession, business: Business, user: User, phone: str) -> None:
        """Generates and delivers official 30-Day Verified Statement PDF and text ledger."""
        receipt_svc = ReceiptService()
        try:
            pdf_bytes, filename, summary = await receipt_svc.generate_statement_pdf(db, business.id, days=30)
            caption = f"📑 Official Statement of Business Activity - {business.name}"
            doc_res = await self.whatsapp.send_document_bytes(phone, pdf_bytes, filename=filename, caption=caption)
            await self.ledger.record_outbound_message(db, business.id, phone, f"[Sent statement PDF {filename}]", doc_res, user_id=user.id)

            # Follow up with concise text summary
            now = datetime.now(UTC)
            start_date = now - timedelta(days=30)
            summary_msg = (
                f"🏛️ *Official Business Statement Delivered*\n\n"
                f"• Business: *{business.name}*\n"
                f"• Period: {start_date.strftime('%d %b')} – {now.strftime('%d %b %Y')} (30 Days)\n"
                f"• Gross Sales: *{format_naira(Decimal(str(summary.get('total_sales', 0))))}* ({summary.get('sales_count', 0)} sales)\n"
                f"• Operating Expenses: *{format_naira(Decimal(str(summary.get('total_expenses', 0))))}* ({summary.get('expense_count', 0)} expenses)\n"
                f"• Net Profit: *{format_naira(Decimal(str(summary.get('net_profit', 0))))}*\n"
                f"• Total Ledger Entries: *{summary.get('total_transactions', 0)}*\n\n"
                "📄 Your official cryptographically verified Statement PDF is attached above."
            )
            text_res = await self.whatsapp.send_text(phone, summary_msg)
            await self.ledger.record_outbound_message(db, business.id, phone, summary_msg, text_res, user_id=user.id)
        except Exception as exc:
            logger.error("Failed to generate statement PDF: %s", exc)
            err_msg = "⚠️ Could not generate official statement PDF. Please ensure you have recorded confirmed sales or expenses first."
            send_res = await self.whatsapp.send_text(phone, err_msg)
            await self.ledger.record_outbound_message(db, business.id, phone, err_msg, send_res, user_id=user.id)

    async def _handle_profile(self, db: AsyncSession, business: Business, user: User, phone: str) -> None:
        """Compiles and delivers 2-page Institutional Financial Health Profile PDF."""
        pdf_svc = BusinessHealthProfilePdfService()
        score_svc = CreditScoreService()
        try:
            pdf_bytes, filename = await pdf_svc.generate_health_profile_pdf(db, business.id)
            score_data = await score_svc.calculate_score(db, business.id)

            caption = f"Business Financial Health Report - {business.name}"
            send_res = await self.whatsapp.send_document_bytes(phone, pdf_bytes, filename=filename, caption=caption)
            await self.ledger.record_outbound_message(db, business.id, phone, f"[Sent document {filename}]", send_res, user_id=user.id)

            summary = (
                f"📑 *Business Financial Health Profile Generated*\n\n"
                f"• Business: *{business.name}*\n"
                f"• Underwriting Score: *{score_data.get('score', 650)} / 850* ({score_data.get('tier', 'Developing')})\n"
                f"• 30-Day Revenue: ₦{score_data.get('sales_30d', 0):,.0f}\n"
                f"• Ledger Streak: {score_data.get('active_days_last_30', 0)} of 30 days active\n\n"
                "Your 2-page institutional health report PDF is attached above."
            )
            text_res = await self.whatsapp.send_text(phone, summary)
            await self.ledger.record_outbound_message(db, business.id, phone, summary, text_res, user_id=user.id)
        except Exception as exc:
            logger.error("Failed to generate profile: %s", exc)
            err_msg = "⚠️ Could not generate business profile. Please ensure you have recorded confirmed sales or expenses first."
            send_res = await self.whatsapp.send_text(phone, err_msg)
            await self.ledger.record_outbound_message(db, business.id, phone, err_msg, send_res, user_id=user.id)

    async def _handle_credit_score(self, db: AsyncSession, business: Business, user: User, phone: str) -> None:
        """Pulls verified credit score and 30-day streak count."""
        score_svc = CreditScoreService()
        score_data = await score_svc.calculate_score(db, business.id)

        score_val = score_data.get("score", 650)
        tier = score_data.get("tier", "Developing")
        streak = score_data.get("streak_days", 0)
        days_left = max(0, 30 - streak)
        tier_emoji = "🟢" if score_val >= 650 else ("🟡" if score_val >= 500 else "🔴")

        msg = (
            f"🏆 *Waasz Business Credit Score*\n\n"
            f"• *Score:* {score_val} / 850 [{tier} {tier_emoji}]\n"
            f"• *30-Day Record Streak:* Day {streak} of 30 active\n"
        )
        if days_left > 0:
            msg += f"• ⏳ *{days_left} more days* of active daily bookkeeping to complete your 30-day verified health profile!\n\n"
        else:
            msg += "• 🎉 *30-Day Verified Profile Complete!* Text *'/statement'* to download your report.\n\n"

        msg += f"💡 *Financial Metrics:* 30-Day Revenue: ₦{score_data.get('sales_30d', 0):,.0f} | Recovery Ratio: {score_data.get('debt_settlement_ratio', 100)}%"
        send_res = await self.whatsapp.send_text(phone, msg)
        await self.ledger.record_outbound_message(db, business.id, phone, msg, send_res, user_id=user.id)

    async def _handle_sales_summary(self, db: AsyncSession, business: Business, user: User, phone: str) -> None:
        """Calculates today's confirmed sales, profit and margin."""
        now = datetime.now(UTC)
        today_start = datetime(now.year, now.month, now.day, tzinfo=UTC)

        stmt = (
            select(Transaction)
            .where(
                Transaction.business_id == business.id,
                Transaction.transaction_type == "sale",
                Transaction.status == "confirmed",
                Transaction.occurred_at >= today_start,
            )
        )
        res = await db.execute(stmt)
        txs = res.scalars().all()

        total_rev = sum(t.amount for t in txs)
        total_credit = sum(t.amount for t in txs if t.is_credit)
        total_cash = total_rev - total_credit

        msg = (
            f"📊 *Today's Sales Breakdown ({now.strftime('%d %b %Y')})*\n\n"
            f"• *Total Sales:* {format_naira(total_rev)} ({len(txs)} transactions)\n"
            f"• *Cash / Transfer Collected:* {format_naira(total_cash)}\n"
            f"• *Credit Sales (Debts):* {format_naira(total_credit)}\n"
        )
        if txs:
            msg += "\n*Recent Items Today:*\n"
            for t in txs[:5]:
                msg += f"• {t.item_name or 'Item'}: {format_naira(t.amount)}" + (" _(Credit)_" if t.is_credit else "") + "\n"

        msg += "\n💡 Text *'/receipt'* for your latest PDF receipt or *'/statement'* for your full report."
        send_res = await self.whatsapp.send_text(phone, msg)
        await self.ledger.record_outbound_message(db, business.id, phone, msg, send_res, user_id=user.id)

    async def _handle_expenses_summary(self, db: AsyncSession, business: Business, user: User, phone: str) -> None:
        """Calculates today's confirmed operating expenses."""
        now = datetime.now(UTC)
        today_start = datetime(now.year, now.month, now.day, tzinfo=UTC)

        stmt = (
            select(Transaction)
            .where(
                Transaction.business_id == business.id,
                Transaction.transaction_type == "expense",
                Transaction.status == "confirmed",
                Transaction.occurred_at >= today_start,
            )
        )
        res = await db.execute(stmt)
        txs = res.scalars().all()

        total_exp = sum(t.amount for t in txs)
        msg = (
            f"💸 *Today's Operating Expenses ({now.strftime('%d %b %Y')})*\n\n"
            f"• *Total Expenses:* {format_naira(total_exp)} ({len(txs)} entries)\n"
        )
        if txs:
            msg += "\n*Expense Breakdown:*\n"
            for t in txs[:5]:
                msg += f"• {t.item_name or t.description or 'Expense'}: {format_naira(t.amount)}\n"

        send_res = await self.whatsapp.send_text(phone, msg)
        await self.ledger.record_outbound_message(db, business.id, phone, msg, send_res, user_id=user.id)

    async def _handle_stock(self, db: AsyncSession, business: Business, user: User, phone: str, low_only: bool = False) -> None:
        """Lists inventory on hand or low stock alerts."""
        stmt = select(InventoryItem).where(InventoryItem.business_id == business.id)
        if low_only:
            stmt = stmt.where(InventoryItem.quantity_on_hand <= func.coalesce(InventoryItem.low_stock_threshold, Decimal("5")))
        stmt = stmt.order_by(InventoryItem.item_name.asc())

        res = await db.execute(stmt)
        items = res.scalars().all()

        if not items:
            if low_only:
                any_stmt = select(func.count(InventoryItem.id)).where(InventoryItem.business_id == business.id)
                any_count = (await db.execute(any_stmt)).scalar() or 0
                if any_count == 0:
                    msg = "ℹ️ You haven't added any tracked inventory items yet.\nText e.g. *'I have 50 bags of cement, minimum 10'* to start tracking stock and getting low-stock alerts!"
                else:
                    msg = "🎉 *Healthy Stock:* No low-stock items detected! All your tracked products are safely above their reorder thresholds."
            else:
                msg = "ℹ️ No inventory items tracked yet. Text e.g. *'I have 50 bags of cement'* to start tracking stock."
            send_res = await self.whatsapp.send_text(phone, msg)
            await self.ledger.record_outbound_message(db, business.id, phone, msg, send_res, user_id=user.id)
            return

        header = "⚠️ *Low Stock Restock Alerts*" if low_only else f"📦 *Current Inventory Status ({len(items)} items)*"
        lines = [header, "---------------------------------------"]
        for it in items[:15]:
            min_thresh = it.low_stock_threshold or Decimal("5")
            status_dot = "🔴" if it.quantity_on_hand <= min_thresh else "🟢"
            lines.append(f"{status_dot} *{it.item_name}:* {it.quantity_on_hand:g} {it.unit or 'units'} (Min: {min_thresh:g})")

        lines.append("\n💡 Text *'Correct [Item] stock to [Qty]'* to reconcile physical count.")
        summary = "\n".join(lines)
        send_res = await self.whatsapp.send_text(phone, summary)
        await self.ledger.record_outbound_message(db, business.id, phone, summary, send_res, user_id=user.id)

    async def _handle_chart(self, db: AsyncSession, business: Business, user: User, phone: str) -> None:
        """Delivers financial chart image and web dashboard link."""
        from app.services.financial_chart_service import FinancialChartService
        chart_svc = FinancialChartService(whatsapp=self.whatsapp)
        try:
            res = await chart_svc.generate_and_send_financial_chart(db, business.id, period="monthly", to_phone=phone)
            caption = f"📊 Financial Performance Trend Dashboard - {business.name}"
            if res.get("image_bytes"):
                send_res = await self.whatsapp.send_image_bytes(phone, res["image_bytes"], caption=caption)
                await self.ledger.record_outbound_message(db, business.id, phone, "[Sent Chart Image]", send_res, user_id=user.id)
            if res.get("dashboard_url"):
                dash_msg = f"🌐 *Interactive Web Dashboard:*\nView full charts, filters, and analytics here:\n👉 {res['dashboard_url']}"
                send_res2 = await self.whatsapp.send_text(phone, dash_msg)
                await self.ledger.record_outbound_message(db, business.id, phone, dash_msg, send_res2, user_id=user.id)
        except Exception as exc:
            logger.error("Failed to generate financial chart: %s", exc)
            msg = "⚠️ Could not render financial chart right now. Please record at least 2 confirmed transactions first."
            send_res = await self.whatsapp.send_text(phone, msg)
            await self.ledger.record_outbound_message(db, business.id, phone, msg, send_res, user_id=user.id)

    async def _handle_drive_connect(self, db: AsyncSession, business: Business, user: User, phone: str) -> None:
        """Generates Google Drive OAuth authorization link."""
        from app.services.google_drive_service import GoogleDriveService
        drive_svc = GoogleDriveService()
        auth_url = drive_svc.generate_auth_url(business_id=business.id, user_id=user.id)
        msg = (
            "🔗 *Connect Google Drive for Automated Backups*\n\n"
            "Tap below to securely authorize Waasz to back up your monthly financial PDFs to your personal Drive:\n\n"
            f"👉 {auth_url}\n\n"
            "🔒 *Security:* Restricted strictly to `drive.file` scope — Waasz only accesses files it creates, never your private documents.\n"
            "Disconnect anytime by typing *'/disconnectdrive'*."
        )
        send_res = await self.whatsapp.send_text(phone, msg)
        await self.ledger.record_outbound_message(db, business.id, phone, msg, send_res, user_id=user.id)

    async def _handle_drive_disconnect(self, db: AsyncSession, business: Business, user: User, phone: str) -> None:
        """Disconnects Google Drive."""
        from app.services.google_drive_service import GoogleDriveService
        drive_svc = GoogleDriveService()
        disc = await drive_svc.disconnect(db, business_id=business.id)
        msg = "✅ Your Google Drive has been disconnected. Automated monthly cloud backups have been stopped." if disc else "ℹ️ No active Google Drive connection found."
        send_res = await self.whatsapp.send_text(phone, msg)
        await self.ledger.record_outbound_message(db, business.id, phone, msg, send_res, user_id=user.id)

    async def _handle_report(self, db: AsyncSession, business: Business, user: User, phone: str) -> None:
        """Generates and delivers on-demand daily business summary and metrics."""
        now = datetime.now(UTC)
        today_start = datetime(now.year, now.month, now.day, tzinfo=UTC)

        stmt = (
            select(Transaction)
            .where(
                Transaction.business_id == business.id,
                Transaction.status == "confirmed",
                Transaction.occurred_at >= today_start,
            )
            .order_by(Transaction.occurred_at.asc())
        )
        res = await db.execute(stmt)
        txs = res.scalars().all()

        sales = [t for t in txs if t.transaction_type == "sale"]
        expenses = [t for t in txs if t.transaction_type == "expense"]
        total_sales = sum(t.amount for t in sales)
        total_credit = sum(t.amount for t in sales if t.is_credit)
        total_cash = total_sales - total_credit
        total_exp = sum(t.amount for t in expenses)
        est_profit = total_sales - total_exp

        # Format report message
        report_lines = [
            f"📊 *Daily Business Summary — {business.name}*",
            f"📅 *Date:* {now.strftime('%d %b %Y')}",
            "---------------------------------------",
            f"📈 *Total Sales:* {format_naira(total_sales)} ({len(sales)} txns)",
            f"   • Cash / Transfer: {format_naira(total_cash)}",
            f"   • Credit Sales: {format_naira(total_credit)}",
            f"💸 *Total Expenses:* {format_naira(total_exp)} ({len(expenses)} txns)",
            f"💰 *Net Estimated Profit:* {format_naira(est_profit)}",
            "---------------------------------------",
        ]

        if not txs:
            report_lines.append("ℹ️ No transactions recorded yet today. Text e.g. *'sold 3 bags of cement 15k'* to record your first entry.")
        else:
            report_lines.append("*Items Recorded Today:*")
            for t in txs[:6]:
                sign = "➕" if t.transaction_type == "sale" else "➖"
                item_label = t.item_name or t.description or t.transaction_type.capitalize()
                credit_tag = " _(Credit)_" if t.is_credit else ""
                report_lines.append(f"{sign} {item_label}{credit_tag}: {format_naira(t.amount)}")

        # Append dashboard link if available
        try:
            from app.services.magic_link_service import MagicLinkService
            from app.utils.url_helper import get_base_origin
            magic_svc = MagicLinkService()
            token = await magic_svc.create_link(db, business.id)
            base_url = get_base_origin()
            dash_url = f"{base_url}/reports/dashboard/{token}"
            report_lines.append(f"\n🌐 *Live Business Dashboard:*\n👉 {dash_url}")
        except Exception as e_link:
            logger.warning("Could not append magic link: %s", e_link)

        report_lines.append("\n💡 Text *'/statement'* for your 30-day verified statement PDF or *'/chart'* for trend visuals.")
        msg = "\n".join(report_lines)
        send_res = await self.whatsapp.send_text(phone, msg)
        await self.ledger.record_outbound_message(db, business.id, phone, msg, send_res, user_id=user.id)

    async def _resolve_actor(self, db: AsyncSession, business: Business, user: User, phone: str) -> ActorContext:
        """Resolves ActorContext with valid member_id from members table."""
        stmt = (
            select(Member)
            .where(
                Member.business_id == business.id,
                Member.wa_id == phone,
                Member.status.in_(("active", "invited")),
            )
            .limit(1)
        )
        res = await db.execute(stmt)
        mem = res.scalar_one_or_none()
        return ActorContext(
            business_id=business.id,
            member_id=mem.id if mem else None,
            role=mem.role if mem else (user.role or "owner"),
            wa_id=phone,
            display_name=(mem.display_name if mem else None) or user.display_name or business.name,
        )

    async def _handle_void(self, db: AsyncSession, business: Business, user: User, phone: str) -> None:
        """Voids the latest confirmed transaction with proper ActorContext."""
        from app.services.ledger_service import LedgerService
        ledger = LedgerService()

        # Build proper ActorContext with actual Member ID
        actor = await self._resolve_actor(db, business, user, phone)

        stmt = (
            select(Transaction)
            .where(Transaction.business_id == business.id, Transaction.status == "confirmed")
            .order_by(Transaction.occurred_at.desc())
            .limit(1)
        )
        res = await db.execute(stmt)
        latest_tx = res.scalar_one_or_none()
        if not latest_tx:
            msg = "ℹ️ No recent confirmed transactions found to void."
            send_res = await self.whatsapp.send_text(phone, msg)
            await self.ledger.record_outbound_message(db, business.id, phone, msg, send_res, user_id=user.id)
            return

        res_void = await ledger.void_transaction(
            db,
            actor=actor,
            business=business,
            transaction_id=latest_tx.id,
            reason="Requested via /void shortcut",
        )
        if res_void.get("success"):
            item_desc = latest_tx.item_name or "transaction"
            msg = (
                f"🗑️ *Transaction Voided Successfully:*\n"
                f"Cancelled {latest_tx.transaction_type} of '{item_desc}' ({format_naira(latest_tx.amount)}).\n"
                f"• Deducted inventory has been restored.\n"
                f"• Associated customer debts cancelled."
            )
        else:
            err = res_void.get("error", "Could not void transaction.")
            msg = f"⚠️ {err}"
        send_res = await self.whatsapp.send_text(phone, msg)
        await self.ledger.record_outbound_message(db, business.id, phone, msg, send_res, user_id=user.id)

    async def _handle_list_staff(self, db: AsyncSession, business: Business, user: User, phone: str) -> None:
        """Lists team staff members and seat limits."""
        from app.services.staff_service import StaffService
        staff_svc = StaffService()
        actor = await self._resolve_actor(db, business, user, phone)
        res = await staff_svc.list_staff_members(db, business, actor)
        if res.get("success"):
            msg = res.get("formatted_text", "No staff members.")
            msg += "\n\n💡 *Staff Shortcuts:*\n• `/addstaff [name] [phone]` — Invite new staff\n• `/removestaff [phone or name]` — Remove staff"
        else:
            msg = f"⚠️ {res.get('error', 'Could not retrieve staff list.')}"
        send_res = await self.whatsapp.send_text(phone, msg)
        await self.ledger.record_outbound_message(db, business.id, phone, msg, send_res, user_id=user.id)

    async def _handle_add_staff(self, db: AsyncSession, business: Business, user: User, phone: str, cmd_args: str) -> None:
        """Invites a new staff member to the business."""
        from app.services.staff_service import StaffService
        from app.utils.phone import normalize_phone
        staff_svc = StaffService()
        actor = await self._resolve_actor(db, business, user, phone)

        args_parts = cmd_args.strip().split()
        if len(args_parts) < 2:
            msg = (
                "ℹ️ *How to add a staff member:*\n\n"
                "Type: `/addstaff [name] [phone]`\n"
                "Example: `/addstaff Chidi 08012345678`\n\n"
                "Waasz will send an instant WhatsApp invitation to your staff member to start logging entries."
            )
            send_res = await self.whatsapp.send_text(phone, msg)
            await self.ledger.record_outbound_message(db, business.id, phone, msg, send_res, user_id=user.id)
            return

        # Name is everything except the last token (which is the phone number)
        phone_num = args_parts[-1]
        display_name = " ".join(args_parts[:-1])

        res = await staff_svc.invite_staff_member(
            db=db,
            business=business,
            actor=actor,
            display_name=display_name,
            phone_number=phone_num,
            whatsapp=self.whatsapp,
        )
        if res.get("success"):
            msg = (
                f"✅ *Staff Invitation Sent!*\n\n"
                f"• Name: *{display_name}*\n"
                f"• Phone: *{res.get('phone_number', phone_num)}*\n"
                f"• Status: Invited ⏳\n\n"
                f"Your staff member has received a WhatsApp invite. Once they reply 'ACCEPT', they can log sales & expenses for {business.name}."
            )
        else:
            msg = f"⚠️ {res.get('error', 'Could not invite staff member.')}"
        send_res = await self.whatsapp.send_text(phone, msg)
        await self.ledger.record_outbound_message(db, business.id, phone, msg, send_res, user_id=user.id)

    async def _handle_remove_staff(self, db: AsyncSession, business: Business, user: User, phone: str, cmd_args: str) -> None:
        """Removes a staff member from the business."""
        from app.services.staff_service import StaffService
        staff_svc = StaffService()
        actor = await self._resolve_actor(db, business, user, phone)

        identifier = cmd_args.strip()
        if not identifier:
            msg = (
                "ℹ️ *How to remove a staff member:*\n\n"
                "Type: `/removestaff [phone or name]`\n"
                "Example: `/removestaff 08012345678` or `/removestaff Chidi`\n\n"
                "To see all current team members and phone numbers, text *'/staff'*."
            )
            send_res = await self.whatsapp.send_text(phone, msg)
            await self.ledger.record_outbound_message(db, business.id, phone, msg, send_res, user_id=user.id)
            return

        res = await staff_svc.remove_staff_member(
            db=db,
            business=business,
            actor=actor,
            identifier=identifier,
            whatsapp=self.whatsapp,
        )
        if res.get("success"):
            msg = f"✅ *Staff Member Removed:*\n{res.get('display_name', identifier)} ({res.get('phone_number', '')}) has been removed from {business.name}."
        else:
            msg = f"⚠️ {res.get('error', 'Could not remove staff member.')}"
        send_res = await self.whatsapp.send_text(phone, msg)
        await self.ledger.record_outbound_message(db, business.id, phone, msg, send_res, user_id=user.id)
