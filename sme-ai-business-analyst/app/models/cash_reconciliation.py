from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Numeric, String
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.core.database import Base


class CashRestockReconciliation(Base):
    """
    Ties cash expenses for inventory purchases to subsequent cash sales inflows.
    Ensures merchants cannot claim cash turnover for inventory they never purchased.
    """
    __tablename__ = "cash_restock_reconciliations"

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid4)
    business_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("businesses.id", ondelete="CASCADE"), index=True
    )

    # The Restock Expense (Outflow anchor)
    restock_expense_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("transactions.id", ondelete="SET NULL"), nullable=True
    )
    item_name: Mapped[str] = mapped_column(String(200))
    normalized_item_name: Mapped[str] = mapped_column(String(200), index=True)

    # Physical Quantities
    quantity_acquired: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    quantity_sold_cash: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0"))
    unit_cost: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    total_cost: Mapped[Decimal] = mapped_column(Numeric(14, 2))  # Quantity * Unit Cost

    # Realized Inflows
    total_cash_revenue_realized: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0"))
    realized_gross_margin: Mapped[Decimal | None] = mapped_column(Numeric(6, 3), nullable=True)

    # Reconciliation Lifecycle: 'active', 'fully_depleted', 'expired'
    status: Mapped[str] = mapped_column(String(30), default="active", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_sale_matched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
