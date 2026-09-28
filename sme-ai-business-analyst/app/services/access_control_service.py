from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import logging
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.approved_tester import ApprovedTester
from app.models.invite_code import InviteCode
from app.models.user import User
from app.services.invite_service import InviteCodeService
from app.utils.phone import normalize_phone

logger = logging.getLogger(__name__)


@dataclass
class AccessDecision:
    allowed: bool
    reply_text: str | None = None
    welcome_message: str | None = None
    invite_code: InviteCode | None = None
    reason: str = ""


class AccessControlService:
    """
    Pluggable Access Control Service:
    Supports swappable strategies via settings.access_mode:
      - 'allowlist': Inbound sender must be in approved_testers AND active user count < MAX_ACTIVE_USERS.
      - 'invite_code': Inbound sender must provide a valid invite code with remaining uses.
      - 'open': Inbound sender is admitted immediately without restriction.
    """

    def __init__(self, invite_service: InviteCodeService | None = None) -> None:
        self.invites = invite_service or InviteCodeService()

    async def get_active_user_count(self, db: AsyncSession) -> int:
        """Count actual registered users in the database."""
        stmt = select(func.count(User.id))
        res = await db.execute(stmt)
        return res.scalar() or 0

    async def is_approved_tester(self, db: AsyncSession, norm_phone: str) -> bool:
        """Check if normalized phone number exists in approved_testers."""
        stmt = select(ApprovedTester).where(ApprovedTester.phone_number == norm_phone).limit(1)
        res = await db.execute(stmt)
        return res.scalar_one_or_none() is not None

    async def add_approved_tester(
        self, db: AsyncSession, phone: str, label: str | None = None, added_by: str = "admin"
    ) -> ApprovedTester:
        """Add an approved tester phone number."""
        norm_phone = normalize_phone(phone)
        stmt = select(ApprovedTester).where(ApprovedTester.phone_number == norm_phone).limit(1)
        res = await db.execute(stmt)
        existing = res.scalar_one_or_none()
        if existing:
            if label is not None:
                existing.label = label
            if hasattr(db, "commit"):
                await db.commit()
            return existing

        tester = ApprovedTester(
            phone_number=norm_phone,
            label=label,
            added_by=added_by,
        )
        db.add(tester)
        if hasattr(db, "commit"):
            await db.commit()
        return tester

    async def remove_approved_tester(self, db: AsyncSession, phone: str) -> bool:
        """Remove a phone number from approved testers."""
        norm_phone = normalize_phone(phone)
        stmt = delete(ApprovedTester).where(ApprovedTester.phone_number == norm_phone)
        res = await db.execute(stmt)
        deleted = (res.rowcount or 0) > 0
        if hasattr(db, "commit"):
            await db.commit()
        return deleted

    async def list_approved_testers(self, db: AsyncSession) -> list[ApprovedTester]:
        """List all approved testers."""
        stmt = select(ApprovedTester).order_by(ApprovedTester.added_at.desc())
        res = await db.execute(stmt)
        return list(res.scalars().all())

    async def get_status(self, db: AsyncSession) -> dict:
        """Get live access control metrics."""
        active_users = await self.get_active_user_count(db)
        tester_stmt = select(func.count(ApprovedTester.id))
        tester_res = await db.execute(tester_stmt)
        tester_count = tester_res.scalar() or 0

        return {
            "access_mode": settings.access_mode,
            "max_active_users": settings.max_active_users,
            "active_users_count": active_users,
            "approved_testers_count": tester_count,
            "capacity_reached": active_users >= settings.max_active_users,
        }

    async def evaluate_access(
        self,
        db: AsyncSession,
        norm_phone: str,
        raw_phone: str,
        text: str | None,
        is_existing_user: bool = False,
    ) -> AccessDecision:
        """
        Single gate evaluation before get_or_create_business_and_user() is ever called.
        """
        mode = getattr(settings, "access_mode", "allowlist").lower().strip()
        text_body = (text or "").strip()

        # Strategy 1: OPEN mode
        if mode == "open":
            return AccessDecision(allowed=True, reason="open_mode")

        # Strategy 2: ALLOWLIST mode
        if mode == "allowlist":
            # 1. Independent hard cap on real users (only applies to new user registrations)
            if not is_existing_user:
                active_users = await self.get_active_user_count(db)
                max_users = getattr(settings, "max_active_users", 20)
                if active_users >= max_users:
                    logger.warning(
                        "Allowlist gate rejected %s: user cap reached (%d/%d)",
                        norm_phone,
                        active_users,
                        max_users,
                    )
                    cap_msg = (
                        "👋 Hello! Waasz is currently in a limited private test and has reached its maximum user capacity. "
                        "Please check back soon as more access spots open up!"
                    )
                    return AccessDecision(allowed=False, reply_text=cap_msg, reason="max_users_reached")

            # 2. Check if sender is in approved_testers (enforced on both new and existing users)
            is_approved = await self.is_approved_tester(db, norm_phone)
            if not is_approved:
                # If waitlist requested
                if getattr(settings, "enable_user_referrals", False) and text_body.upper() == "REQUEST":
                    await self.invites.add_to_waitlist(db, raw_phone)
                    waitlist_msg = (
                        "📋 Thank you! You've been added to our private access waitlist. "
                        "We'll message you here as soon as an invitation spot opens up."
                    )
                    return AccessDecision(allowed=False, reply_text=waitlist_msg, reason="waitlist_requested")

                gate_msg = (
                    "👋 Hello! Waasz is currently in a limited private test for approved participants only. "
                    "Access is not open to the public at this time."
                )
                return AccessDecision(allowed=False, reply_text=gate_msg, reason="not_in_approved_testers")

            # Approved tester (under cap or existing): admitted
            return AccessDecision(allowed=True, reason="allowlist_approved")

        # Strategy 3: INVITE_CODE mode
        if mode == "invite_code":
            if is_existing_user:
                return AccessDecision(allowed=True, reason="existing_user_bypass")
            claimed_code = await self.invites.try_claim_from_text(db, text_body)
            if claimed_code:
                ref_msg = ""
                if getattr(settings, "enable_user_referrals", False) and claimed_code.created_by_user_id:
                    ref_msg = "\n\n🎁 You can generate your own personal referral codes once logged in."

                welcome = (
                    "🎉 Welcome to Waasz! Your invite code has been verified and your account is ready.\n\n"
                    "I'm your WhatsApp AI assistant ready to help with business tracking, reminders, tasks, notes, and everyday questions.\n\n"
                    "How can I help you today?"
                    f"{ref_msg}"
                )
                return AccessDecision(
                    allowed=True,
                    welcome_message=welcome,
                    invite_code=claimed_code,
                    reason="invite_code_claimed",
                )

            # Waitlist request check
            if getattr(settings, "enable_user_referrals", False) and text_body.upper() == "REQUEST":
                await self.invites.add_to_waitlist(db, raw_phone)
                waitlist_reply = (
                    "📋 Thank you! You've been added to our private access waitlist. "
                    "We'll message you here as soon as an invitation spot opens up."
                )
                return AccessDecision(allowed=False, reply_text=waitlist_reply, reason="waitlist_requested")

            # Rejection message
            gate_reply = (
                "👋 Hello! Waasz is currently in private access by invitation only.\n\n"
                "• If you have an invite code, simply reply with your code to get started.\n"
                "• If you'd like to join our access waitlist, reply *REQUEST*."
            )
            return AccessDecision(allowed=False, reply_text=gate_reply, reason="invalid_invite_code")

        # Default fallback: allow
        return AccessDecision(allowed=True, reason="default_allow")
