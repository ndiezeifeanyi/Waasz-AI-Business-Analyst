from datetime import UTC, datetime
import logging
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import SecurityError
from app.models.debt import Debt
from app.models.debt_recovery import (
    DebtEvent,
    DebtorConsent,
    DebtorMessage,
    MessageSuppression,
    ReminderState,
)
from app.utils.phone import (
    hash_phone_for_suppression,
    legacy_unkeyed_hash_phone,
    normalize_e164_phone,
    validate_nigerian_phone,
)

logger = logging.getLogger(__name__)

ALLOWED_SUPPRESSION_REASONS = {
    "stop_keyword",
    "stop_button",
    "not_me",
    "wrong_number",
    "unreachable",
}

ALLOWED_DEBT_EVENT_TYPES = {
    "sale",
    "consent",
    "message",
    "tap",
    "part_payment",
    "note",
    "dispute",
}


class DebtRecoveryService:
    """
    Automated, consent-based IOU delivery, reminder and recovery engine.
    Strictly enforces:
    - Counter consent captured by merchant before any automated dispatch.
    - Global recipient-level message suppression (hash-only) across all merchants.
    - Tenant isolation across all tables.
    - Truthful message state delivery tracking.
    """

    async def has_debtor_consent(
        self,
        db: AsyncSession,
        business_id: UUID,
        debt_id: UUID,
    ) -> bool:
        """Checks if active debtor consent exists for a given debt."""
        stmt = select(DebtorConsent).where(
            DebtorConsent.business_id == business_id,
            DebtorConsent.debt_id == debt_id,
            DebtorConsent.revoked_at.is_(None),
        )
        res = await db.execute(stmt)
        return res.scalar_one_or_none() is not None

    async def record_merchant_consent(
        self,
        db: AsyncSession,
        business_id: UUID,
        debt_id: UUID,
        debtor_phone: str,
        member_id: UUID | None = None,
        wording_version: str = "counter_v1",
    ) -> DebtorConsent:
        """
        Records consent given by debtor at the counter, attested by the merchant.
        Validates Nigerian phone number and enforces tenant isolation.
        """
        # 1. Enforce tenant isolation on Debt
        debt = await db.get(Debt, debt_id)
        if not debt or debt.business_id != business_id:
            raise SecurityError(
                f"Tenant isolation violation: Debt {debt_id} does not belong to business {business_id}."
            )

        # 2. Strict Nigerian phone validation
        is_valid, e164_phone, err_msg = validate_nigerian_phone(debtor_phone)
        if not is_valid:
            raise ValueError(err_msg or "Invalid Nigerian mobile phone number.")

        # 3. Check for existing active consent
        stmt = select(DebtorConsent).where(
            DebtorConsent.business_id == business_id,
            DebtorConsent.debt_id == debt_id,
            DebtorConsent.revoked_at.is_(None),
        )
        res = await db.execute(stmt)
        existing = res.scalar_one_or_none()
        if existing:
            existing.debtor_phone = e164_phone
            existing.source = "attested_by_merchant"
            existing.wording_version = wording_version
            if member_id:
                existing.attested_by_member_id = member_id
            consent = existing
        else:
            consent = DebtorConsent(
                business_id=business_id,
                debt_id=debt_id,
                debtor_phone=e164_phone,
                source="attested_by_merchant",
                wording_version=wording_version,
                attested_by_member_id=member_id,
            )
            db.add(consent)

        # 4. Record audit event
        await self.record_debt_event(
            db,
            business_id=business_id,
            debt_id=debt_id,
            event_type="consent",
            payload={
                "agreed": True,
                "source": "attested_by_merchant",
                "phone": e164_phone,
                "wording_version": wording_version,
            },
        )

        # 5. Initialize or reset reminder state
        rem_res = await db.execute(
            select(ReminderState).where(
                ReminderState.debt_id == debt_id,
                ReminderState.business_id == business_id,
            )
        )
        rem_state = rem_res.scalar_one_or_none()
        if not rem_state:
            rem_state = ReminderState(
                debt_id=debt_id,
                business_id=business_id,
                stage="record",
            )
            db.add(rem_state)

        await db.flush()
        return consent

    async def record_declined_consent(
        self,
        db: AsyncSession,
        business_id: UUID,
        debt_id: UUID,
        debtor_phone: str | None = None,
    ) -> None:
        """
        Records that counter consent was declined by the debtor or merchant.
        No automated messages will be queued.
        """
        debt = await db.get(Debt, debt_id)
        if not debt or debt.business_id != business_id:
            raise SecurityError(
                f"Tenant isolation violation: Debt {debt_id} does not belong to business {business_id}."
            )

        norm_phone = normalize_e164_phone(debtor_phone) if debtor_phone else ""
        await self.record_debt_event(
            db,
            business_id=business_id,
            debt_id=debt_id,
            event_type="consent",
            payload={
                "agreed": False,
                "source": "attested_by_merchant",
                "phone": norm_phone,
            },
        )
        await db.flush()

    async def revoke_consent(
        self, db: AsyncSession, business_id: UUID, debt_id: UUID
    ) -> bool:
        """Revokes consent for a specific debt."""
        stmt = select(DebtorConsent).where(
            DebtorConsent.business_id == business_id,
            DebtorConsent.debt_id == debt_id,
            DebtorConsent.revoked_at.is_(None),
        )
        res = await db.execute(stmt)
        consent = res.scalar_one_or_none()
        if not consent:
            return False

        consent.revoked_at = datetime.now(UTC)
        await self.record_debt_event(
            db,
            business_id=business_id,
            debt_id=debt_id,
            event_type="consent",
            payload={"action": "revoked"},
        )
        await db.flush()
        return True

    async def has_valid_consent(
        self, db: AsyncSession, business_id: UUID, debt_id: UUID
    ) -> bool:
        """Returns True if active, unrevoked consent exists for this debt under business_id."""
        stmt = (
            select(DebtorConsent.id)
            .where(
                DebtorConsent.business_id == business_id,
                DebtorConsent.debt_id == debt_id,
                DebtorConsent.revoked_at.is_(None),
            )
            .limit(1)
        )
        res = await db.execute(stmt)
        return res.scalar_one_or_none() is not None

    async def suppress_phone(
        self, db: AsyncSession, phone: str, reason: str
    ) -> MessageSuppression:
        """
        Adds a phone hash to the global suppression list.
        Works across all merchants: STOP means STOP on WhatsApp globally.
        """
        if reason not in ALLOWED_SUPPRESSION_REASONS:
            raise ValueError(f"Invalid suppression reason: '{reason}'.")

        p_hash = hash_phone_for_suppression(phone)
        supp = await db.get(MessageSuppression, p_hash)
        if supp:
            supp.reason = reason
            supp.resumed_at = None
        else:
            supp = MessageSuppression(
                phone_hash=p_hash,
                reason=reason,
            )
            db.add(supp)

        await db.flush()
        return supp

    async def is_phone_suppressed(self, db: AsyncSession, phone: str) -> bool:
        """Checks if a phone number is currently suppressed globally (supports both keyed and legacy unkeyed hashes)."""
        p_hash = hash_phone_for_suppression(phone)
        legacy_hash = legacy_unkeyed_hash_phone(phone)
        stmt = (
            select(MessageSuppression.phone_hash)
            .where(
                MessageSuppression.phone_hash.in_([p_hash, legacy_hash]),
                MessageSuppression.resumed_at.is_(None),
            )
            .limit(1)
        )
        res = await db.execute(stmt)
        return res.scalar_one_or_none() is not None

    async def resume_phone_by_debtor(self, db: AsyncSession, phone: str) -> bool:
        """
        Lifts suppression ONLY when debtor texts START.
        A merchant cannot call this to lift an opt-out.
        Supports both keyed and legacy unkeyed hashes.
        """
        p_hash = hash_phone_for_suppression(phone)
        legacy_hash = legacy_unkeyed_hash_phone(phone)
        supp = await db.get(MessageSuppression, p_hash)
        if not supp:
            supp = await db.get(MessageSuppression, legacy_hash)
        if supp and supp.resumed_at is None:
            supp.resumed_at = datetime.now(UTC)
            await db.flush()
            return True
        return False

    def resume_phone_by_merchant(self, business_id: UUID, phone: str) -> None:
        """
        Hard rule: A merchant CANNOT reverse an opt-out.
        Only the debtor can resume by sending START.
        """
        raise SecurityError(
            "Opt-out policy violation: A merchant cannot reverse a debtor's opt-out. "
            "Under WhatsApp policy and recipient privacy rules, only the debtor can resume by sending START."
        )

    async def can_send_to_debtor(
        self,
        db: AsyncSession,
        business_id: UUID,
        debt_id: UUID,
        debtor_phone: str | None = None,
    ) -> tuple[bool, str]:
        """
        Verifies if an outbound message can be sent to this debtor.
        Checks:
        1. Consent recorded and active for this debt under business_id.
        2. Recipient phone not in global suppression list.
        3. Reminder not currently paused by merchant or agreed payment date.
        """
        # 1. Consent check
        stmt = select(DebtorConsent).where(
            DebtorConsent.business_id == business_id,
            DebtorConsent.debt_id == debt_id,
            DebtorConsent.revoked_at.is_(None),
        )
        res = await db.execute(stmt)
        consent = res.scalar_one_or_none()
        if not consent:
            return False, "no_consent"

        target_phone = debtor_phone or consent.debtor_phone
        if not target_phone:
            return False, "no_phone"

        # 2. Global suppression check
        if await self.is_phone_suppressed(db, target_phone):
            return False, "suppressed"

        # 3. Reminder pause check
        rem_res = await db.execute(
            select(ReminderState).where(
                ReminderState.debt_id == debt_id,
                ReminderState.business_id == business_id,
            )
        )
        rem_state = rem_res.scalar_one_or_none()
        if rem_state and rem_state.paused_until:
            now_utc = datetime.now(UTC)
            if rem_state.paused_until > now_utc:
                return False, f"paused_until_{rem_state.paused_until.isoformat()}"

        return True, "allowed"

    async def record_debt_event(
        self,
        db: AsyncSession,
        business_id: UUID,
        debt_id: UUID,
        event_type: str,
        payload: dict,
    ) -> DebtEvent:
        """Records an immutable audit event for the debt timeline."""
        if event_type not in ALLOWED_DEBT_EVENT_TYPES:
            raise ValueError(f"Invalid debt event type: '{event_type}'.")

        event = DebtEvent(
            business_id=business_id,
            debt_id=debt_id,
            type=event_type,
            payload=payload,
        )
        db.add(event)
        await db.flush()
        return event
