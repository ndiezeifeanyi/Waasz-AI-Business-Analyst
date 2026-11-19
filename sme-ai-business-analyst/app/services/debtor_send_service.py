from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
import inspect
import logging
import re
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import SecurityError
from app.models.business import Business
from app.models.customer import Customer
from app.models.debt import Debt
from app.models.debt_recovery import (
    DebtEvent,
    DebtorConsent,
    DebtorMessage,
    MessageSuppression,
    ReminderState,
)
from app.schemas.whatsapp import ParsedWhatsAppMessage, ParsedWhatsAppStatus
from app.services.debt_recovery_service import DebtRecoveryService
from app.services.whatsapp_client import WhatsAppClient
from app.utils.currency import format_naira
from app.utils.phone import normalize_e164_phone
from app.utils.tokens import generate_debt_token, mask_phone_number, verify_debt_token

logger = logging.getLogger(__name__)

# West Africa Time (WAT) is UTC+1
WAT_TZ = timezone(timedelta(hours=1))

META_ERROR_EXPLANATIONS: dict[str, str] = {
    "131047": "Customer has not messaged in over 24 hours. Pre-approved template required.",
    "131049": "Meta delivery policy: recipient throttled (24h cooldown applied).",
    "132000": "Template parameter mismatch. Check template parameters.",
    "132001": "Template language or locale not found on Meta account.",
    "131026": "Phone number unreachable or recipient WhatsApp account inactive.",
    "131051": "Phone number unreachable or recipient WhatsApp account inactive.",
    "130429": "Rate limit hit on Meta WhatsApp API.",
}


def explain_meta_error(error_code: str | None, raw_message: str | None = None) -> str:
    """Translates raw Meta WhatsApp Cloud API error codes into honest, human-readable explanations."""
    if not error_code:
        return raw_message or "Undelivered by Meta"
    code_str = str(error_code).strip()
    return META_ERROR_EXPLANATIONS.get(code_str, raw_message or f"Meta delivery error ({code_str})")


