from datetime import date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import Date, DateTime, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.core.database import Base
from app.models.enums import ReportStatusEnum, ReportTypeEnum


class BusinessReport(Base):
    __tablename__ = "business_reports"

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid4)
    business_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), ForeignKey("businesses.id"))
    report_type: Mapped[str] = mapped_column(ReportTypeEnum)
    period_start: Mapped[date] = mapped_column(Date)
    period_end: Mapped[date] = mapped_column(Date)
    total_sales: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0"))
    total_expenses: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0"))
    estimated_profit: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0"))
    records_count: Mapped[int] = mapped_column(Integer, default=0)
    inventory_changes_count: Mapped[int] = mapped_column(Integer, default=0)
    body: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(ReportStatusEnum, default="generated")
    whatsapp_message_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("whatsapp_messages.id"), nullable=True
    )
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
