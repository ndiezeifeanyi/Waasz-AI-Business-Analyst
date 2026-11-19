from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, Numeric, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.core.database import Base


class TransactionRiskAssessment(Base):
    __tablename__ = "transaction_risk_assessments"

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid4)
    transaction_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("transactions.id", ondelete="CASCADE"), unique=True
    )
    business_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("businesses.id", ondelete="CASCADE"), index=True
    )

    # Risk Classification: 0.000 (Clean) to 1.000 (Definite Fraud/Anomaly)
    risk_score: Mapped[float] = mapped_column(Numeric(4, 3), default=0.0)
    risk_tier: Mapped[str] = mapped_column(String(30), default="tier_0_clean", index=True)
    # Values: 'tier_0_clean', 'tier_1_step_up', 'tier_2_shadow_quarantined', 'tier_3_hard_block'

    # Anomaly Reasons (JSON Array: e.g. ["VELOCITY_SPIKE", "ORPHAN_CASH_SALE", "MARGIN_OUTLIER"])
    anomaly_flags: Mapped[list] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))

    # Step-Up Verification Lifecycle
    step_up_triggered: Mapped[bool] = mapped_column(Boolean, default=False)
    step_up_status: Mapped[str] = mapped_column(String(30), default="not_required")
    # Values: 'not_required', 'pending_user_proof', 'proof_submitted', 'skipped_by_user', 'expired'
    step_up_prompt_wamid: Mapped[str | None] = mapped_column(String(160), nullable=True)

    # Evidence Attachment (If merchant sends receipt/waybill/transfer photo)
    evidence_media_id: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("media_assets.id", ondelete="SET NULL"), nullable=True
    )

    # Credit Scoring Quarantine Flag
    is_credit_eligible: Mapped[bool] = mapped_column(Boolean, default=True)
    quarantine_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
