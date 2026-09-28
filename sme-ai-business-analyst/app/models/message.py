from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.core.database import Base
from app.models.enums import MessageDirectionEnum, MessageStatusEnum, WhatsAppMessageTypeEnum


class WhatsAppMessage(Base):
    __tablename__ = "whatsapp_messages"

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid4)
    business_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("businesses.id"), nullable=True
    )
    user_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    webhook_event_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("webhook_events.id"), nullable=True
    )
    direction: Mapped[str] = mapped_column(MessageDirectionEnum)
    whatsapp_message_id: Mapped[str | None] = mapped_column(String(160), nullable=True, index=True)
    whatsapp_conversation_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    from_phone: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    to_phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    message_type: Mapped[str] = mapped_column(WhatsAppMessageTypeEnum)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    media_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    interactive_reply_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_payload: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    status: Mapped[str] = mapped_column(MessageStatusEnum, default="received")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
