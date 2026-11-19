from datetime import UTC, datetime, timedelta
from decimal import Decimal
import logging
import re
import urllib.parse
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.business import Business
from app.models.customer import Customer
from app.models.debt import Debt
from app.models.message import WhatsAppMessage
from app.services.whatsapp_client import WhatsAppClient
from app.utils.currency import format_naira

logger = logging.getLogger(__name__)


class DebtNotifierService:
    """
    Automated Outbound Debt Enforcement & Registry Dispatcher.
    Reaches out directly to debtors on behalf of the merchant from the
    official Waasz Merchant Registry corporate channel (WhatsApp + SMS fallback).
    Enforces repayment directly to the merchant's bank account.
    """

    def __init__(self, whatsapp: WhatsAppClient | None = None) -> None:
        self.whatsapp = whatsapp or WhatsAppClient()

    def normalize_phone(self, phone: str | None) -> str:
        """Normalizes Nigerian phone numbers to E.164 (234...) format."""
        if not phone:
            return ""
        digits = re.sub(r"\D", "", phone)
        if digits.startswith("0") and len(digits) == 11:
            return "234" + digits[1:]
        if digits.startswith("234") and len(digits) == 13:
            return digits
        if len(digits) == 10:
            return "234" + digits
        return digits

    def _get_merchant_bank_info(self, biz: Business, payment_terms_note: str | None = None) -> str:
        """Extracts merchant's bank account details from business settings or receipt template."""
        settings_dict = biz.settings or {}
        bank_name = settings_dict.get("bank_name") or settings_dict.get("settlement_bank")
        account_number = settings_dict.get("account_number") or settings_dict.get("settlement_account")
        account_name = settings_dict.get("account_name") or biz.name

        if bank_name and account_number:
            return f"{bank_name} - {account_number} ({account_name})"
        
        if payment_terms_note and any(c.isdigit() for c in payment_terms_note):
            return payment_terms_note.strip()
        
        return f"Contact {biz.name} directly for designated settlement account"

    def build_notice_text(
        self,
        stage: int,
        debtor_name: str,
        biz_name: str,
        amount: Decimal,
        due_date_str: str,
        bank_info: str,
        ref_code: str,
    ) -> str:
        """
        Builds the institutional notice text for the debtor across the 4 escalation tiers.
        """
        amount_fmt = format_naira(amount)
        
        if stage == 1:
            # Stage 1: Component 1 Overhauled Mobile Notification (WhatsApp / SMS Text)
            return (
                f"🏛️ WAASZ LEDGER REGISTRY // REF: #{ref_code}\n\n"
                f"ATTENTION: {debtor_name.upper()}\n\n"
                f"This instrument confirms the formal registration of a commercial credit obligation "
                f"under the Waasz Enterprise Ledger Registry.\n\n"
                f"📊 *CORE OBLIGATION METRICS:*\n"
                f"• Creditor: {biz_name}\n"
                f"• *Balance Due:* *{amount_fmt}*\n"
                f"• *Maturity / Due Date:* *{due_date_str}*\n"
                f"• Interest Structure: 0.0% APR (Fixed Default)\n"
                f"💳 *Settlement Details:*\n"
                f"{bank_info}\n\n"
                f"📄 *View & Download Official Invoice (PDF):*\n"
                f"👉 https://waasz-sme-api.duckdns.org/i/{ref_code}/pdf\n\n"
                f"🔗 *Online Verification Card:*\n"
                f"👉 https://waasz-sme-api.duckdns.org/i/{ref_code}\n\n"
                f"🔒 *Security Note:* This ledger entry is cryptographically timestamped and immutable "
                f"under the Waasz Enterprise Registry."
            )

        elif stage == 2:
            # Stage 2: Due Date Settlement Alert
            return (
                f"⚠️ WAASZ SETTLEMENT NOTICE\n"
                f"Account Ref: #{ref_code}\n\n"
                f"Dear {debtor_name},\n\n"
                f"Your balance of {amount_fmt} with {biz_name} is due for settlement today.\n\n"
                f"💳 Remit to Merchant Account:\n{bank_info}\n\n"
                f"Kindly forward your transfer receipt to {biz_name} once completed to mark "
                f"your record as cleared."
            )

        elif stage == 3:
            # Stage 3: Reputational Leverage / Trade Network Flag Warning
            return (
                f"🚨 OVERDUE NOTICE — ACTION REQUIRED\n"
                f"Registry File: #{ref_code}\n\n"
                f"Dear {debtor_name},\n\n"
                f"Your credit balance of {amount_fmt} owed to {biz_name} is now PAST DUE.\n\n"
                f"Failure to settle within 24 hours will result in your contact details being "
                f"flagged across the Waasz Verified Merchant Network as a credit risk, "
                f"which may restrict future credit purchases with partner merchants.\n\n"
                f"💳 Settle Immediately To:\n{bank_info}\n"
                f"Quote #{ref_code} in payment narration."
            )

        else:
            # Stage 4: Formal Legalistic Demand & Credit Bureau Referral Warning
            return (
                f"⚖️ FORMAL DEMAND FOR SETTLEMENT\n"
                f"File Ref: #{ref_code}\n\n"
                f"ATTENTION: {debtor_name.upper()}\n\n"
                f"The outstanding sum of {amount_fmt} owed to {biz_name} remains unpaid despite prior notices.\n\n"
                f"Be advised that non-settlement constitutes commercial breach and is scheduled "
                f"for escalation to partner credit bureaus (CRC Credit Bureau / FirstCentral) for national risk reporting.\n\n"
                f"To prevent credit blacklisting, pay in full immediately:\n"
                f"💳 {bank_info}\n\n"
                f"Ignore this notice only if full payment has already been verified."
            )

    async def dispatch_debt_notice(
        self,
        db: AsyncSession,
        debt_id: UUID,
        stage: int = 1,
    ) -> dict:
        """
        Dispatches an automated debt notice to the debtor's phone number via WhatsApp.
        """
        debt = await db.get(Debt, debt_id)
        if not debt:
            return {"status": "error", "message": "Debt record not found"}

        customer = await db.get(Customer, debt.customer_id)
        if not customer:
            return {"status": "error", "message": "Customer record not found"}

        biz = await db.get(Business, debt.business_id)
        if not biz:
            return {"status": "error", "message": "Business record not found"}

        raw_phone = customer.phone_number
        debtor_phone = self.normalize_phone(raw_phone)
        if not debtor_phone:
            return {
                "status": "error",
                "message": f"Customer '{customer.name}' does not have a valid phone number on file.",
            }

        if debt.due_date:
            due_date_str = debt.due_date.strftime("%d %b %Y")
        else:
            effective_due = (debt.created_at or datetime.now(UTC)) + timedelta(days=14)
            due_date_str = f"{effective_due.strftime('%d %b %Y')} (Net-14)"
        # Check if ReceiptTemplate has bank info if biz.settings is missing it
        from app.models.receipt_template import ReceiptTemplate
        tmpl_stmt = select(ReceiptTemplate).where(ReceiptTemplate.business_id == biz.id).limit(1)
        tmpl_res = await db.execute(tmpl_stmt)
        tmpl_record = tmpl_res.scalar_one_or_none()
        payment_terms = tmpl_record.payment_terms_note if tmpl_record else None

        bank_info = self._get_merchant_bank_info(biz, payment_terms_note=payment_terms)
        ref_code = f"WZ-{str(debt.id).split('-')[0].upper()}"

        notice_body = self.build_notice_text(
            stage=stage,
            debtor_name=customer.name,
            biz_name=biz.name,
            amount=debt.amount,
            due_date_str=due_date_str,
            bank_info=bank_info,
            ref_code=ref_code,
        )

        # Dispatch via WhatsApp to the debtor:
        # First attempt via official Meta Utility Template with Document Header
        pdf_url = f"https://waasz-sme-api.duckdns.org/i/{ref_code}/pdf"
        body_params = [
            {"type": "text", "text": ref_code},
            {"type": "text", "text": customer.name},
            {"type": "text", "text": biz.name},
            {"type": "text", "text": format_naira(debt.amount)},
            {"type": "text", "text": due_date_str},
            {"type": "text", "text": bank_info},
        ]
        doc_header = {
            "type": "header",
            "parameters": [
                {
                    "type": "document",
                    "document": {
                        "link": pdf_url,
                        "filename": f"Invoice_IOU_{ref_code}.pdf",
                    },
                }
            ],
        }

        # Phase 5: Route through DebtorSendService with v2 utility templates and 6 strict sending gates
        from app.services.debtor_send_service import DebtorSendService
        from app.services.debt_recovery_service import DebtRecoveryService

        recovery_svc = DebtRecoveryService()
        send_svc = DebtorSendService(whatsapp=self.whatsapp)

        stage_map = {
            1: ("initial", "iou_record_v2"),
            2: ("due_today", "iou_due_today_v2"),
            3: ("overdue", "iou_overdue_v2"),
            4: ("final_notice", "iou_final_notice_v2"),
        }
        stage_name, tmpl_name = stage_map.get(stage, ("overdue", "iou_overdue_v2"))

        # Ensure merchant consent is recorded if merchant triggered dispatch
        has_consent = await recovery_svc.has_debtor_consent(db, biz.id, debt.id)
        if not has_consent:
            await recovery_svc.record_merchant_consent(
                db,
                business_id=biz.id,
                debt_id=debt.id,
                debtor_phone=debtor_phone,
                wording_version="manual_dispatch_v1",
            )

        ok, reason, msg_rec = await send_svc.send_debtor_notice(
            db,
            business_id=biz.id,
            debt_id=debt.id,
            stage=stage_name,
            template_name=tmpl_name,
        )

        send_success = ok
        err_msg = None if ok else reason
        wamid = msg_rec.whatsapp_message_id if msg_rec else None

        # Deliver Credit Sale Invoice PDF if available
        pdf_bytes = None
        invoice_filename = None
        invoice_sent = False
        if debt.transaction_id:
            try:
                from app.services.receipt_service import ReceiptService
                receipt_svc = ReceiptService()
                pdf_bytes, invoice_filename, _ = await receipt_svc.generate_receipt_pdf(
                    db, business_id=biz.id, transaction_id=debt.transaction_id
                )
                caption = (
                    f"📄 Commercial Invoice #{ref_code} — {biz.name}\n"
                    f"Amount Due: {format_naira(debt.amount)} | Due: {due_date_str}\n"
                    f"Bank: {bank_info}"
                )[:1024]
                doc_res = await self.whatsapp.send_document_bytes(
                    debtor_phone, pdf_bytes, filename=invoice_filename, caption=caption
                )
                if doc_res.success:
                    invoice_sent = True
            except Exception as e:
                logger.warning("Could not auto-generate or send invoice PDF for debtor: %s", e)

        # Record into whatsapp_messages table for full audit and visibility
        try:
            out_msg = WhatsAppMessage(
                business_id=biz.id,
                direction="outbound",
                to_phone=debtor_phone,
                message_type="template",
                body=notice_body,
                whatsapp_message_id=wamid,
                status="accepted" if send_success else "failed",
                error_message=err_msg,
                sent_at=datetime.now(UTC) if send_success else None,
            )
            db.add(out_msg)
        except Exception as e:
            logger.warning("Could not record outbound debt notice message: %s", e)

        # Log dispatch into debt notes
        now_str = datetime.now(UTC).strftime("%Y-%m-%d %H:%M")
        dispatch_note = f"[Notice Stage {stage} sent to {debtor_phone} on {now_str}]"
        debt.notes = f"{debt.notes or ''} {dispatch_note}".strip()
        await db.flush()

        logger.info(
            "Dispatched Stage %s debt notice for debt %s to debtor %s (success=%s)",
            stage,
            debt_id,
            debtor_phone,
            send_success,
        )

        from app.utils.url_helper import get_short_iou_url
        short_link = get_short_iou_url(ref_code)
        wa_link = f"https://wa.me/{debtor_phone}?text={urllib.parse.quote(notice_body)}"

        return {
            "status": "accepted" if send_success else "queued_or_failed",
            "debtor_name": customer.name,
            "debtor_phone": debtor_phone,
            "amount": debt.amount,
            "stage": stage,
            "ref_code": ref_code,
            "notice_text": notice_body,
            "wa_link": wa_link,
            "short_link": short_link,
            "success": send_success,
            "error": err_msg,
            "invoice_sent": invoice_sent,
            "invoice_filename": invoice_filename,
            "pdf_bytes": pdf_bytes,
        }

    async def trigger_notice_by_customer_name(
        self,
        db: AsyncSession,
        business_id: UUID,
        customer_name: str,
        stage: int = 1,
        phone_number: str | None = None,
    ) -> str:
        """
        Convenience handler called by AI agent or WebhookProcessor when the merchant says:
        'send notice to Emeka' or 'remind Emeka'.
        If phone_number is provided, it updates/links the customer's phone number automatically.
        """
        if not customer_name:
            return "Please provide the debtor's name to dispatch the notice."

        norm_name = customer_name.strip().lower()

        # Find customer
        cust_stmt = (
            select(Customer)
            .where(
                Customer.business_id == business_id,
                Customer.normalized_name.contains(norm_name),
            )
            .limit(1)
        )
        customer = (await db.execute(cust_stmt)).scalar_one_or_none()
        if not customer:
            # Check if customer exists by name equality or create customer on the fly if needed
            customer = Customer(
                business_id=business_id,
                name=customer_name.strip(),
                normalized_name=norm_name,
                phone_number=self.normalize_phone(phone_number) if phone_number else None,
            )
            db.add(customer)
            await db.flush()
        elif phone_number:
            customer.phone_number = self.normalize_phone(phone_number)
            await db.flush()

        # Find latest outstanding debt
        debt_stmt = (
            select(Debt)
            .where(
                Debt.business_id == business_id,
                Debt.customer_id == customer.id,
                Debt.status == "outstanding",
            )
            .order_by(Debt.created_at.desc())
            .limit(1)
        )
        debt = (await db.execute(debt_stmt)).scalar_one_or_none()
        if not debt:
            return f"Great news! {customer.name} currently has no outstanding debt on record."

        res = await self.dispatch_debt_notice(db, debt.id, stage=stage)

        if res.get("status") == "error":
            return f"⚠️ Could not dispatch notice: {res.get('message')}"

        stage_labels = {
            1: "Digital IOU Confirmation",
            2: "Due Date Settlement Alert",
            3: "Overdue Trade Risk Warning",
            4: "Formal Credit Bureau Demand",
        }
        label = stage_labels.get(stage, f"Stage {stage} Notice")

        if res.get("success"):
            inv_note = " and Commercial Invoice PDF have been dispatched" if res.get("invoice_sent") else " has been dispatched"
            return (
                f"✅ *Official {label} Delivered!*\n\n"
                f"• *Debtor:* {res['debtor_name']} ({res['debtor_phone']})\n"
                f"• *Amount:* {format_naira(res['amount'])}\n"
                f"• *Ref Code:* #{res['ref_code']}\n\n"
                f"🚀 Official IOU notice{inv_note} directly to {res['debtor_name']} on WhatsApp!\n\n"
                f"🔗 *Debtor Verification Link:* {res.get('short_link')}"
            )

        return (
            f"✅ *Official {label} Processed!*\n\n"
            f"• *Debtor:* {res['debtor_name']} ({res['debtor_phone']})\n"
            f"• *Amount:* {format_naira(res['amount'])}\n"
            f"• *Ref Code:* #{res['ref_code']}\n\n"
            f"📲 *1-Tap Direct WhatsApp Delivery:*\n"
            f"Tap below to send the official IOU straight to {res['debtor_name']} on WhatsApp:\n"
            f"👉 {res.get('short_link') or res['wa_link']}\n\n"
            f"📄 *Notice Content:*\n"
            f"{res['notice_text']}"
        )
