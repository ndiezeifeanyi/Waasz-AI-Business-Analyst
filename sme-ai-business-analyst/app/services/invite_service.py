from __future__ import annotations

from datetime import UTC, datetime
import logging
import re
import secrets
from uuid import UUID

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.invite_code import InviteCode, Waitlist

logger = logging.getLogger(__name__)


class InviteCodeService:
    """
    Manages invite codes, atomic claims, user referrals, and waitlist registrations.
    """

    def extract_candidate_codes(self, text: str | None) -> list[str]:
        if not text:
            return []
        raw_text = text.strip()
        # Look for alphanumeric / hyphen / underscore tokens of length 4 to 32
        matches = re.findall(r"\b[A-Za-z0-9_-]{4,32}\b", raw_text)
        # Preserve order while deduplicating
        candidates: list[str] = []
        for m in matches:
            u = m.upper()
            if u not in candidates and u != "REQUEST":
                candidates.append(u)
        return candidates

    async def claim_code(self, db: AsyncSession, code: str) -> InviteCode | None:
        """
        Atomically claims a single use of an invite code.
        Uses UPDATE ... WHERE ... RETURNING to ensure race-condition-free claims.
        """
        clean_code = code.strip().upper()
        now = datetime.now(UTC)
        stmt = (
            update(InviteCode)
            .where(
                func.upper(InviteCode.code) == clean_code,
                InviteCode.is_active.is_(True),
                InviteCode.use_count < InviteCode.max_uses,
                or_(InviteCode.expires_at.is_(None), InviteCode.expires_at > now),
            )
            .values(use_count=InviteCode.use_count + 1)
            .returning(InviteCode)
        )
        result = await db.execute(stmt)
        claimed = result.scalar_one_or_none()
        if claimed:
            if hasattr(db, "commit"):
                await db.commit()
            logger.info("Invite code %s successfully claimed (use %d/%d)", clean_code, claimed.use_count, claimed.max_uses)
            return claimed
        return None

    async def try_claim_from_text(self, db: AsyncSession, text: str | None) -> InviteCode | None:
        """
        Scans inbound text for candidate codes and attempts an atomic claim on the first valid match.
        """
        candidates = self.extract_candidate_codes(text)
        for cand in candidates:
            claimed = await self.claim_code(db, cand)
            if claimed:
                return claimed
        return None

    async def create_code(
        self,
        db: AsyncSession,
        code: str | None = None,
        max_uses: int = 1,
        expires_at: datetime | None = None,
        created_by_user_id: UUID | None = None,
    ) -> InviteCode:
        if not code:
            code = f"WAASZ-{secrets.token_hex(3).upper()}"
        clean_code = code.strip().upper()
        invite = InviteCode(
            code=clean_code,
            created_by_user_id=created_by_user_id,
            max_uses=max(1, max_uses),
            expires_at=expires_at,
            is_active=True,
        )
        db.add(invite)
        if hasattr(db, "commit"):
            await db.commit()
        return invite

    async def generate_user_referral_code(
        self,
        db: AsyncSession,
        user_id: UUID,
        max_uses: int = 5,
        expires_at: datetime | None = None,
    ) -> InviteCode:
        ref_code = f"REF-{secrets.token_hex(3).upper()}"
        return await self.create_code(
            db,
            code=ref_code,
            max_uses=max_uses,
            expires_at=expires_at,
            created_by_user_id=user_id,
        )

    async def add_to_waitlist(self, db: AsyncSession, phone_number: str) -> bool:
        """
        Adds a phone number to the waitlist table if not already present.
        """
        from app.utils.phone import normalize_phone
        norm = normalize_phone(phone_number)
        stmt = select(Waitlist).where(Waitlist.phone_number == norm).limit(1)
        res = await db.execute(stmt)
        if res.scalar_one_or_none():
            return False  # Already on waitlist
        entry = Waitlist(phone_number=norm)
        db.add(entry)
        if hasattr(db, "commit"):
            await db.commit()
        logger.info("Phone %s added to access waitlist", norm)
        return True

    async def deactivate_code(self, db: AsyncSession, code_id: UUID) -> bool:
        stmt = update(InviteCode).where(InviteCode.id == code_id).values(is_active=False)
        res = await db.execute(stmt)
        if hasattr(db, "commit"):
            await db.commit()
        return bool(res.rowcount and res.rowcount > 0)

    async def list_codes(
        self, db: AsyncSession, active_only: bool = False, limit: int = 100
    ) -> list[InviteCode]:
        query = select(InviteCode).order_by(InviteCode.created_at.desc()).limit(limit)
        if active_only:
            query = query.where(InviteCode.is_active.is_(True))
        res = await db.execute(query)
        return list(res.scalars().all())

    async def list_waitlist(self, db: AsyncSession, limit: int = 100) -> list[Waitlist]:
        query = select(Waitlist).order_by(Waitlist.requested_at.desc()).limit(limit)
        res = await db.execute(query)
        return list(res.scalars().all())
