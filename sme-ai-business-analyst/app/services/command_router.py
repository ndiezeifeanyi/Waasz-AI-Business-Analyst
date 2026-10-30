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
from app.models.transaction import Transaction
from app.models.user import User
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

        # -------------------------------------------------------------------
        # 1. /menu -> Native WhatsApp Interactive List Drawer
        # -------------------------------------------------------------------
        if cmd_name in {"menu", "help", "cmd_menu"}:
            await self._send_quick_menu(db, business, user, phone)
            return True

        # -------------------------------------------------------------------
        # 2. /receipt -> Instant PDF Receipt
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
        # 6. /statement -> Download 2-Page Institutional Health Profile PDF
        # -------------------------------------------------------------------
        # -------------------------------------------------------------------
        # 6. /statement -> Download 2-Page Institutional Health Profile PDF
        # -------------------------------------------------------------------
        if cmd_name in {"statement", "stmt", "healthprofile", "bankprofile", "smedan", "downloadstatement", "cmd_statement"}:
            await self._handle_statement(db, business, user, phone)
            return True

        # -------------------------------------------------------------------
        # 7. /score -> Credit Underwriting Score & 30-Day Loan Streak
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
        if cmd_name in {"report", "daily", "todayreport"}:
            from app.services.unified_report_service import UnifiedReportService
            rep_svc = UnifiedReportService(whatsapp=self.whatsapp)
            res = await rep_svc.generate_and_dispatch_report(db, user_id=user.id, cadence="daily")
            msg = "📊 Dispatched your real-time daily business summary!" if res.get("status") == "sent" else "ℹ️ No activity recorded yet today to report."
            send_res = await self.whatsapp.send_text(phone, msg)
            await self.ledger.record_outbound_message(db, business.id, phone, msg, send_res, user_id=user.id)
            return True

        # -------------------------------------------------------------------
        # 15. /void or /delete -> Void Last Transaction
        # -------------------------------------------------------------------
        if cmd_name in {"void", "cancel", "delete", "deletelast", "undolast", "undo"}:
            await self._handle_void(db, business, user, phone)
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
                "title": "🏦 Statements & Score",
                "rows": [
                    {
                        "id": "cmd_statement",
                        "title": "🏛️ Bank Statement PDF",
                        "description": "Download 2-page institutional underwriting PDF",
                    },
                    {
                        "id": "cmd_score",
                        "title": "🏆 Loan Credit Score",
                        "description": "Check 300-850 score & 30-day streak",
                    },
                ],
            },
            {
                "title": "📊 Books & Stock",
                "rows": [
                    {
                        "id": "cmd_sales",
                        "title": "📈 Today's Sales",
                        "description": "Daily revenue, profit & cash collections",
                    },
                    {
                        "id": "cmd_debtors",
                        "title": "👥 Debtors Table",
                        "description": "Active customer debt balances",
                    },
                    {
                        "id": "cmd_stock",
                        "title": "📦 Stock Inventory",
                        "description": "Stock on hand & low-stock alerts",
                    },
                ],
            },
            {
                "title": "☁️ Cloud & Analytics",
                "rows": [
                    {
                        "id": "cmd_drive",
                        "title": "🔗 Google Drive Backup",
                        "description": "Automated cloud archive setup",
                    },
                    {
                        "id": "cmd_chart",
                        "title": "📊 Financial Chart",
                        "description": "Visual 7-day revenue trend chart",
                    },
                ],
            },
        ]

        body_text = (
            f"👋 *Welcome to {business.name or 'Waasz AI'} Quick Actions*\n\n"
            "Tap *'Select Action'* below for 1-tap navigation, or type any slash command:\n"
            "• `/receipt` • `/invoice` • `/iou` • `/statement`\n"
            "• `/score` • `/sales` • `/debtors` • `/stock` • `/drive`"
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
        except Exception as exc:
            logger.error("Failed to generate PDF document: %s", exc)
            err_msg = "⚠️ Could not generate PDF right now. Please record a confirmed sale first."
            send_res = await self.whatsapp.send_text(phone, err_msg)
            await self.ledger.record_outbound_message(db, business.id, phone, err_msg, send_res, user_id=user.id)

    async def _handle_iou(self, db: AsyncSession, business: Business, user: User, phone: str) -> None:
        """Pulls latest credit sale and provides official IOU ref and 1-tap link."""
        from app.services.debt_notifier_service import DebtNotifierService
        notifier = DebtNotifierService()

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
        ref_code = f"WZ-{str(tx.id).split('-')[0].upper()}"
        amt_fmt = format_naira(tx.amount)

        notice_text = (
            f"🤝 *Digital IOU Record (#{ref_code})*\n"
            f"• *Customer:* {cust_name}\n"
            f"• *Amount:* {amt_fmt}\n"
            f"• *Item:* {tx.item_name or 'Credit purchase'}\n"
            f"• *Status:* Outstanding\n\n"
        )
        if cust_phone:
            norm_phone = notifier.normalize_phone(cust_phone)
            short_msg = (
                f"🏛️ *WAASZ VERIFIED MERCHANT REGISTRY — Ref: #{ref_code}*\n\n"
                f"Dear {cust_name}, your credit purchase of {tx.item_name or 'goods'} ({amt_fmt}) from {business.name} has been registered.\n"
                f"Please settle to designated merchant account quoting Ref #{ref_code}."
            )
            import urllib.parse
            wa_link = f"https://wa.me/{norm_phone}?text={urllib.parse.quote(short_msg)}"
            notice_text += f"📲 *Forward to {cust_name} on WhatsApp:*\n👉 {wa_link}"
        else:
            notice_text += f"💡 *Tip:* Text *'{cust_name} phone is 070...' to generate an instant 1-tap WhatsApp link."

        send_res = await self.whatsapp.send_text(phone, notice_text)
        await self.ledger.record_outbound_message(db, business.id, phone, notice_text, send_res, user_id=user.id)

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
        """Compiles and delivers 2-page Financial Health Report PDF."""
        pdf_svc = BusinessHealthProfilePdfService()
        score_svc = CreditScoreService()
        try:
            pdf_bytes, filename = await pdf_svc.generate_health_profile_pdf(db, business.id)
            score_data = await score_svc.calculate_score(db, business.id)

            caption = f"Business Financial Health Report - {business.name}"
            send_res = await self.whatsapp.send_document_bytes(phone, pdf_bytes, filename=filename, caption=caption)
            await self.ledger.record_outbound_message(db, business.id, phone, f"[Sent document {filename}]", send_res, user_id=user.id)

            summary = (
                f"🏛️ *Financial Health Report Generated*\n\n"
                f"• Business: *{business.name}*\n"
                f"• Underwriting Score: *{score_data.get('score', 650)} / 850* ({score_data.get('tier', 'Developing')})\n"
                f"• 30-Day Revenue: ₦{score_data.get('sales_30d', 0):,.0f}\n"
                f"• Ledger Streak: {score_data.get('active_days_last_30', 0)} of 30 days active\n\n"
                "Your 2-page institutional underwriting PDF is attached above."
            )
            text_res = await self.whatsapp.send_text(phone, summary)
            await self.ledger.record_outbound_message(db, business.id, phone, summary, text_res, user_id=user.id)
        except Exception as exc:
            logger.error("Failed to generate statement: %s", exc)
            err_msg = "⚠️ Could not generate statement. Please ensure you have recorded confirmed sales or expenses first."
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
            f"• *30-Day Bank Loan Streak:* Day {streak} of 30 active\n"
        )
        if days_left > 0:
            msg += f"• ⏳ *{days_left} more days* of active daily bookkeeping to unlock your Certified Bank Loan Profile!\n\n"
        else:
            msg += "• 🎉 *Certified Bank Loan Profile UNLOCKED!* Text *'/statement'* to download your report.\n\n"

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
            stmt = stmt.where(InventoryItem.quantity_on_hand <= InventoryItem.reorder_threshold)
        stmt = stmt.order_by(InventoryItem.item_name.asc())

        res = await db.execute(stmt)
        items = res.scalars().all()

        if not items:
            msg = "ℹ️ No low-stock items detected! All tracked products are above reorder thresholds." if low_only else "ℹ️ No inventory items tracked yet. Text *'I have 50 bags of cement'* to start tracking."
            send_res = await self.whatsapp.send_text(phone, msg)
            await self.ledger.record_outbound_message(db, business.id, phone, msg, send_res, user_id=user.id)
            return

        header = "⚠️ *Low Stock Restock Alerts*" if low_only else f"📦 *Current Inventory Status ({len(items)} items)*"
        lines = [header, "---------------------------------------"]
        for it in items[:15]:
            status_dot = "🔴" if it.quantity_on_hand <= (it.reorder_threshold or 0) else "🟢"
            lines.append(f"{status_dot} *{it.item_name}:* {it.quantity_on_hand:g} {it.unit or 'units'} (Min: {it.reorder_threshold or 0:g})")

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

    async def _handle_void(self, db: AsyncSession, business: Business, user: User, phone: str) -> None:
        """Voids the latest confirmed transaction."""
        from app.services.ledger_service import LedgerService
        ledger = LedgerService()
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

        voided = await ledger.void_transaction(db, latest_tx.id, user.id, reason="Requested via /void shortcut")
        if voided:
            item_desc = latest_tx.item_name or "transaction"
            msg = f"🗑️ *Transaction Voided Successfully:*\nCancelled {latest_tx.transaction_type} of {item_desc} ({format_naira(latest_tx.amount)}). Deducted stock restored and linked debts cancelled."
        else:
            msg = "⚠️ Could not void transaction."
        send_res = await self.whatsapp.send_text(phone, msg)
        await self.ledger.record_outbound_message(db, business.id, phone, msg, send_res, user_id=user.id)
