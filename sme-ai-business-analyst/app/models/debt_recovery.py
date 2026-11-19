from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.core.database import Base


class DebtorConsent(Base):
    __tablename__ = "debtor_consents"

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid4)
    business_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("businesses.id", ondelete="CASCADE"), index=True
    )
    debt_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("debts.id", ondelete="CASCADE"), index=True
    )
    debtor_phone: Mapped[str] = mapped_column(String(32), index=True)
    source: Mapped[str] = mapped_column(String(32), default="attested_by_merchant")  # attested_by_merchant | confirmed_by_debtor
    wording_version: Mapped[str] = mapped_column(String(32), default="counter_v1")
    attested_by_member_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("members.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class MessageSuppression(Base):
    """
    Global suppression list. Stores ONLY a SHA-256 hash of the normalized E.164 phone.
    No merchant or business ID: an opt-out (STOP / not me / wrong number) is globally respected across all merchants.
    Only the debtor sending START can resume messages.
    """
    __tablename__ = "message_suppressions"

    phone_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    reason: Mapped[str] = mapped_column(String(32))  # stop_keyword, stop_button, not_me, wrong_number, unreachable
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    resumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class DebtorMessage(Base):
    """
    Tracks every outbound message attempt and delivery state truthfully.
    """
    __tablename__ = "debtor_messages"

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid4)
    business_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("businesses.id", ondelete="CASCADE"), index=True
    )
    debt_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("debts.id", ondelete="CASCADE"), index=True
    )
    stage: Mapped[str] = mapped_column(String(32))
    template_name: Mapped[str] = mapped_column(String(64))
    template_version: Mapped[str] = mapped_column(String(32), default="v2")
    wamid: Mapped[str | None] = mapped_column(String(160), nullable=True, index=True)
    status: Mapped[str] = mapped_column(
        String(32), default="queued", index=True
    )  # queued, accepted, sent, delivered, read, failed, suppressed
    error_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    error_title: Mapped[str | None] = mapped_column(Text, nullable=True)
    pricing_category: Mapped[str | None] = mapped_column(String(32), nullable=True)
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ReminderState(Base):
    """
    Tracks the reminder ladder state per debt.
    """
    __tablename__ = "reminder_state"

    debt_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("debts.id", ondelete="CASCADE"), primary_key=True
    )
    business_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("businesses.id", ondelete="CASCADE"), index=True
    )
    stage: Mapped[str] = mapped_column(String(32), default="record")
    paused_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    paused_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    escalated_to_merchant_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class DebtEvent(Base):
    """
    Immutable chronological audit trail of debt events. Feeds the Evidence Pack.
    """
    __tablename__ = "debt_events"

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid4)
    business_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("businesses.id", ondelete="CASCADE"), index=True
    )
    debt_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("debts.id", ondelete="CASCADE"), index=True
    )
    type: Mapped[str] = mapped_column(
        String(32)
    )  # sale, consent, message, tap, part_payment, note, dispute
    payload: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
