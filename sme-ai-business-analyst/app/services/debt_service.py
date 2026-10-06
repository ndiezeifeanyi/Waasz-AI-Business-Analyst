from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.business import Business
from app.models.customer import Customer
from app.models.debt import Debt
from app.utils.currency import format_naira


class DebtService:
    """
    Manages customer credit tracking, debt status updates, and reminder drafting.
    STRICT GUARDRAIL: Never sends WhatsApp messages directly to customer phone numbers.
    All drafts are returned solely to the business owner.
    """

    async def list_outstanding_debts(
        self,
        db: AsyncSession,
        business_id: UUID,
        customer_name: str | None = None,
    ) -> list[dict]:
        """
        List all unpaid/outstanding debts for a business, optionally filtered by customer.
        Returns a list of dicts with customer details, amounts, and overdue status.
        """
        stmt = (
            select(Debt, Customer)
            .join(Customer, Debt.customer_id == Customer.id)
            .where(
                Debt.business_id == business_id,
                Debt.status == "outstanding",
            )
            .order_by(Debt.created_at.desc())
        )

        if customer_name and customer_name.strip():
            norm_name = customer_name.strip().lower()
            stmt = stmt.where(Customer.normalized_name.contains(norm_name))

        rows = (await db.execute(stmt)).all()
        now = datetime.now(UTC)

        results = []
        for debt, customer in rows:
            is_overdue = False
            days_overdue = 0
            if debt.due_date:
                due_dt = debt.due_date if debt.due_date.tzinfo else debt.due_date.replace(tzinfo=UTC)
                if now > due_dt:
                    is_overdue = True
                    days_overdue = (now - due_dt).days

            results.append({
                "debt_id": str(debt.id),
                "customer_name": customer.name,
                "customer_phone": customer.phone_number,
                "amount": debt.amount,
                "currency": debt.currency,
                "due_date": debt.due_date.strftime("%Y-%m-%d") if debt.due_date else None,
                "is_overdue": is_overdue,
                "days_overdue": days_overdue,
                "notes": debt.notes,
                "created_at": debt.created_at.strftime("%Y-%m-%d") if debt.created_at else None,
            })
        return results

    def format_debts_summary(self, debts: list[dict], customer_filter: str | None = None) -> str:
        """Format debts into a readable message for the business owner."""
        if not debts:
            if customer_filter:
                return f"No outstanding debts found for '{customer_filter}'."
            return "Good news! There are currently no outstanding customer debts on record."

        total_owed = sum((d["amount"] for d in debts), Decimal("0"))
        lines = [
            f"📋 *Outstanding Debts Summary* (Total: {format_naira(total_owed)} across {len(debts)} record{'s' if len(debts) != 1 else ''}):\n"
        ]

        for d in debts:
            due_str = f"Due: {d['due_date']}" if d['due_date'] else "No due date set"
            overdue_str = f" ⚠️ *{d['days_overdue']} days overdue*" if d["is_overdue"] else ""
            phone_str = f" ({d['customer_phone']})" if d.get("customer_phone") else ""
            notes_str = f" - _{d['notes']}_" if d.get("notes") else ""
            lines.append(
                f"• *{d['customer_name']}*{phone_str}: {format_naira(d['amount'])} ({due_str}{overdue_str}){notes_str}"
            )

        lines.append("\nTip: Ask me to draft a reminder (e.g. 'draft reminder for Emeka') or mark a debt as paid.")
        return "\n".join(lines)

    async def mark_debt_paid(
        self,
        db: AsyncSession,
        business_id: UUID,
        customer_name: str | None = None,
        amount_or_debt_id: str | None = None,
    ) -> tuple[Debt | None, str]:
        """
        Mark an outstanding debt as fully or partially paid.
        """
        debt = None
        now = datetime.now(UTC)

        # 1. Check if amount_or_debt_id is a UUID
        target_uuid = None
        target_amount = None
        if amount_or_debt_id:
            val_clean = amount_or_debt_id.strip()
            try:
                target_uuid = UUID(val_clean)
            except Exception:
                try:
                    target_amount = Decimal(val_clean.replace(",", "").replace("₦", "").replace("ngn", "").strip())
                except Exception:
                    target_amount = None

        if target_uuid:
            res = await db.execute(
                select(Debt).where(
                    Debt.id == target_uuid,
                    Debt.business_id == business_id,
                ).limit(1)
            )
            debt = res.scalar_one_or_none()

        # 2. If not found by UUID, find by customer_name
        if not debt and customer_name and customer_name.strip():
            norm_name = customer_name.strip().lower()
            res = await db.execute(
                select(Debt, Customer)
                .join(Customer, Debt.customer_id == Customer.id)
                .where(
                    Debt.business_id == business_id,
                    Debt.status == "outstanding",
                    Customer.normalized_name.contains(norm_name),
                )
                .order_by(Debt.created_at.asc())
                .limit(1)
            )
            row = res.first()
            if row:
                debt = row[0]

        if not debt:
            # Check latest outstanding debt if neither provided
            res = await db.execute(
                select(Debt)
                .where(Debt.business_id == business_id, Debt.status == "outstanding")
                .order_by(Debt.created_at.desc())
                .limit(1)
            )
            debt = res.scalar_one_or_none()

        if not debt:
            target = f"for '{customer_name}'" if customer_name else ""
            return None, f"No outstanding debt found {target} to mark as paid."

        cust = await db.get(Customer, debt.customer_id)
        cust_name = cust.name if cust else "the customer"

        # Handle partial vs full payment
        if target_amount and target_amount < debt.amount:
            remaining = debt.amount - target_amount
            debt.amount = remaining
            debt.notes = f"{debt.notes or ''} [Partial payment of {format_naira(target_amount)} on {now.strftime('%Y-%m-%d')}]".strip()
            await db.flush()
            return debt, f"Recorded partial payment of {format_naira(target_amount)} from {cust_name}. Remaining balance: {format_naira(remaining)}."

        debt.status = "paid"
        debt.paid_at = now
        await db.flush()
        return debt, f"Done! Marked debt of {format_naira(debt.amount)} from {cust_name} as paid in full."

    async def draft_payment_reminder(
        self,
        db: AsyncSession,
        business_id: UUID,
        customer_name: str,
    ) -> str:
        """
        Drafts a courteous, ready-to-forward WhatsApp payment reminder for the business owner.
        STRICT GUARDRAIL: Never sends anything to the customer.
        Returns message text to the OWNER.
        """
        if not customer_name or not customer_name.strip():
            return "Please provide the customer's name so I can draft their payment reminder."

        norm_name = customer_name.strip().lower()

        # Find customer
        cust_res = await db.execute(
            select(Customer)
            .where(
                Customer.business_id == business_id,
                Customer.normalized_name.contains(norm_name),
            )
            .limit(1)
        )
        customer = cust_res.scalar_one_or_none()
        if not customer:
            return f"I couldn't find any customer matching '{customer_name}' in your records."

        # Find customer's outstanding debts
        debts_res = await db.execute(
            select(Debt)
            .where(
                Debt.business_id == business_id,
                Debt.customer_id == customer.id,
                Debt.status == "outstanding",
            )
            .order_by(Debt.created_at.asc())
        )
        debts = debts_res.scalars().all()
        if not debts:
            return f"Great news! {customer.name} currently has no outstanding balance."

        biz = await db.get(Business, business_id)
        biz_name = biz.name if biz else "our business"

        total_owed = sum((d.amount for d in debts), Decimal("0"))
        due_dates = [d.due_date.strftime("%d %b %Y") for d in debts if d.due_date]
        due_str = f"which was due on {', '.join(due_dates)}" if due_dates else "which is currently outstanding"

        items = [d.notes for d in debts if d.notes]
        items_str = f" for {', '.join(items[:2])}" if items else ""

        draft = (
            f"Here is a draft reminder message for you to review and forward to *{customer.name}*:\n\n"
            f"---\n"
            f"Hello {customer.name},\n\n"
            f"Trust you're having a productive week. This is a gentle reminder regarding the outstanding balance of "
            f"*{format_naira(total_owed)}*{items_str}, {due_str}.\n\n"
            f"Kindly let us know once payment has been made or share the transfer confirmation. If you have any questions, feel free to reach out.\n\n"
            f"Thank you for your valued patronage!\n\n"
            f"Warm regards,\n"
            f"*{biz_name}*\n"
            f"---"
        )
        return draft
