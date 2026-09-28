from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.core.database import Base
from app.models.enums import ConfirmationStatusEnum


class Confirmation(Base):
    __tablename__ = "confirmations"

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid4)
    business_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), ForeignKey("businesses.id"))
    ai_extraction_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("ai_extractions.id"), unique=True
    )
    source_message_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("whatsapp_messages.id"), nullable=True
    )
    confirmation_message_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("whatsapp_messages.id"), nullable=True
    )
    token: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    confirmation_text: Mapped[str] = mapped_column(Text)
    extracted_record: Mapped[dict] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(ConfirmationStatusEnum, default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    reply_payload: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    correction_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
