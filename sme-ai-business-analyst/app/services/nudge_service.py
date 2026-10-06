from datetime import UTC, datetime, timedelta
import logging
from zoneinfo import ZoneInfo
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.business import Business
from app.models.user import User
from app.models.message import WhatsAppMessage
from app.services.ledger_service import LedgerService
from app.services.whatsapp_client import WhatsAppClient

logger = logging.getLogger(__name__)

DEFAULT_NUDGE_TEXT = "Didn't hear from you today — want to log anything before it slips your mind? 📝\n(You can reply with a sale, expense, task, or say hi!)"
UTILITY_TEMPLATE_NAME = "daily_inactivity_checkin_v1"


class InactivityNudgeService:
    def __init__(
        self,
        whatsapp: WhatsAppClient | None = None,
        ledger: LedgerService | None = None,
    ) -> None:
        self.whatsapp = whatsapp or WhatsAppClient()
        self.ledger = ledger or LedgerService()

    async def dispatch_daily_inactivity_nudges(
        self,
        db: AsyncSession,
        target_user_id: UUID | None = None,
        now_override: datetime | None = None,
    ) -> int:
        """
        Check all active users. If their last inbound message was not today in their local timezone,
        and they have not been silent for more than 3 consecutive days, send a gentle 6pm nudge.
        """
        now_utc = now_override or datetime.now(UTC)

        stmt = select(User).where(User.is_active.is_(True), User.deleted_at.is_(None))
        if target_user_id:
            stmt = stmt.where(User.id == target_user_id)

        res = await db.execute(stmt)
        users = list(res.scalars().all())

        nudged_count = 0
        for user in users:
            try:
                sent = await self._evaluate_and_nudge_user(db, user, now_utc)
                if sent:
                    nudged_count += 1
            except Exception as e:
                logger.error("Failed to evaluate/nudge user %s: %s", user.id, e)

        return nudged_count

    async def _evaluate_and_nudge_user(
        self,
        db: AsyncSession,
        user: User,
        now_utc: datetime,
    ) -> bool:
        # 1. Resolve local timezone
        tz_name = settings.local_timezone
        if user.business_id:
            biz = await db.get(Business, user.business_id)
            if biz and getattr(biz, "timezone", None):
                tz_name = biz.timezone

        try:
            user_tz = ZoneInfo(tz_name)
        except Exception:
            user_tz = ZoneInfo("Africa/Lagos")

        now_local = now_utc.astimezone(user_tz)
        today_local_date = now_local.date()

        # 2. Check last inbound activity
        last_inbound_utc = user.last_inbound_at
        if last_inbound_utc:
            if last_inbound_utc.tzinfo is None:
                last_inbound_utc = last_inbound_utc.replace(tzinfo=UTC)
            last_inbound_local = last_inbound_utc.astimezone(user_tz)
            last_inbound_date = last_inbound_local.date()

            # If user has already communicated today in their local timezone, skip
            if last_inbound_date == today_local_date:
                logger.debug("User %s already had activity today (%s); skipping nudge", user.id, today_local_date)
                return False

            # Cap on consecutive silent days: skip if silent for > 3 consecutive days
            silent_days = (today_local_date - last_inbound_date).days
            if silent_days > 3:
                logger.info(
                    "User %s has been silent for %d consecutive days (> 3 cap); skipping nudge",
                    user.id,
                    silent_days,
                )
                return False
        else:
            # User has never sent an inbound message; check account age
            created_at_utc = user.created_at
            if created_at_utc:
                if created_at_utc.tzinfo is None:
                    created_at_utc = created_at_utc.replace(tzinfo=UTC)
                created_days = (now_utc - created_at_utc).days
                if created_days > 3:
                    logger.info("User %s has never messaged and account is %d days old; skipping nudge", user.id, created_days)
                    return False

        # 3. Check idempotency: ensure we haven't already nudged user today
        recent_msg_stmt = (
            select(WhatsAppMessage)
            .where(
                WhatsAppMessage.user_id == user.id,
                WhatsAppMessage.direction == "outbound",
                WhatsAppMessage.created_at >= now_utc - timedelta(hours=14),
            )
            .order_by(WhatsAppMessage.created_at.desc())
            .limit(5)
        )
        out_res = await db.execute(recent_msg_stmt)
        recent_msgs = out_res.scalars().all()
        for msg in recent_msgs:
            body = (msg.body or "").lower()
            if "didn't hear from you today" in body or "daily_inactivity_checkin" in body:
                logger.info("User %s was already nudged today; skipping", user.id)
                return False

        # 4. Respect 24-hour customer care window
        is_within_24h = False
        if last_inbound_utc:
            is_within_24h = (now_utc - last_inbound_utc) <= timedelta(hours=24)

        user_name = user.display_name or "there"
        if is_within_24h:
            # Free-form session message allowed
            send_res = await self.whatsapp.send_text(user.phone_number, DEFAULT_NUDGE_TEXT)
            outbound_text = DEFAULT_NUDGE_TEXT
        else:
            # Outside 24h window: Must use approved WhatsApp utility template
            logger.info("User %s outside 24h window; sending utility template %s", user.id, UTILITY_TEMPLATE_NAME)
            template_components = [
                {
                    "type": "body",
                    "parameters": [
                        {"type": "text", "text": user_name},
                    ],
                }
            ]
            send_res = await self.whatsapp.send_template(
                user.phone_number,
                template_name=UTILITY_TEMPLATE_NAME,
                language_code="en",
                components=template_components,
            )
            outbound_text = f"[Template: {UTILITY_TEMPLATE_NAME}] {DEFAULT_NUDGE_TEXT}"

        if user.business_id:
            await self.ledger.record_outbound_message(
                db,
                business_id=user.business_id,
                to_phone=user.phone_number,
                body=outbound_text,
                result=send_res,
                user_id=user.id,
            )
            await db.commit()

        logger.info("Dispatched 6pm inactivity nudge to user %s (%s)", user.id, user.phone_number)
        return True