class DebtorSendService:
    """
    Automated outbound sender and webhook receiver for debtor notices.
    Enforces the strict Phase 2 sending rule order:
    1. Kill switch
    2. Consent
    3. Suppression
    4. Quiet hours (08:00 - 19:00 WAT, Sundays after 12:00 WAT)
    5. Frequency caps (1/day, 4/7-days)
    6. Reminder pause
    """

    def __init__(self, whatsapp: WhatsAppClient | None = None) -> None:
        self.whatsapp = whatsapp or WhatsAppClient()
        self.recovery = DebtRecoveryService()

    def is_kill_switch_active(self) -> bool:
        """Checks if the global kill switch for automated debtor messages is active."""
        return getattr(settings, "debt_reminders_kill_switch", False)

    def is_within_sending_hours(self, dt: datetime | None = None) -> bool:
        """
        Permitted sending hours: 08:00 to 19:00 West Africa Time (WAT = UTC+1).
        On Sundays (weekday == 6): Not before 12:00 WAT (12:00 to 19:00 WAT).
        All other hours (19:00 to 08:00, and Sunday before 12:00) are quiet hours.
        """
        from datetime import time as dt_time
        now_wat = (dt or datetime.now(UTC)).astimezone(WAT_TZ)
        now_time = now_wat.time()
        is_sunday = now_wat.weekday() == 6

        start_time = dt_time(12, 0) if is_sunday else dt_time(8, 0)
        end_time = dt_time(19, 0)
        return start_time <= now_time <= end_time

    async def check_frequency_caps(
        self, db: AsyncSession, debtor_phone: str
    ) -> tuple[bool, str]:
        """
        Enforces frequency caps per debtor:
        - Max 1 message in any 24 hours.
        - Max 4 messages in any 7 rolling days.
        """
        norm_phone = normalize_e164_phone(debtor_phone)
        now_utc = datetime.now(UTC)
        cutoff_24h = now_utc - timedelta(hours=24)
        cutoff_7d = now_utc - timedelta(days=7)

        # Count messages across all debts sent to this debtor phone
        # Check through debtor_consents
        consents_stmt = select(DebtorConsent.debt_id).where(
            DebtorConsent.debtor_phone == norm_phone
        )
        c_res = await db.execute(consents_stmt)
        debt_ids = [row[0] for row in c_res.fetchall()]
        if not debt_ids:
            return True, "ok"

        # 24-hour cap
        stmt_24h = (
            select(func.count(DebtorMessage.id))
            .where(
                DebtorMessage.debt_id.in_(debt_ids),
                DebtorMessage.status.in_(("accepted", "sent", "delivered", "read")),
                DebtorMessage.created_at >= cutoff_24h,
            )
        )
        res_24h = await db.execute(stmt_24h)
        count_24h = res_24h.scalar() or 0
        if count_24h >= 1:
            return False, f"daily_cap_reached ({count_24h}/1 in 24h)"

        # 7-day cap
        stmt_7d = (
            select(func.count(DebtorMessage.id))
            .where(
                DebtorMessage.debt_id.in_(debt_ids),
                DebtorMessage.status.in_(("accepted", "sent", "delivered", "read")),
                DebtorMessage.created_at >= cutoff_7d,
            )
        )
        res_7d = await db.execute(stmt_7d)
        count_7d = res_7d.scalar() or 0
        if count_7d >= 4:
            return False, f"weekly_cap_reached ({count_7d}/4 in 7d)"

        return True, "ok"

    async def send_debtor_notice(
        self,
        db: AsyncSession,
        business_id: UUID,
        debt_id: UUID,
        stage: str,
        template_name: str,
        dry_run: bool = False,
    ) -> tuple[bool, str, DebtorMessage | None]:
        """
        One unified send function enforcing the 6 strict checks in order:
        1. Kill switch
        2. Consent
        3. Suppression
        4. Quiet hours
        5. Frequency caps
        6. Reminder pause
        Records attempt and never marks delivered on HTTP 200.
        """
        # 0. Check if template is auto-paused due to elevated failure/stop rates
        from app.services.debt_monitoring_service import is_template_paused
        if is_template_paused(template_name):
            logger.warning("Send blocked: Template '%s' is auto-paused due to elevated failure/STOP rates.", template_name)
            return False, "template_paused", None

        # 1. Kill switch check
        if self.is_kill_switch_active():
            logger.warning("Send blocked: Global kill switch is ACTIVE.")
            return False, "kill_switch_active", None

        # 2. Consent check
        stmt_consent = select(DebtorConsent).where(
            DebtorConsent.business_id == business_id,
            DebtorConsent.debt_id == debt_id,
            DebtorConsent.revoked_at.is_(None),
        )
        res_c = await db.execute(stmt_consent)
        consent = res_c.scalar_one_or_none()
        if not consent:
            logger.info("Send blocked: No active consent for debt %s.", debt_id)
            return False, "no_consent", None

        debtor_phone = consent.debtor_phone
        if not debtor_phone:
            return False, "no_phone", None

        # 3. Global suppression check
        if await self.recovery.is_phone_suppressed(db, debtor_phone):
            logger.info("Send blocked: Debtor %s is globally suppressed.", debtor_phone)
            return False, "suppressed", None

        # 4. Quiet hours check
        if not self.is_within_sending_hours():
            logger.info("Send blocked: Outside permitted sending hours.")
            return False, "outside_sending_hours", None

        # 5. Frequency caps check
        caps_ok, cap_reason = await self.check_frequency_caps(db, debtor_phone)
        if not caps_ok:
            logger.info("Send blocked: Frequency cap exceeded: %s.", cap_reason)
            return False, f"frequency_cap_exceeded: {cap_reason}", None

        # 6. Reminder pause check
        stmt_rem = select(ReminderState).where(
            ReminderState.debt_id == debt_id,
            ReminderState.business_id == business_id,
        )
        res_rem = await db.execute(stmt_rem)
        rem_state = res_rem.scalar_one_or_none()
        if rem_state and rem_state.paused_until:
            now_utc = datetime.now(UTC)
            if rem_state.paused_until > now_utc:
                return False, f"paused_until_{rem_state.paused_until.isoformat()}", None

        # Resolve Debt, Customer, and Business details
        debt = await db.get(Debt, debt_id)
        if not debt:
            return False, "debt_not_found", None

        biz = await db.get(Business, business_id)
        cust = await db.get(Customer, debt.customer_id) if debt.customer_id else None

        cust_name = cust.name if cust else "Valued Customer"
        biz_name = biz.name if biz else "Merchant"
        amt_fmt = format_naira(debt.amount)
        due_str = debt.due_date.strftime("%d %b %Y") if debt.due_date else "Immediate"

        # Generate secure token for URL and button payload (expires no earlier than due_date + 30 days)
        token = generate_debt_token(debt.id, due_date=debt.due_date)

        # Build template components (Never bank details or PDF in template!)
        components = self._build_template_components(
            stage=stage,
            template_name=template_name,
            cust_name=cust_name,
            biz_name=biz_name,
            amt_fmt=amt_fmt,
            due_str=due_str,
            token=token,
            debt_id=debt.id,
        )

        clean_to = re.sub(r"\D+", "", debtor_phone)

        # Dispatch via WhatsApp Client
        wamid = None
        send_status = "accepted"
        err_code = None
        err_msg = None

        if dry_run:
            logger.info("[DRY RUN] Would send template %s to %s", template_name, clean_to)
            wamid = f"dry_run_{token[:16]}"
            send_status = "accepted"
        else:
            send_res = await self.whatsapp.send_template(
                to_phone=clean_to,
                template_name=template_name,
                language_code="en",
                components=components,
            )
            if send_res.success:
                wamid = send_res.message_id
                send_status = "accepted"  # Truthful reporting: Accepted by Meta, NOT delivered!
            else:
                send_status = "failed"
                code_match = re.search(r"\b(13\d{4})\b", send_res.error_message or "")
                err_code = code_match.group(1) if code_match else "dispatch_error"
                err_msg = explain_meta_error(err_code if code_match else None, send_res.error_message)
                logger.warning("Meta template send failed (%s): %s", err_code, err_msg)

        # Record DebtorMessage attempt
        msg_record = DebtorMessage(
            business_id=business_id,
            debt_id=debt_id,
            stage=stage,
            template_name=template_name,
            template_version="v2",
            wamid=wamid,
            status=send_status,
            error_code=err_code,
            error_title=err_msg,
            pricing_category="utility",
            attempt=1,
        )
        db.add(msg_record)

        # Record DebtEvent audit
        await self.recovery.record_debt_event(
            db,
            business_id=business_id,
            debt_id=debt_id,
            event_type="message",
            payload={
                "stage": stage,
                "template_name": template_name,
                "status": send_status,
                "wamid": wamid,
                "phone": debtor_phone,
                "timestamp": datetime.now(UTC).isoformat(),
            },
        )

        if rem_state:
            rem_state.stage = stage
            rem_state.last_sent_at = datetime.now(UTC)

        await db.flush()
        return True, send_status, msg_record

    def _build_template_components(
        self,
        stage: str,
        template_name: str,
        cust_name: str,
        biz_name: str,
        amt_fmt: str,
        due_str: str,
        token: str,
        debt_id: UUID,
    ) -> list[dict]:
        """Builds body and button parameters conforming to template specifications."""
        body_params: list[dict] = []

        if template_name == "iou_record_v2":
            # Body: Hello {{1}}, {{2}} recorded a purchase on credit for you: {{3}}, on {{4}}. It is due on {{5}}. Tap below to see the details.
            today_str = datetime.now(UTC).strftime("%d %b %Y")
            body_params = [
                {"type": "text", "text": cust_name},
                {"type": "text", "text": biz_name},
                {"type": "text", "text": amt_fmt},
                {"type": "text", "text": today_str},
                {"type": "text", "text": due_str},
            ]
        elif template_name in ("iou_due_soon_v2", "iou_overdue_v2", "iou_final_notice_soft_v2"):
            # Body: Hello {{1}}, a reminder from {{2}}. Your balance of {{3}} is due on {{4}}...
            body_params = [
                {"type": "text", "text": cust_name},
                {"type": "text", "text": biz_name},
                {"type": "text", "text": amt_fmt},
                {"type": "text", "text": due_str},
            ]
        elif template_name == "iou_due_today_v2":
            # Body: Hello {{1}}, {{2}} is reminding you that {{3}} is due today.
            body_params = [
                {"type": "text", "text": cust_name},
                {"type": "text", "text": biz_name},
                {"type": "text", "text": amt_fmt},
            ]
        elif template_name == "iou_final_notice_v2":
            # Body: Hello {{1}}, {{2}} has still not received {{3}}, due on {{4}}. If {{2}} does not hear from you by {{5}}...
            cutoff_date = (datetime.now(UTC) + timedelta(days=5)).strftime("%d %b %Y")
            body_params = [
                {"type": "text", "text": cust_name},
                {"type": "text", "text": biz_name},
                {"type": "text", "text": amt_fmt},
                {"type": "text", "text": due_str},
                {"type": "text", "text": cutoff_date},
            ]
        elif template_name == "iou_paid_v2":
            body_params = [
                {"type": "text", "text": cust_name},
                {"type": "text", "text": biz_name},
                {"type": "text", "text": amt_fmt},
                {"type": "text", "text": "₦0.00"},
            ]
        else:
            body_params = [
                {"type": "text", "text": cust_name},
                {"type": "text", "text": biz_name},
                {"type": "text", "text": amt_fmt},
                {"type": "text", "text": due_str},
            ]

        components = [
            {
                "type": "body",
                "parameters": body_params,
            },
            {
                "type": "button",
                "sub_type": "url",
                "index": "0",
                "parameters": [
                    {"type": "text", "text": token}
                ],
            },
        ]
        return components

    async def handle_message_status_update(
        self, db: AsyncSession, status_item: ParsedWhatsAppStatus
    ) -> bool:
        """
        Updates debtor_messages truthfully from Meta webhook statuses.
        Never marks delivered on HTTP 200.
        Idempotent status progression: accepted -> sent -> delivered -> read (or failed).
        Stores error code and pricing category.
        """
        if not status_item.message_id:
            return False

        stmt = select(DebtorMessage).where(DebtorMessage.wamid == status_item.message_id).limit(1)
        res = await db.execute(stmt)
        msg = res.scalar_one_or_none()
        if not msg:
            return False

        new_status = status_item.status.lower()
        STATUS_RANKS = {"queued": 0, "accepted": 1, "sent": 2, "delivered": 3, "read": 4, "failed": 99}

        current_rank = STATUS_RANKS.get(msg.status, 0)
        new_rank = STATUS_RANKS.get(new_status, 0)

        # Do not regress (e.g. read back to delivered, or terminal failed overwritten by late sent)
        if current_rank == 99 and new_status not in ("delivered", "read"):
            # Don't overwrite an existing failure unless Meta explicitly confirmed delivery/read
            return True

        if current_rank >= 4 and new_status != "failed":
            # Already read; don't regress to delivered or sent
            return True

        if current_rank >= 3 and new_status in ("queued", "accepted", "sent"):
            # Already delivered; don't regress to sent/accepted
            return True

        # Extract pricing category if reported by Meta
        raw_payload = status_item.raw_payload or {}
        pricing_cat = raw_payload.get("pricing", {}).get("category")
        if pricing_cat:
            msg.pricing_category = pricing_cat

        if new_rank > current_rank or new_status == "failed":
            msg.status = new_status

        if status_item.timestamp:
            msg.updated_at = status_item.timestamp

        if new_status == "failed":
            err_code_str = str(status_item.error_code or "")
            msg.error_code = err_code_str
            msg.error_title = explain_meta_error(err_code_str, status_item.error_message)

            # Specific error rule handling (Requirement 7)
            if err_code_str == "131047":
                logger.error("🚨 METRIC ALERT: 131047 (Outside 24h window) on message %s for debt %s!", msg.wamid, msg.debt_id)
            elif err_code_str == "131049":
                # Meta recipient throttle cooldown 24 hours
                msg.next_retry_at = datetime.now(UTC) + timedelta(hours=24)
                logger.warning("131049 throttle on message %s. 24h cooldown applied.", msg.wamid)
            elif err_code_str in ("131026", "131051") or "undeliverable" in (status_item.error_message or "").lower():
                # Number unreachable -> suppress and mark
                consent = await db.execute(
                    select(DebtorConsent.debtor_phone).where(DebtorConsent.debt_id == msg.debt_id).limit(1)
                )
                p = consent.scalar_one_or_none()
                if p:
                    await self.recovery.suppress_phone(db, p, reason="unreachable")

            if msg.template_name:
                try:
                    from app.services.debt_monitoring_service import DebtMonitoringService
                    monitoring = DebtMonitoringService(whatsapp=self.whatsapp)
                    await monitoring.check_template_health_and_auto_pause(db, msg.template_name)
                except Exception as exc:
                    logger.warning("Failed to evaluate template health on delivery failure: %s", exc)

        # Record DebtEvent audit
        await self.recovery.record_debt_event(
            db,
            business_id=msg.business_id,
            debt_id=msg.debt_id,
            event_type="message",
            payload={
                "wamid": msg.wamid,
                "status": msg.status,
                "pricing_category": msg.pricing_category,
                "error_code": msg.error_code,
                "error_title": msg.error_title,
                "timestamp": (status_item.timestamp or datetime.now(UTC)).isoformat(),
            },
        )
        await db.flush()
        return True

    async def try_handle_inbound_debtor_message(
        self, db: AsyncSession, parsed: ParsedWhatsAppMessage
    ) -> bool:
        """
        Intercepts incoming messages from debtors before merchant onboarding!
        Handles:
        - STOP, stop, unsubscribe -> Global suppression across all merchants.
        - START -> Resumes global suppression.
        - Button taps & plain words:
          - I have paid -> notifies merchant to confirm before closing.
          - Need more time / date -> pauses reminders, notifies merchant.
          - This is not me / wrong number -> stops reminders for this debt, alerts merchant.
        Returns True if handled as debtor message, False to continue normal merchant routing.
        """
        raw_body = (parsed.body or "").strip()
        body_lower = raw_body.lower()
        interactive_id = (parsed.interactive_reply_id or "").strip().lower()

        # Normalize phone
        from app.utils.phone import normalize_e164_phone
        from_e164 = normalize_e164_phone(parsed.from_phone)

        # 1. Check STOP keywords (Works globally across all merchants)
        STOP_KEYWORDS = {"stop", "unsubscribe", "stop messages", "cancel", "opt out", "optout", "leave"}
        if body_lower in STOP_KEYWORDS or interactive_id in STOP_KEYWORDS:
            await self.recovery.suppress_phone(db, from_e164, reason="stop_keyword")
            
            # Send single acknowledgement to debtor
            try:
                await self.whatsapp.send_text(
                    parsed.from_phone,
                    "You have opted out of WhatsApp reminders. You will not receive further messages. Send START anytime to resume.",
                )
            except Exception:
                pass

            # Notify all affected merchants
            stmt = select(DebtorConsent).where(
                DebtorConsent.debtor_phone == from_e164,
                DebtorConsent.revoked_at.is_(None),
            )
            consents = (await db.execute(stmt)).scalars().all()
            for c in consents:
                biz = await db.get(Business, c.business_id)
                debt = await db.get(Debt, c.debt_id)
                cust = await db.get(Customer, debt.customer_id) if debt and debt.customer_id else None
                cust_name = cust.name if cust else "A customer"
                if biz and biz.phone_number:
                    await self.whatsapp.send_text(
                        biz.phone_number,
                        f"⚠️ *Customer Opted Out:*\n{cust_name} ({mask_phone_number(from_e164)}) has replied STOP. "
                        f"Automatic WhatsApp reminders have stopped for this number.",
                    )

            # Evaluate template health for recently contacted template
            try:
                from app.services.debt_monitoring_service import DebtMonitoringService
                monitoring = DebtMonitoringService(whatsapp=self.whatsapp)
                stmt_recent = (
                    select(DebtorMessage.template_name)
                    .where(DebtorMessage.recipient_phone == from_e164)
                    .order_by(DebtorMessage.created_at.desc())
                    .limit(1)
                )
                recent_tmpl = (await db.execute(stmt_recent)).scalar_one_or_none()
                if recent_tmpl:
                    await monitoring.check_template_health_and_auto_pause(db, recent_tmpl)
            except Exception as exc:
                logger.warning("Failed to evaluate template health on STOP: %s", exc)

            await db.commit()
            return True

        # 2. Check START keyword (Lifts suppression)
        if body_lower in ("start", "unstop") or interactive_id in ("start", "unstop"):
            resumed = await self.recovery.resume_phone_by_debtor(db, from_e164)
            if resumed:
                try:
                    await self.whatsapp.send_text(
                        parsed.from_phone,
                        "Your WhatsApp reminders have been resumed. Thank you.",
                    )
                except Exception:
                    pass
                await db.commit()
                return True

        # Check if caller has an active debt consent
        stmt_c = select(DebtorConsent).where(
            DebtorConsent.debtor_phone == from_e164,
            DebtorConsent.revoked_at.is_(None),
        ).order_by(DebtorConsent.created_at.desc()).limit(1)
        res_c = await db.execute(stmt_c)
        active_consent = res_c.scalar_one_or_none()
        if inspect.isawaitable(active_consent):
            active_consent = await active_consent
        if not active_consent or not isinstance(active_consent, DebtorConsent):
            return False

        debt_coro = db.get(Debt, active_consent.debt_id)
        debt = await debt_coro if inspect.isawaitable(debt_coro) else debt_coro
        if not debt or getattr(debt, "status", "") == "paid":
            return False

        biz_coro = db.get(Business, debt.business_id)
        biz = await biz_coro if inspect.isawaitable(biz_coro) else biz_coro

        cust = None
        if getattr(debt, "customer_id", None):
            cust_coro = db.get(Customer, debt.customer_id)
            cust = await cust_coro if inspect.isawaitable(cust_coro) else cust_coro
        cust_name = cust.name if cust else "Customer"
        amt_fmt = format_naira(debt.amount)

        # Query prior tap events to check total pause limit and count repeat taps
        prior_events_res = await db.execute(
            select(DebtEvent).where(DebtEvent.debt_id == debt.id, DebtEvent.type == "tap")
        )
        prior_events = prior_events_res.scalars().all()
        total_pauses_granted = len([
            e for e in prior_events
            if (e.payload or {}).get("pause_granted") is True
        ])

        # 3. Handle "I have paid"
        if any(kw in body_lower for kw in ("i have paid", "i paid", "paid", "already paid")) or "paid" in interactive_id:
            prior_taps = [
                e for e in prior_events
                if (e.payload or {}).get("action") == "paid"
            ]
            tap_count = len(prior_taps) + 1

            # Pause reminders strictly ONCE per debt across both "I have paid" and extensions
            rem_res = await db.execute(select(ReminderState).where(ReminderState.debt_id == debt.id))
            rem_state = rem_res.scalar_one_or_none()
            can_pause = (total_pauses_granted == 0) and (tap_count == 1)

            if rem_state and can_pause:
                rem_state.paused_until = datetime.now(UTC) + timedelta(hours=48)
                rem_state.paused_reason = "debtor_claimed_paid_pending_merchant_confirmation"
                pause_granted = True
            else:
                pause_granted = False

            # Log audit event with tap count and pause granted flag
            await self.recovery.record_debt_event(
                db,
                business_id=debt.business_id,
                debt_id=debt.id,
                event_type="tap",
                payload={
                    "action": "paid",
                    "source": "whatsapp_reply",
                    "tap_count": tap_count,
                    "pause_granted": pause_granted,
                    "timestamp": datetime.now(UTC).isoformat(),
                },
            )

            # Factual & neutral acknowledgement to debtor (NO implication of acceptance & NO auto-resume promise!)
            await self.whatsapp.send_text(
                parsed.from_phone,
                f"We noted your payment report. {biz.name if biz else 'The shop'} has been notified to check their account and confirm receipt.",
            )

            # Notify merchant to verify before closing debt (using approved template with text fallback)
            if biz and biz.phone_number:
                repeat_tag = f" *(Report #{tap_count})*" if tap_count > 1 else ""
                pause_note = (
                    "Reminders are paused for 48 hours to allow you to check your account."
                    if pause_granted
                    else "The reminder pause limit for this debt has already been used. Reminders remain on the current schedule."
                )
                merchant_msg = (
                    f"🔔 *Customer Payment Notification{repeat_tag}:*\n"
                    f"{cust_name} ({mask_phone_number(from_e164)}) reported that they have paid {amt_fmt}.\n\n"
                    f"{pause_note}\n\n"
                    f"Please check your bank account to confirm receipt. Reply *'paid {cust_name}'* to close this debt record."
                )
                m_token = generate_debt_token(debt.id, due_date=debt.due_date)
                ref_code = getattr(debt, "ref_code", None) or f"WZ-{str(debt.id).split('-')[0].upper()}"
                m_components = [
                    {
                        "type": "body",
                        "parameters": [
                            {"type": "text", "text": biz.name or "Merchant"},
                            {"type": "text", "text": cust_name},
                            {"type": "text", "text": amt_fmt},
                            {"type": "text", "text": ref_code},
                        ],
                    },
                    {
                        "type": "button",
                        "sub_type": "url",
                        "index": "0",
                        "parameters": [{"type": "text", "text": m_token}],
                    },
                ]
                m_res = await self.whatsapp.send_template(
                    to_phone=biz.phone_number,
                    template_name="merchant_payment_alert_v1",
                    language_code="en",
                    components=m_components,
                )
                if not m_res.success:
                    m_res = await self.whatsapp.send_text(biz.phone_number, merchant_msg)

                # Record in DebtorMessage for webhook delivery tracking
                m_record = DebtorMessage(
                    business_id=biz.id,
                    debt_id=debt.id,
                    stage="merchant_alert",
                    template_name="merchant_payment_alert_v1",
                    wamid=m_res.message_id,
                    status="accepted" if m_res.success else "failed",
                    error_title=m_res.error_message if not m_res.success else None,
                )
                db.add(m_record)
            await db.commit()
            return True

        # 4. Handle "Need more time" / "Choose a date" (Requires merchant approval; max 14 days)
        if any(kw in body_lower for kw in ("need more time", "more time", "choose a date", "give me time", "later")) or "time" in interactive_id:
            # Check if debtor provided a date in the text
            date_match = re.search(r"\b(\d{4}-\d{2}-\d{2}|\d{1,2}[/-]\d{1,2}[/-]\d{2,4})\b", raw_body)
            p_date_str = date_match.group(0) if date_match else "a later date"

            await self.recovery.record_debt_event(
                db,
                business_id=debt.business_id,
                debt_id=debt.id,
                event_type="tap",
                payload={
                    "action": "need_time",
                    "proposed_date": p_date_str,
                    "pause_granted": False,
                    "source": "whatsapp_reply",
                    "timestamp": datetime.now(UTC).isoformat(),
                },
            )

            # Debtor informed: Extension requires direct merchant approval
            await self.whatsapp.send_text(
                parsed.from_phone,
                f"We noted your request for more time. {biz.name if biz else 'The shop'} has been notified. Any payment extension requires the shop's direct approval.",
            )
            if biz and biz.phone_number:
                await self.whatsapp.send_text(
                    biz.phone_number,
                    f"⏳ *Payment Extension Request:*\n"
                    f"{cust_name} ({mask_phone_number(from_e164)}) requested more time to pay {amt_fmt} (proposed: *{p_date_str}*).\n\n"
                    f"To approve an extension, reply *'EXTEND {cust_name} [1-14] days'* (Maximum 14 days).\n"
                    f"To decline, reply *'DECLINE EXTENSION {cust_name}'*.",
                )
            await db.commit()
            return True

        # 5. Handle "This is not me" / wrong number
        if any(kw in body_lower for kw in ("this is not me", "not me", "wrong number", "wrong person")) or "not_me" in interactive_id:
            await self.recovery.record_debt_event(
                db,
                business_id=debt.business_id,
                debt_id=debt.id,
                event_type="tap",
                payload={"action": "not_me", "source": "whatsapp_reply", "timestamp": datetime.now(UTC).isoformat()},
            )
            # Stop automatic messages for this debt
            rem_res = await db.execute(select(ReminderState).where(ReminderState.debt_id == debt.id))
            rem_state = rem_res.scalar_one_or_none()
            if rem_state:
                rem_state.paused_until = datetime.now(UTC) + timedelta(days=365)
                rem_state.paused_reason = "reported_not_me"

            await self.whatsapp.send_text(
                parsed.from_phone,
                "Thank you. Messages for this record have been stopped for this number.",
            )
            if biz and biz.phone_number:
                await self.whatsapp.send_text(
                    biz.phone_number,
                    f"⚠️ *Identity / Wrong Number Report:*\n"
                    f"The recipient of the purchase record for {cust_name} ({amt_fmt}) reported: *'This is not me'*.\n\n"
                    f"Automatic reminders have been stopped for this number. Please check the customer's phone number.",
                )
            await db.commit()
            return True

        return False
