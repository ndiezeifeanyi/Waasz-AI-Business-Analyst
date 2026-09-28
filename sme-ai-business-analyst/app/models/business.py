from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, Integer, Numeric, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.core.database import Base
from app.models.enums import SubscriptionPlanEnum


class Business(Base):
    __tablename__ = "businesses"

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(Text)
    owner_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    phone_number: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    business_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    country: Mapped[str] = mapped_column(String(80), default="Nigeria")
    currency: Mapped[str] = mapped_column(String(8), default="NGN")
    timezone: Mapped[str] = mapped_column(String(80), default="Africa/Lagos")
    subscription_plan: Mapped[str] = mapped_column(SubscriptionPlanEnum, default="pilot")
    onboarding_status: Mapped[str] = mapped_column(String(80), default="active")
    daily_ai_spend_limit_usd: Mapped[Decimal] = mapped_column(Numeric(12, 4), default=Decimal("5"))
    data_retention_days: Mapped[int] = mapped_column(Integer, default=730)
    is_provisional: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    settings: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
