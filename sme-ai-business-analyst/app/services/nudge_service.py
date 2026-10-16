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

DEFAULT_NUDGE_TEXT = "Bookkeeping alert: Your daily business ledger is open for reconciliation. Please reply with any sales or expenses recorded today to finalize your closing balance."
UTILITY_TEMPLATE_NAME = "daily_ledger_reconciliation_v1"
FALLBACK_TEMPLATE_NAME = "daily_inactivity_checkin_v1"


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

        # Suppress repeated nudges if user has 0 lifetime transactions and account is >= 2 days old
        if user.business_id:
            from app.models.transaction import Transaction
            from sqlalchemy import func
            tx_count = await db.scalar(
                select(func.count(Transaction.id)).where(Transaction.business_id == user.business_id)
            )
            if (not tx_count or tx_count == 0) and user.created_at:
                created_dt = user.created_at if user.created_at.tzinfo else user.created_at.replace(tzinfo=UTC)
                created_days = (now_utc - created_dt).days
                if created_days >= 2:
                    logger.info("User %s has 0 lifetime transactions and account is %d days old; skipping repeated nudge spam", user.id, created_days)
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
            if "didn't hear from you today" in body or "daily_inactivity_checkin" in body or "ledger_reconciliation" in body:
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
            logger.info("User %s outside 24h window; dispatching utility template %s", user.id, UTILITY_TEMPLATE_NAME)
            
            date_str = now_local.strftime("%b %d")
            tx_count_today = 0
            if user.business_id:
                from app.models.transaction import Transaction
                from sqlalchemy import func
                start_of_day_utc = now_local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(UTC)
                tx_count_today = (
                    await db.scalar(
                        select(func.count(Transaction.id)).where(
                            Transaction.business_id == user.business_id,
                            Transaction.created_at >= start_of_day_utc,
                        )
                    )
                    or 0
                )

            # Components for 3 variables: Name, Date, Entries
            components_3 = [
                {
                    "type": "body",
                    "parameters": [
                        {"type": "text", "text": user_name},
                        {"type": "text", "text": date_str},
                        {"type": "text", "text": str(tx_count_today)},
                    ],
                }
            ]
            components_1 = [
                {
                    "type": "body",
                    "parameters": [
                        {"type": "text", "text": user_name},
                    ],
                }
            ]

            # Attempt 1: daily_ledger_reconciliation_v1 with 3 params (lang 'en')
            send_res = await self.whatsapp.send_template(
                user.phone_number,
                template_name=UTILITY_TEMPLATE_NAME,
                language_code="en",
                components=components_3,
            )

            # Attempt 2: if parameter count mismatch, retry with 1 param
            if not send_res.success and ("132000" in str(send_res.error_message) or "parameter" in str(send_res.error_message).lower()):
                send_res = await self.whatsapp.send_template(
                    user.phone_number,
                    template_name=UTILITY_TEMPLATE_NAME,
                    language_code="en",
                    components=components_1,
                )

            # Attempt 3: if language en_US needed
            if not send_res.success and "132001" in str(send_res.error_message):
                send_res = await self.whatsapp.send_template(
                    user.phone_number,
                    template_name=UTILITY_TEMPLATE_NAME,
                    language_code="en_US",
                    components=components_3,
                )

            # Attempt 4: If not yet approved on Meta, fallback to active template
            if not send_res.success and ("132001" in str(send_res.error_message) or "does not exist" in str(send_res.error_message).lower()):
                logger.info("Utility template %s not yet active on Meta. Trying fallback %s", UTILITY_TEMPLATE_NAME, FALLBACK_TEMPLATE_NAME)
                send_res = await self.whatsapp.send_template(
                    user.phone_number,
                    template_name=FALLBACK_TEMPLATE_NAME,
                    language_code="en_US",
                    components=components_1,
                )
                if not send_res.success:
                    send_res = await self.whatsapp.send_template(
                        user.phone_number,
                        template_name=FALLBACK_TEMPLATE_NAME,
                        language_code="en",
                        components=components_1,
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
