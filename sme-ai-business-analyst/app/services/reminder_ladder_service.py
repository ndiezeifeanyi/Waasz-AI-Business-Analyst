"""
Reminder Ladder Service for Waasz Automated Debt Recovery.
----------------------------------------------------------
Handles the automated reminder ladder schedule (Days -2, 0, +3, +7, +10, +21),
merchant plain-word controls (status, pause, resume, reminders on/off),
and merchant escalation cards when automated WhatsApp messaging stops.
"""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
import logging
from typing import Any
from uuid import UUID
import zoneinfo

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.business import Business
from app.models.customer import Customer
from app.models.debt import Debt
from app.models.debt_recovery import DebtEvent, DebtorConsent, DebtorMessage, ReminderState
from app.services.debt_recovery_service import DebtRecoveryService
from app.services.debtor_send_service import DebtorSendService, WAT_TZ, explain_meta_error
from app.services.whatsapp_client import WhatsAppClient
from app.utils.currency import format_naira
from app.utils.phone import normalize_e164_phone

logger = logging.getLogger(__name__)

# Ladder definition relative to due date (days)
LADDER_SCHEDULE: list[dict[str, Any]] = [
    {"step": "due_soon", "day_offset": -2, "template": "iou_due_soon_v2"},
    {"step": "due_today", "day_offset": 0, "template": "iou_due_today_v2"},
    {"step": "overdue_3", "day_offset": 3, "template": "iou_overdue_v2"},
    {"step": "overdue_7", "day_offset": 7, "template": "iou_overdue_v2"},
    {"step": "final_notice", "day_offset": 10, "template": "iou_final_notice_v2"},
]


