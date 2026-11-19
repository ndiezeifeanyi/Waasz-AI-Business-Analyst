import logging
import re
import urllib.parse
from datetime import UTC, datetime, timedelta
from uuid import UUID

logger = logging.getLogger(__name__)

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.activity import Activity
from app.models.business import Business
from app.models.confirmation import Confirmation
from app.models.customer import Customer
from app.models.debt import Debt
from app.models.inventory import InventoryItem, InventoryMovement
from app.models.task import Task
from app.models.transaction import Transaction
from app.schemas.confirmation import ConfirmationDecision
from app.schemas.extraction import ExtractedRecord
from app.utils.currency import format_naira

CONFIRM_REPLY_IDS = {"1", "yes", "y", "confirm", "confirmed", "ok", "okay", "correct"}
EDIT_REPLY_IDS = {
    "2",
    "edit",
    "change",
    "no",
    "n",
    "wrong",
    "mistake",
    "incorrect",
    "modify",
    "not correct",
    "wait",
    "hold on",
}
CANCEL_REPLY_IDS = {"3", "cancel", "stop", "abort", "dismiss", "nevermind", "never mind", "ignore", "discard"}


def normalize_confirmation_reply(
    text: str | None = None, interactive_reply_id: str | None = None
) -> str:
    value = (interactive_reply_id or text or "").strip().lower()
    value = value.replace("reply_", "").replace("confirm_", "").replace("btn_", "")
    if value in CONFIRM_REPLY_IDS:
        return "confirm"
    if value in EDIT_REPLY_IDS:
        return "edit"
    if value in CANCEL_REPLY_IDS:
        return "cancel"
    return "unknown"


def build_confirmation_text(record: ExtractedRecord) -> str:
    if record.items and len(record.items) > 1:
        n_items = len(record.items)
        plural_type = "sales" if record.record_type == "sale" else "expenses" if record.record_type == "expense" else "items"
        lines = [f"Please confirm {n_items} item {plural_type}:"]
        if record.occurred_at_date:
            lines.append(f"📅 Date: {record.occurred_at_date}")
        if record.customer_name:
            lines.append(f"👤 Customer: {record.customer_name}")

        display_limit = 35
        for idx, it in enumerate(record.items[:display_limit], 1):
            qty_str = f"{it.quantity:g} " if it.quantity is not None else ""
            unit_str = f"{it.unit} " if it.unit else ""
            amt_str = format_naira(it.amount)
            prefix = f"{qty_str}{unit_str}".strip()
            item_desc = f"{it.item_name} ({prefix})" if prefix else it.item_name
            lines.append(f"{idx}. {item_desc} - {amt_str}")

        if n_items > display_limit:
            remaining = n_items - display_limit
            lines.append(f"... and {remaining} more items")

        total_amt = record.amount if record.amount is not None else sum(it.amount for it in record.items)
        lines.append(f"💰 Grand Total: {format_naira(total_amt)}")
        lines.append("Reply 1=Yes, 2=Edit, 3=Cancel.")
        return "\n".join(lines)

    quantity = f"{record.quantity:g} " if record.quantity is not None else ""
    unit = f"{record.unit} " if record.unit else ""
    item = record.item_name or record.description or "this record"
    amount = format_naira(record.amount)

    if record.record_type == "sale":
        cust_str = f" to {record.customer_name}" if record.customer_name else ""
        if record.is_credit:
            summary = (
                f"Credit sale of {quantity}{unit}{item}{cust_str} for {amount} (Debt recorded)".strip()
                if record.amount is not None
                else f"Credit sale of {quantity}{unit}{item}{cust_str} (Debt recorded)".strip()
            )
        else:
            summary = (
                f"Sold {quantity}{unit}{item}{cust_str} for {amount}".strip()
                if record.amount is not None
                else f"Sold {quantity}{unit}{item}{cust_str}".strip()
            )
    elif record.record_type == "expense":
        summary = (
            f"Spent {amount} on {quantity}{unit}{item}".strip()
            if record.amount is not None
            else f"Spent on {quantity}{unit}{item}".strip()
        )
    elif record.record_type == "inventory_update":
        summary = f"Added/updated {quantity}{unit}{item} in stock".strip()
    elif record.record_type == "task":
        summary = f"New task: {item}".strip()
        if record.due_at:
            summary += f" (Due: {record.due_at})"
    elif record.record_type == "activity":
        summary = f"Logged activity: {item or record.description}".strip()
    else:
        summary = "I no sure say I understand this record well"
        return (
            f"{summary}. Reply 1=Ignore, 2=Edit and type it like: "
            "Sold 5 bags rice for ₦250000."
        )

    return f"I understood: {summary}. Reply 1=Yes, 2=Edit."


