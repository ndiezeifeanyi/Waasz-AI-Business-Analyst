from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.activity import Activity
from app.models.confirmation import Confirmation
from app.models.debt import Debt
from app.models.inventory import InventoryItem, InventoryMovement
from app.models.task import Task
from app.models.transaction import Transaction
from app.schemas.confirmation import ConfirmationDecision
from app.schemas.extraction import ExtractedRecord
from app.utils.currency import format_naira

CONFIRM_REPLY_IDS = {"1", "yes", "y", "confirm", "confirmed", "ok", "okay", "correct"}
EDIT_REPLY_IDS = {"2", "edit", "change", "no", "n"}
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
        self, db: AsyncSession, business_id: UUID
    ) -> Confirmation | None:
        result = await db.execute(
            select(Confirmation)
            .where(
                Confirmation.business_id == business_id,
                Confirmation.status.in_(("pending", "needs_edit")),
                Confirmation.expires_at > datetime.now(UTC),
            )
            .order_by(Confirmation.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def confirm(self, db: AsyncSession, confirmation: Confirmation) -> str:
        confirmation.status = "confirmed"
        confirmation.confirmed_at = datetime.now(UTC)

        tx_result = await db.execute(
            select(Transaction).where(Transaction.confirmation_id == confirmation.id).limit(1)
        )
        transaction = tx_result.scalar_one_or_none()
        if transaction:
            transaction.status = "confirmed"
            transaction.confirmed_at = confirmation.confirmed_at
            if transaction.is_credit and transaction.customer_id:
                debt_res = await db.execute(
                    select(Debt).where(Debt.transaction_id == transaction.id).limit(1)
                )
                if not debt_res.scalar_one_or_none():
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
                        transaction_id=transaction.id,
                        amount=transaction.amount or Decimal("0"),
                        currency=transaction.currency or "NGN",
                        status="outstanding",
                        due_date=due_dt,
                        notes=f"Credit sale for {transaction.item_name or 'goods'}",
                    )
                    db.add(debt)
                    await db.flush()
            return confirmation_success_text(transaction.transaction_type, is_credit=transaction.is_credit)

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
        return (
            "No wahala. Please type the corrected record in one message, e.g. "
            "Sold 5 bags rice for ₦250000."
        )

    async def cancel(self, db: AsyncSession, confirmation: Confirmation) -> str:
        confirmation.status = "cancelled"
        confirmation.updated_at = datetime.now(UTC)
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
