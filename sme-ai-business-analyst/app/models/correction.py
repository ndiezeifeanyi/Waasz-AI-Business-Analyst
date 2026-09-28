from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.core.database import Base


class Correction(Base):
    __tablename__ = "corrections"

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid4)
    business_id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), ForeignKey("businesses.id"))
    confirmation_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("confirmations.id")
    )
    correction_message_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("whatsapp_messages.id"), nullable=True
    )
    correction_text: Mapped[str] = mapped_column(Text)
    corrected_record: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    status: Mapped[str] = mapped_column(String(32), default="corrected")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