def confirmation_success_text(record_type: str, is_credit: bool = False) -> str:
    if record_type == "inventory_update":
        return "Done. I have confirmed and updated your stock record."
    if record_type == "sale" and is_credit:
        return "Done. I have confirmed your credit sale and recorded the customer's debt."
    return "Done. I have confirmed and saved the record."


class ConfirmationService:
    async def handle_decision(self, decision: ConfirmationDecision) -> str:
        action = normalize_confirmation_reply(decision.decision)
        if action == "confirm":
            return "confirmed"
        if action == "edit":
            return "needs_edit"
        if action == "cancel":
            return "cancelled"
        return "pending"

    async def latest_actionable(
        self, db: AsyncSession, business_id: UUID, member_id: UUID | None = None
    ) -> Confirmation | None:
        stmt = (
            select(Confirmation)
            .where(
                Confirmation.business_id == business_id,
                Confirmation.status.in_(("pending", "needs_edit")),
                Confirmation.expires_at > datetime.now(UTC),
            )
        )
        if member_id is not None:
            stmt = stmt.where(Confirmation.member_id == member_id)
        stmt = stmt.order_by(Confirmation.created_at.desc()).limit(1)
        result = await db.execute(stmt)
        return result.scalar_one_or_none()

    async def confirm(self, db: AsyncSession, confirmation: Confirmation) -> str:
        confirmation.status = "confirmed"
        confirmation.confirmed_at = datetime.now(UTC)

        tx_result = await db.execute(
            select(Transaction).where(
                (Transaction.confirmation_id == confirmation.id)
                | (
                    (Transaction.ai_extraction_id == confirmation.ai_extraction_id)
                    & (Transaction.status == "pending_confirmation")
                )
            ).order_by(Transaction.created_at.asc())
        )
        transactions = tx_result.scalars().all()
        if transactions:
            low_stock_alerts: list[str] = []
            step_up_alerts: list[str] = []
            active_debt: Debt | None = None
            for transaction in transactions:
                transaction.status = "confirmed"
                transaction.confirmed_at = confirmation.confirmed_at
                if confirmation.member_id and not transaction.created_by_member_id:
                    transaction.created_by_member_id = confirmation.member_id
                if transaction.is_credit and transaction.customer_id:
                    debt_res = await db.execute(
                        select(Debt).where(Debt.transaction_id == transaction.id).limit(1)
                    )
                    existing_debt = debt_res.scalar_one_or_none()
                    if not existing_debt:
                        due_dt = None
                        if confirmation.extracted_record and confirmation.extracted_record.get("due_date"):
                            try:
                                due_raw = str(confirmation.extracted_record["due_date"]).replace("Z", "+00:00")
                                due_dt = datetime.fromisoformat(due_raw)
                                if due_dt.tzinfo is None:
                                    due_dt = due_dt.replace(tzinfo=UTC)
                            except Exception:
                                pass
                        debt = Debt(
                            business_id=transaction.business_id,
                            customer_id=transaction.customer_id,
                            created_by_member_id=confirmation.member_id,
                            transaction_id=transaction.id,
                            amount=transaction.amount or Decimal("0"),
                            currency=transaction.currency or "NGN",
                            status="outstanding",
                            due_date=due_dt,
                            notes=f"Credit sale for {transaction.item_name or 'goods'}",
                        )
                        db.add(debt)
                        await db.flush()
                        active_debt = debt
                    else:
                        active_debt = existing_debt

                if transaction.transaction_type == "sale" and transaction.item_name and transaction.quantity:
                    norm_item = transaction.item_name.strip().lower()
                    inv_res = await db.execute(
                        select(InventoryItem)
                        .where(
                            InventoryItem.business_id == transaction.business_id,
                            InventoryItem.normalized_item_name == norm_item,
                        )
                        .limit(1)
                    )
                    item = inv_res.scalar_one_or_none()
                    if item:
                        item.quantity_on_hand -= transaction.quantity
                        item.updated_at = datetime.now(UTC)
                        item.last_updated_from_confirmation_id = confirmation.id
                        threshold = item.low_stock_threshold
                        if threshold is not None and item.quantity_on_hand <= threshold:
                            unit_str = f" {item.unit}" if item.unit else ""
                            low_stock_alerts.append(
                                f"⚠️ Low Stock Alert: {item.item_name} is down to {item.quantity_on_hand:g}{unit_str} "
                                f"(reorder threshold: {threshold:g}{unit_str})."
                            )
                        await db.flush()

                # --- Cash Reconciliation & Alternative Credit Anomaly Hook ---
                try:
                    from app.services.risk.cash_reconciliation_service import CashReconciliationEngine
                    risk_engine = CashReconciliationEngine()
                    if transaction.transaction_type == "expense":
                        await risk_engine.record_restock_expense(db, transaction.business_id, transaction)
                    elif transaction.transaction_type == "sale":
                        assessment = await risk_engine.evaluate_cash_sale(db, transaction.business_id, transaction)
                        if assessment.risk_tier == "tier_1_step_up":
                            amt_str = format_naira(transaction.amount)
                            step_up_alerts.append(
                                f"\n⚠️ *Credit Limit Notice:*\n"
                                f"Your sale of {transaction.item_name or 'goods'} ({amt_str}) is significantly higher than your typical daily sales and is pending verification for your Credit Limit.\n"
                                f"📸 *Snap a photo of the waybill, invoice, or transfer receipt* to verify this sale."
                            )
                except Exception as risk_err:
                    logger.warning("Could not run cash risk evaluation for tx %s: %s", transaction.id, risk_err)

            if len(transactions) > 1:
                total_sum = sum((t.amount or Decimal("0")) for t in transactions)
                plural_type = "sales" if transactions[0].transaction_type == "sale" else "expenses"
                base_text = f"Done! I have confirmed and recorded all {len(transactions)} {plural_type} (Total: {format_naira(total_sum)})."
            elif transactions[0].is_credit and transactions[0].customer_id:
                cust = await db.get(Customer, transactions[0].customer_id)
                biz = await db.get(Business, transactions[0].business_id)
                cust_name = cust.name if cust else "Customer"
                cust_phone = cust.phone_number if cust else ""
                amt_fmt = format_naira(transactions[0].amount)
                item_desc = transactions[0].item_name or "goods"
                ref_code = f"WZ-{str(transactions[0].id).split('-')[0].upper()}"
                due_str = "Immediate / On Demand"
                if confirmation.extracted_record and confirmation.extracted_record.get("due_date"):
                    due_str = str(confirmation.extracted_record["due_date"])[:10]

                base_text = (
                    f"Done! Confirmed credit sale of {item_desc} ({amt_fmt}) to {cust_name}.\n\n"
                    f"🤝 *Digital IOU Generated (#{ref_code})*\n"
                    f"• *Customer:* {cust_name}" + (f" ({cust_phone})\n" if cust_phone else "\n") +
                    f"• *Amount:* {amt_fmt}\n"
                    f"• *Due Date:* {due_str}\n"
                )

                if active_debt:
                    from app.services.debt_recovery_service import DebtRecoveryService
                    recovery_svc = DebtRecoveryService()
                    await recovery_svc.record_debt_event(
                        db,
                        business_id=biz.id if biz else transactions[0].business_id,
                        debt_id=active_debt.id,
                        event_type="sale",
                        payload={
                            "transaction_id": str(transactions[0].id),
                            "amount": float(transactions[0].amount or 0),
                            "customer_name": cust_name,
                            "customer_phone": cust_phone,
                            "due_date": due_str,
                            "item_name": item_desc,
                            "ref_code": ref_code,
                        },
                    )

                if cust_phone and active_debt:
                    from app.utils.phone import validate_nigerian_phone
                    is_valid_phone, norm_e164, phone_err = validate_nigerian_phone(cust_phone)
                    short_link = f"https://waasz-sme-api.duckdns.org/i/{ref_code}"
                    manual_msg = (
                        f"Hello {cust_name}, here is your credit purchase record from {biz.name if biz else 'our shop'} "
                        f"for {item_desc} ({amt_fmt}) due on {due_str}.\nDetails: {short_link}"
                    )
                    clean_phone = re.sub(r"\D+", "", norm_e164 if is_valid_phone else cust_phone)
                    wa_link = f"https://wa.me/{clean_phone}?text={urllib.parse.quote(manual_msg)}"

                    if not is_valid_phone:
                        base_text += (
                            f"\n⚠️ *Debtor Phone Notice:* Customer phone '{cust_phone}' is not a valid Nigerian mobile number (+234...). "
                            f"Automated WhatsApp reminders require a valid mobile number.\n\n"
                            f"📲 *Manual Share Option:*\n👉 {wa_link}"
                        )
                    else:
                        if biz:
                            b_settings = dict(biz.settings or {})
                            now_iso = datetime.now(UTC).isoformat()
                            expires_iso = (datetime.now(UTC) + timedelta(hours=2)).isoformat()
                            consents_map = dict(b_settings.get("pending_counter_consents") or {})
                            consents_map[str(active_debt.id)] = {
                                "debt_id": str(active_debt.id),
                                "customer_name": cust_name,
                                "customer_phone": norm_e164,
                                "wa_link": wa_link,
                                "short_link": short_link,
                                "ref_code": ref_code,
                                "member_id": str(confirmation.member_id) if confirmation.member_id else None,
                                "staged_at": now_iso,
                                "expires_at": expires_iso,
                            }
                            b_settings["pending_counter_consents"] = consents_map
                            b_settings["pending_counter_consent"] = consents_map[str(active_debt.id)]
                            biz.settings = b_settings

                        base_text += (
                            f"\n❓ *{cust_name} is buying on credit. Does {cust_name} agree to receive the record and payment reminders on WhatsApp?*\n"
                            f"Reply *'1 {cust_name}'* (or *'YES {cust_name}'*) to confirm.\n"
                            f"Reply *'2 {cust_name}'* (or *'NO {cust_name}'*) to decline.\n\n"
                            f"📲 *Manual Share Option:*\n👉 {wa_link}"
                        )

                    # Send the merchant's credit invoice copy directly to the merchant
                    try:
                        from app.services.receipt_service import ReceiptService
                        receipt_svc = ReceiptService()
                        merchant_phone = biz.phone_number if biz else None
                        if merchant_phone:
                            pdf_bytes, inv_fname, _ = await receipt_svc.generate_receipt_pdf(
                                db, business_id=biz.id, transaction_id=transactions[0].id
                            )
                            from app.services.whatsapp_client import WhatsAppClient
                            wa_client = WhatsAppClient()
                            await wa_client.send_document_bytes(
                                merchant_phone,
                                pdf_bytes,
                                filename=inv_fname or f"Invoice_{ref_code}.pdf",
                                caption=f"📄 Credit Invoice #{ref_code} — {cust_name} ({amt_fmt})",
                            )
                    except Exception as e:
                        logger.warning("Could not auto-send merchant invoice copy: %s", e)
                else:
                    base_text += f"\n💡 *Tip:* Add {cust_name}'s phone number to record counter consent and enable automated WhatsApp reminders."
            else:
                base_text = confirmation_success_text(transactions[0].transaction_type, is_credit=transactions[0].is_credit)

            if low_stock_alerts:
                base_text += "\n\n" + "\n".join(low_stock_alerts)
            if step_up_alerts:
                base_text += "\n" + "\n".join(step_up_alerts)
            return base_text

        move_result = await db.execute(
            select(InventoryMovement)
            .where(InventoryMovement.confirmation_id == confirmation.id)
            .limit(1)
        )
        movement = move_result.scalar_one_or_none()
        if movement:
            movement.status = "confirmed"
            movement.confirmed_at = confirmation.confirmed_at
            await self._apply_inventory_movement(db, movement)
            return confirmation_success_text("inventory_update")

        task_result = await db.execute(
            select(Task).where(Task.id == confirmation.ai_extraction_id).limit(1)
        )
        task = task_result.scalar_one_or_none()
        if task:
            record = ExtractedRecord.model_validate(confirmation.extracted_record)
            if record.due_at:
                return f"Confirmed! I will remind you about '{task.title}' at {record.due_at}."
            return f"Confirmed! Task '{task.title}' saved. What time should I remind you?"

        return "Done. I have confirmed the record."

    async def request_edit(self, confirmation: Confirmation) -> str:
        confirmation.status = "needs_edit"
        confirmation.attempts += 1
        rec = confirmation.extracted_record or {}
        items = rec.get("items") or []
        if items and len(items) > 1:
            ex1 = items[0].get("item_name") or "Item 1"
            ex2 = items[1].get("item_name") or "Item 2" if len(items) > 1 else ex1
            amt_val = items[0].get("amount") or 5000
            p1 = f"{float(amt_val):,.0f}"
            return (
                f"No wahala. Type what needs to be changed (e.g. '{ex1} is ₦{p1}' or 'Change price of {ex2}'). "
                "I will update those items and keep the rest."
            )
        ex_name = rec.get("item_name") or "this item"
        ex_amt = f"{float(rec.get('amount') or 5000):,.0f}"
        return (
            f"No wahala. Type what needs to be changed (e.g. 'Change amount to ₦{ex_amt}' or 'Item is {ex_name}')."
        )

    async def cancel(self, db: AsyncSession, confirmation: Confirmation) -> str:
        confirmation.status = "rejected"
        confirmation.updated_at = datetime.now(UTC)
        tx_result = await db.execute(
            select(Transaction).where(
                (Transaction.confirmation_id == confirmation.id)
                | (
                    (Transaction.ai_extraction_id == confirmation.ai_extraction_id)
                    & (Transaction.status == "pending_confirmation")
                )
            )
        )
        for tx in tx_result.scalars().all():
            tx.status = "rejected"
        return "Record discarded. What else can I help you with?"

    async def _apply_inventory_movement(
        self, db: AsyncSession, movement: InventoryMovement
    ) -> None:
        normalized = movement.item_name.strip().lower()
        result = await db.execute(
            select(InventoryItem)
            .where(
                InventoryItem.business_id == movement.business_id,
                InventoryItem.normalized_item_name == normalized,
            )
            .limit(1)
        )
        item = result.scalar_one_or_none()
        if not item:
            item = InventoryItem(
                business_id=movement.business_id,
                item_name=movement.item_name,
                normalized_item_name=normalized,
                quantity_on_hand=0,
                unit=movement.unit,
                last_updated_from_confirmation_id=movement.confirmation_id,
            )
            db.add(item)
            await db.flush()

        if movement.movement_type == "stock_out":
            item.quantity_on_hand -= movement.quantity
        else:
            item.quantity_on_hand += movement.quantity
        item.unit = movement.unit or item.unit
        item.last_updated_from_confirmation_id = movement.confirmation_id
