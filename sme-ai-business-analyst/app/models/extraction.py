from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Numeric, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.core.database import Base
from app.models.enums import BusinessRecordTypeEnum, ExtractionStatusEnum


class AiExtraction(Base):
    __tablename__ = "ai_extractions"

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid4)
    business_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("businesses.id"), nullable=True
    )
    whatsapp_message_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("whatsapp_messages.id"), nullable=True
    )
    media_asset_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("media_assets.id"), nullable=True
    )
    source_text: Mapped[str] = mapped_column(Text)
    prompt_name: Mapped[str] = mapped_column(String(80), default="extraction")
    prompt_version: Mapped[str] = mapped_column(String(40), default="1")
    provider: Mapped[str] = mapped_column(String(40), default="gemini")
    model: Mapped[str] = mapped_column(String(120))
    operation: Mapped[str] = mapped_column(String(40), default="extraction")
    raw_response: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    extracted_record: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    record_type: Mapped[str] = mapped_column(BusinessRecordTypeEnum, default="unknown")
    confidence: Mapped[Decimal] = mapped_column(Numeric(5, 4), default=Decimal("0"))
    needs_clarification: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(ExtractionStatusEnum, default="pending")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    estimated_cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6), default=Decimal("0"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