class ReminderLadderService:
    def __init__(
        self,
        whatsapp: WhatsAppClient | None = None,
        debtor_sender: DebtorSendService | None = None,
        recovery: DebtRecoveryService | None = None,
    ) -> None:
        self.whatsapp = whatsapp or WhatsAppClient()
        self.debtor_sender = debtor_sender or DebtorSendService(whatsapp=self.whatsapp)
        self.recovery = recovery or DebtRecoveryService()

    # -------------------------------------------------------------------------
    # 1. Automated Daily Ladder Execution (Scheduled by APScheduler at 10 AM WAT)
    # -------------------------------------------------------------------------
    async def run_daily_ladder_check(self, db: AsyncSession) -> dict[str, int]:
        """
        Runs once daily. Checks all outstanding debts with active counter consent.
        Dispatches the appropriate ladder template if due today, or escalates if 21 days past due.
        """
        now_wat = datetime.now(WAT_TZ)
        today_date = now_wat.date()
        logger.info("Running daily debt reminder ladder check at %s (WAT)", now_wat.isoformat())

        stats = {
            "evaluated": 0,
            "dispatched": 0,
            "skipped_paused": 0,
            "skipped_shop_disabled": 0,
            "escalated": 0,
            "errors": 0,
        }

        # Query all outstanding debts that have active debtor consent
        stmt = (
            select(Debt, Customer, Business, DebtorConsent, ReminderState)
            .join(Customer, Debt.customer_id == Customer.id)
            .join(Business, Debt.business_id == Business.id)
            .join(DebtorConsent, (DebtorConsent.debt_id == Debt.id) & (DebtorConsent.revoked_at.is_(None)))
            .outerjoin(ReminderState, ReminderState.debt_id == Debt.id)
            .where(
                Debt.status.in_(["outstanding", "unpaid", "partially_paid"]),
                Debt.due_date.isnot(None),
            )
        )
        res = await db.execute(stmt)
        candidates = res.all()
        stats["evaluated"] = len(candidates)

        for debt, customer, business, consent, rem_state in candidates:
            try:
                # Check 1: Shop-level reminder toggle
                b_settings = dict(business.settings or {})
                if not b_settings.get("reminders_enabled", True):
                    stats["skipped_shop_disabled"] += 1
                    continue

                # Check 2: ReminderState pause
                if rem_state and rem_state.paused_until:
                    paused_until_wat = rem_state.paused_until.astimezone(WAT_TZ) if rem_state.paused_until.tzinfo else rem_state.paused_until.replace(tzinfo=WAT_TZ)
                    if now_wat < paused_until_wat:
                        stats["skipped_paused"] += 1
                        continue

                # Calculate offset relative to due date in WAT
                due_dt_wat = debt.due_date.astimezone(WAT_TZ) if debt.due_date.tzinfo else debt.due_date.replace(tzinfo=WAT_TZ)
                due_date = due_dt_wat.date()
                days_diff = (today_date - due_date).days

                # Check 3: Has this debt reached the Day 14 escalation mark?
                if days_diff >= 14:
                    already_escalated = rem_state and rem_state.stage == "escalated"
                    if not already_escalated:
                        await self.send_merchant_escalation_card(
                            db,
                            business=business,
                            debt=debt,
                            customer=customer,
                            reason="day_14_unpaid",
                        )
                        if rem_state:
                            rem_state.stage = "escalated"
                            rem_state.escalated_to_merchant_at = datetime.now(UTC)
                            rem_state.updated_at = datetime.now(UTC)
                        stats["escalated"] += 1
                    continue

                # Check 4: Find matching ladder step for today
                target_step = None
                target_template = None
                for step_info in LADDER_SCHEDULE:
                    if days_diff == step_info["day_offset"]:
                        target_step = step_info["step"]
                        target_template = step_info["template"]
                        break

                if not target_step or not target_template:
                    # No ladder trigger scheduled for today
                    continue

                # Check 5: Idempotency (has this step already been sent for this debt?)
                if rem_state and rem_state.stage == target_step:
                    logger.debug("Debt %s already had step %s dispatched. Skipping duplicate.", debt.id, target_step)
                    continue

                # Check recent debtor messages for this specific template today
                recent_msg_stmt = (
                    select(DebtorMessage)
                    .where(
                        DebtorMessage.debt_id == debt.id,
                        DebtorMessage.template_name == target_template,
                    )
                    .limit(1)
                )
                existing_msg = (await db.execute(recent_msg_stmt)).scalar_one_or_none()
                if existing_msg:
                    continue

                # Dispatch notice through DebtorSendService (which enforces all 6 rules: quiet hours, caps, suppression)
                ok, reason, send_result = await self.debtor_sender.send_debtor_notice(
                    db=db,
                    business_id=business.id,
                    debt_id=debt.id,
                    notice_type=target_step,
                    template_name=target_template,
                )

                if ok:
                    stats["dispatched"] += 1
                    # Update reminder state
                    if not rem_state:
                        rem_state = ReminderState(
                            business_id=business.id,
                            debt_id=debt.id,
                            stage=target_step,
                            last_sent_at=datetime.now(UTC),
                        )
                        db.add(rem_state)
                    else:
                        rem_state.stage = target_step
                        rem_state.last_sent_at = datetime.now(UTC)

                    # Record debt event
                    await self.recovery.record_debt_event(
                        db=db,
                        debt_id=debt.id,
                        business_id=business.id,
                        event_type=f"ladder_{target_step}_dispatched",
                        details={
                            "template": target_template,
                            "days_diff": days_diff,
                            "phone": customer.phone_number,
                        },
                    )
                else:
                    logger.info("Ladder send skipped for debt %s: %s", debt.id, reason)

            except Exception as exc:
                logger.exception("Error evaluating ladder for debt %s: %s", debt.id, exc)
                stats["errors"] += 1

        if hasattr(db, "commit"):
            await db.commit()
        elif hasattr(db, "flush"):
            await db.flush()

        logger.info("Daily reminder ladder check finished: %s", stats)
        return stats

    # -------------------------------------------------------------------------
    # 2. Smart Escalation Menu (When Automated Messaging Stops)
    # -------------------------------------------------------------------------
    async def send_merchant_escalation_card(
        self,
        db: AsyncSession,
        business: Business,
        debt: Debt,
        customer: Customer,
        reason: str,
    ) -> bool:
        """
        Sends the merchant a smart, actionable escalation card when automated collection
        stops outside WhatsApp (Day 21 reached, debtor texted STOP, or wrong number).
        """
        merchant_phone = business.phone_number
        if not merchant_phone:
            logger.warning("Cannot send escalation card: business %s has no phone number", business.id)
            return False

        amt_str = format_naira(debt.amount)
        cust_name = customer.name or "Customer"
        raw_cust_phone = customer.phone_number or ""
        cust_e164 = normalize_e164_phone(raw_cust_phone) or raw_cust_phone
        clean_phone_digits = "".join(filter(str.isdigit, cust_e164))

        # Explanatory reason
        if reason in ("day_14_unpaid", "day_21_unpaid"):
            headline = f"⚠️ *Debt Follow-up Needed: {cust_name} ({amt_str})*"
            subtext = "Automated WhatsApp reminders have concluded (14 days past due date). No further automated messages will be sent to the debtor."
        elif reason == "opt_out_stop":
            headline = f"🛑 *Customer Opt-Out Notice: {cust_name} ({amt_str})*"
            subtext = "The customer requested to stop receiving automated WhatsApp messages from Waasz."
        elif reason == "wrong_number":
            headline = f"⚠️ *Wrong Number Alert: {cust_name} ({amt_str})*"
            subtext = "The recipient indicated this WhatsApp number does not belong to the debtor."
        else:
            headline = f"⚠️ *Escalation Alert: {cust_name} ({amt_str})*"
            subtext = f"Automated messages paused: {reason}"

        # 1-tap personal WhatsApp wa.me link for the merchant to message directly from their own phone
        wa_forward_text = f"Hello {cust_name}, gentle reminder from {business.name} regarding your pending balance of {amt_str}. Please let us know when convenient to settle."
        import urllib.parse
        encoded_wa_text = urllib.parse.quote(wa_forward_text)
        wa_personal_link = f"https://wa.me/{clean_phone_digits}?text={encoded_wa_text}" if clean_phone_digits else "N/A"

        card = (
            f"{headline}\n\n"
            f"{subtext}\n\n"
            f"📋 *Recommended Next Steps:*\n"
            f"1. 📞 *Call Customer:* Tap to call {cust_e164} directly\n"
            f"2. 💬 *Personal Chat:* Forward directly from your phone:\n"
            f"   👉 {wa_personal_link}\n"
            f"3. 🤝 *Restructure:* Agree on a flexible payment plan (e.g. 2 weekly installments)\n"
            f"4. ✅ *Mark Paid:* If already paid in cash, reply:\n"
            f"   👉 *'paid {cust_name}'*\n\n"
            f"💡 To pause further reminders on any debt at any time, text *'pause {cust_name}'*."
        )

        try:
            from app.utils.tokens import generate_debt_token
            token = generate_debt_token(debt.id, due_date=debt.due_date)
            ref_code = getattr(debt, "ref_code", None) or f"WZ-{str(debt.id).split('-')[0].upper()}"
            tmpl_components = [
                {
                    "type": "body",
                    "parameters": [
                        {"type": "text", "text": business.name or "Merchant"},
                        {"type": "text", "text": ref_code},
                        {"type": "text", "text": cust_name},
                        {"type": "text", "text": amt_str},
                    ],
                },
                {
                    "type": "button",
                    "sub_type": "url",
                    "index": "0",
                    "parameters": [{"type": "text", "text": token}],
                },
            ]
            res = await self.whatsapp.send_template(
                to_phone=merchant_phone,
                template_name="merchant_escalation_v1",
                language_code="en",
                components=tmpl_components,
            )
            if not res.success:
                # Fallback to interactive/card text if template not yet approved by Meta
                res = await self.whatsapp.send_text(merchant_phone, card)

            # Record in DebtorMessage to track asynchronous delivery and read receipts
            msg_record = DebtorMessage(
                business_id=business.id,
                debt_id=debt.id,
                stage="merchant_escalation",
                template_name="merchant_escalation_v1",
                wamid=res.message_id,
                status="accepted" if res.success else "failed",
                error_title=res.error_message if not res.success else None,
            )
            db.add(msg_record)
            await db.flush()

            from app.services.ledger_service import LedgerService
            ledger = LedgerService()
            await ledger.record_outbound_message(
                db, business.id, merchant_phone, card, res
            )
            # Record escalation event
            await self.recovery.record_debt_event(
                db=db,
                debt_id=debt.id,
                business_id=business.id,
                event_type=f"escalation_notified_{reason}",
                payload={"phone": cust_e164, "reason": reason, "wamid": res.message_id},
            )
            return True
        except Exception as exc:
            logger.error("Failed to send merchant escalation card: %s", exc)
            return False

    # -------------------------------------------------------------------------
    # 3. Merchant Plain-Word Control Handlers
    # -------------------------------------------------------------------------
    async def get_status_overview(self, db: AsyncSession, business: Business) -> str:
        """
        Handles 'status' command alone: returns portfolio overview of debts,
        reminders scheduled today, paused debts, and recent alerts.
        """
        now_wat = datetime.now(WAT_TZ)
        today_date = now_wat.date()

        # 1. Active debts count & sum
        stmt = (
            select(Debt, Customer, DebtorConsent, ReminderState)
            .join(Customer, Debt.customer_id == Customer.id)
            .outerjoin(DebtorConsent, (DebtorConsent.debt_id == Debt.id) & (DebtorConsent.revoked_at.is_(None)))
            .outerjoin(ReminderState, ReminderState.debt_id == Debt.id)
            .where(
                Debt.business_id == business.id,
                Debt.status.in_(["outstanding", "unpaid", "partially_paid"]),
            )
            .order_by(Debt.due_date.asc().nullslast())
        )
        res = await db.execute(stmt)
        rows = res.all()

        if not rows:
            return "🎉 *Clear Ledger:* You have no outstanding debts! All customer balances are settled."

        total_owed = sum(d.amount for d, _, _, _ in rows)
        paused_count = 0
        reminders_today_count = 0
        b_settings = dict(business.settings or {})
        reminders_enabled = b_settings.get("reminders_enabled", True)

        # Batch load latest debtor messages for debts for truthful delivery tracking
        debt_ids = [d.id for d, _, _, _ in rows]
        latest_msgs_by_debt: dict[UUID, DebtorMessage] = {}
        if debt_ids:
            msg_res = await db.execute(
                select(DebtorMessage)
                .where(DebtorMessage.debt_id.in_(debt_ids))
                .order_by(DebtorMessage.created_at.desc())
            )
            for m in msg_res.scalars().all():
                if m.debt_id not in latest_msgs_by_debt:
                    latest_msgs_by_debt[m.debt_id] = m

        summary_lines = []
        for debt, customer, consent, rem_state in rows:
            # Check pause
            is_paused = False
            if rem_state and rem_state.paused_until:
                paused_until_wat = rem_state.paused_until.astimezone(WAT_TZ) if rem_state.paused_until.tzinfo else rem_state.paused_until.replace(tzinfo=WAT_TZ)
                if now_wat < paused_until_wat:
                    is_paused = True
                    paused_count += 1

            # Check if reminder lands today
            due_str = "On demand"
            if debt.due_date:
                due_dt_wat = debt.due_date.astimezone(WAT_TZ) if debt.due_date.tzinfo else debt.due_date.replace(tzinfo=WAT_TZ)
                days_diff = (today_date - due_dt_wat.date()).days
                due_str = due_dt_wat.strftime("%d %b")
                if days_diff in (-2, 0, 3, 7, 10) and consent and not is_paused:
                    reminders_today_count += 1

            status_icon = "⏸️ Paused" if is_paused else ("✅ Automated" if consent else "⚠️ Manual only")

            # Truthful delivery badge
            delivery_badge = ""
            lm = latest_msgs_by_debt.get(debt.id)
            if lm:
                lm_st = lm.status.lower()
                if lm_st == "read":
                    delivery_badge = " [👁️ Read]"
                elif lm_st == "delivered":
                    delivery_badge = " [📬 Delivered]"
                elif lm_st in ("accepted", "sent"):
                    delivery_badge = " [⏳ Sent]"
                elif lm_st == "failed":
                    delivery_badge = " [⚠️ Failed]"

            summary_lines.append(f"• *{customer.name}:* {format_naira(debt.amount)} (Due: {due_str}) — {status_icon}{delivery_badge}")

        shop_status = "🟢 *ACTIVE*" if reminders_enabled else "⏸️ *OFF (Shop-wide)*"

        msg = (
            f"📊 *Debt & Reminder Portfolio Overview*\n"
            f"• *Total Outstanding:* *{format_naira(total_owed)}* ({len(rows)} debtors)\n"
            f"• *Shop Automation:* {shop_status}\n"
            f"• *Reminders Today:* {reminders_today_count}\n"
            f"• *Paused Reminders:* {paused_count}\n"
            f"---------------------------------------\n"
            + "\n".join(summary_lines[:10])
            + (f"\n_...and {len(summary_lines) - 10} more_" if len(summary_lines) > 10 else "")
            + "\n\n💡 *Helpful Shortcuts:*\n"
            + "• *status Emeka* — view detailed timeline for a customer\n"
            + "• *pause Emeka* — pause reminders for that customer\n"
            + "• *resume Emeka* — unpause reminders\n"
            + "• *reminders on / off* — toggle automated reminders for shop"
        )
        return msg

    @staticmethod
    def format_delivery_status(msg: DebtorMessage | None) -> str:
        """Formats debtor message status into truthful, transparent human language."""
        if not msg:
            return "No notices sent yet"
        st = msg.status.lower()
        wat_ts = (
            msg.updated_at.astimezone(WAT_TZ).strftime("%d %b at %I:%M %p")
            if msg.updated_at
            else ""
        )
        ts_suffix = f" ({wat_ts})" if wat_ts else ""

        if st in ("accepted", "sent", "queued"):
            return f"⏳ Sent (waiting for delivery confirmation){ts_suffix}"
        elif st == "delivered":
            return f"📬 Delivered to phone{ts_suffix}"
        elif st == "read":
            return f"👁️ Read by customer{ts_suffix}"
        elif st == "failed":
            reason = explain_meta_error(msg.error_code, msg.error_title)
            return f"⚠️ Failed to deliver: {reason}{ts_suffix}"
        elif st == "suppressed":
            return f"🛑 Suppressed (Customer opted out / Unreachable){ts_suffix}"
        return f"{st.title()}{ts_suffix}"

    async def get_customer_status(self, db: AsyncSession, business: Business, query_name: str) -> str:
        """
        Handles 'status Emeka': looks up specific debtor's debt, consent, and reminder timeline.
        """
        clean_name = query_name.strip()
        stmt = (
            select(Debt, Customer, DebtorConsent, ReminderState)
            .join(Customer, Debt.customer_id == Customer.id)
            .outerjoin(DebtorConsent, (DebtorConsent.debt_id == Debt.id) & (DebtorConsent.revoked_at.is_(None)))
            .outerjoin(ReminderState, ReminderState.debt_id == Debt.id)
            .where(
                Debt.business_id == business.id,
                Customer.name.ilike(f"%{clean_name}%"),
                Debt.status.in_(["outstanding", "unpaid", "partially_paid"]),
            )
            .order_by(Debt.created_at.desc())
            .limit(1)
        )
        res = await db.execute(stmt)
        row = res.first()

        if not row:
            return f"ℹ️ No active debt found matching *'{clean_name}'*. Text *'debtors'* to view your full debtor list."

        debt, customer, consent, rem_state = row
        due_str = debt.due_date.strftime("%d %b %Y") if debt.due_date else "Not set"
        consent_status = "✅ Active counter consent" if consent else "⚠️ No automated consent (Manual share only)"

        # Check pause
        now_wat = datetime.now(WAT_TZ)
        is_paused = False
        pause_info = ""
        if rem_state and rem_state.paused_until:
            paused_until_wat = rem_state.paused_until.astimezone(WAT_TZ) if rem_state.paused_until.tzinfo else rem_state.paused_until.replace(tzinfo=WAT_TZ)
            if now_wat < paused_until_wat:
                is_paused = True
                pause_info = f"\n• *Pause State:* ⏸️ Paused until {paused_until_wat.strftime('%d %b %Y')}"

        last_sent = rem_state.last_sent_at.strftime("%d %b at %I:%M %p") if (rem_state and rem_state.last_sent_at) else "None sent yet"
        last_step = (rem_state.stage.replace("_", " ").title()) if (rem_state and rem_state.stage) else "Awaiting schedule"

        # Check latest delivery status
        msg_stmt = (
            select(DebtorMessage)
            .where(DebtorMessage.debt_id == debt.id)
            .order_by(DebtorMessage.created_at.desc())
            .limit(1)
        )
        latest_msg = (await db.execute(msg_stmt)).scalar_one_or_none()
        delivery_status = self.format_delivery_status(latest_msg)

        return (
            f"📋 *Debtor Status: {customer.name}*\n"
            f"• *Amount Owed:* *{format_naira(debt.amount)}*\n"
            f"• *Due Date:* {due_str}\n"
            f"• *Consent:* {consent_status}\n"
            f"• *Last Reminder Sent:* {last_sent} ({last_step})\n"
            f"• *Delivery Status:* {delivery_status}"
            f"{pause_info}\n\n"
            f"💡 *Commands:*\n"
            f"• *'pause {customer.name}'* — pause automated reminders\n"
            f"• *'resume {customer.name}'* — resume reminders\n"
            f"• *'paid {customer.name}'* — mark as settled"
        )

    async def pause_reminders_for_customer(
        self,
        db: AsyncSession,
        business: Business,
        query_name: str,
        days: int = 7,
    ) -> str:
        """
        Pauses automated reminders for a customer for a given number of days (default 7 days).
        """
        clean_name = query_name.strip()
        stmt = (
            select(Debt, Customer, ReminderState)
            .join(Customer, Debt.customer_id == Customer.id)
            .outerjoin(ReminderState, ReminderState.debt_id == Debt.id)
            .where(
                Debt.business_id == business.id,
                Customer.name.ilike(f"%{clean_name}%"),
                Debt.status.in_(["outstanding", "unpaid", "partially_paid"]),
            )
            .order_by(Debt.created_at.desc())
            .limit(1)
        )
        res = await db.execute(stmt)
        row = res.first()

        if not row:
            return f"ℹ️ Could not find an active debt for *'{clean_name}'* to pause. Text *'debtors'* to check names."

        debt, customer, rem_state = row
        pause_until = datetime.now(UTC) + timedelta(days=days)

        if not rem_state:
            rem_state = ReminderState(
                business_id=business.id,
                debt_id=debt.id,
                paused_until=pause_until,
            )
            db.add(rem_state)
        else:
            rem_state.paused_until = pause_until
            rem_state.updated_at = datetime.now(UTC)

        await self.recovery.record_debt_event(
            db=db,
            debt_id=debt.id,
            business_id=business.id,
            event_type="reminders_paused_by_merchant",
            details={"days": days, "paused_until": pause_until.isoformat()},
        )

        pause_until_wat = pause_until.astimezone(WAT_TZ)
        return (
            f"⏸️ *Reminders Paused for {customer.name}*\n\n"
            f"No automated WhatsApp notices will be sent until *{pause_until_wat.strftime('%d %b %Y')}* ({days} days).\n"
            f"To resume earlier, simply text *'resume {customer.name}'*."
        )

    async def resume_reminders_for_customer(
        self,
        db: AsyncSession,
        business: Business,
        query_name: str,
    ) -> str:
        """
        Resumes automated reminders for a customer by clearing paused_until.
        """
        clean_name = query_name.strip()
        stmt = (
            select(Debt, Customer, ReminderState)
            .join(Customer, Debt.customer_id == Customer.id)
            .outerjoin(ReminderState, ReminderState.debt_id == Debt.id)
            .where(
                Debt.business_id == business.id,
                Customer.name.ilike(f"%{clean_name}%"),
                Debt.status.in_(["outstanding", "unpaid", "partially_paid"]),
            )
            .order_by(Debt.created_at.desc())
            .limit(1)
        )
        res = await db.execute(stmt)
        row = res.first()

        if not row:
            return f"ℹ️ Could not find an active debt for *'{clean_name}'*. Text *'debtors'* to check active debts."

        debt, customer, rem_state = row
        if rem_state:
            rem_state.paused_until = None
            rem_state.updated_at = datetime.now(UTC)

        await self.recovery.record_debt_event(
            db=db,
            debt_id=debt.id,
            business_id=business.id,
            event_type="reminders_resumed_by_merchant",
            details={},
        )

        return (
            f"▶️ *Reminders Resumed for {customer.name}*\n\n"
            f"Automated reminder ladder is active again according to due date."
        )

    async def toggle_shop_reminders(
        self,
        db: AsyncSession,
        business: Business,
        enabled: bool,
    ) -> str:
        """
        Enables or disables automated debt reminders shop-wide.
        """
        b_settings = dict(business.settings or {})
        b_settings["reminders_enabled"] = enabled
        business.settings = b_settings

        if enabled:
            return (
                "🟢 *Automated Debt Reminders: ON*\n\n"
                "Waasz will automatically dispatch consent-based WhatsApp reminders "
                "on scheduled dates for your customers."
            )
        else:
            return (
                "⏸️ *Automated Debt Reminders: OFF*\n\n"
                "All automated WhatsApp reminders have been paused shop-wide. "
                "Your debts are still safely recorded. Text *'reminders on'* to restart anytime."
            )
