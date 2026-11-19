"""
Debt Monitoring & Template Quality Watcher.
Monitors:
- Debts currently in paused state (remaining pause window, stale pause detection).
- Meta template status updates, quality ratings, and category reclassifications.
- Automated template health checks (failure rate >= 8%, STOP rate >= 3%) with auto-pause
  and founder alerting.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.business import Business
from app.models.customer import Customer
from app.models.debt import Debt
from app.models.debt_recovery import DebtEvent, DebtorConsent, DebtorMessage, ReminderState
from app.services.whatsapp_client import WhatsAppClient

logger = logging.getLogger(__name__)
UTC = timezone.utc

# In-memory registry of paused templates
_PAUSED_TEMPLATES: dict[str, dict[str, Any]] = {}


def is_template_paused(template_name: str) -> bool:
    """Checks whether a template is currently in an auto-paused state."""
    return template_name in _PAUSED_TEMPLATES


def get_paused_templates() -> dict[str, dict[str, Any]]:
    """Returns all currently paused templates with reasons and timestamps."""
    return dict(_PAUSED_TEMPLATES)


def pause_template(template_name: str, reason: str, stats: dict[str, Any] | None = None) -> None:
    """Manually or automatically pauses a template from outbound use."""
    _PAUSED_TEMPLATES[template_name] = {
        "paused_at": datetime.now(UTC).isoformat(),
        "reason": reason,
        "stats": stats or {},
    }


def unpause_template(template_name: str) -> bool:
    """Resumes an auto-paused template."""
    if template_name in _PAUSED_TEMPLATES:
        del _PAUSED_TEMPLATES[template_name]
        return True
    return False


def clear_all_paused_templates() -> None:
    """Resets the paused template registry (primarily for test isolation)."""
    _PAUSED_TEMPLATES.clear()


class DebtMonitoringService:
    def __init__(self, whatsapp: WhatsAppClient | None = None) -> None:
        self.whatsapp = whatsapp or WhatsAppClient()

    async def get_active_pauses(self, db: AsyncSession, business_id: UUID | None = None) -> list[dict[str, Any]]:
        """
        Retrieves all currently paused debts, remaining pause duration, and flags stale pauses (> 14 days).
        """
        now = datetime.now(UTC)
        stmt = (
            select(ReminderState, Debt, Customer, Business)
            .join(Debt, ReminderState.debt_id == Debt.id)
            .join(Business, Debt.business_id == Business.id)
            .outerjoin(Customer, Debt.customer_id == Customer.id)
            .where(
                ReminderState.paused_until.isnot(None),
                ReminderState.paused_until > now,
                Debt.status.in_(["outstanding", "unpaid", "partially_paid"]),
            )
        )
        if business_id:
            stmt = stmt.where(ReminderState.business_id == business_id)

        res = await db.execute(stmt)
        rows = res.all()

        active_pauses = []
        for rem_state, debt, customer, biz in rows:
            remaining_seconds = (rem_state.paused_until - now).total_seconds()
            remaining_hours = round(remaining_seconds / 3600.0, 1)
            is_stale = remaining_hours > (14 * 24)

            active_pauses.append({
                "debt_id": str(debt.id),
                "business_id": str(biz.id),
                "business_name": biz.name,
                "customer_name": customer.name if customer else "Unknown",
                "amount": float(debt.amount),
                "currency": debt.currency,
                "paused_until": rem_state.paused_until.isoformat(),
                "remaining_hours": remaining_hours,
                "paused_reason": rem_state.paused_reason,
                "is_stale": is_stale,
            })
        return active_pauses

    async def get_ladder_monitoring_summary(self, db: AsyncSession, business_id: UUID | None = None) -> dict[str, Any]:
        """
        Provides aggregate metrics on debtor messages, deliverability, repeat tap counts, and active pauses.
        """
        now = datetime.now(UTC)
        pauses = await self.get_active_pauses(db, business_id)

        # Message deliverability breakdown
        stmt_msg = select(DebtorMessage.status, func.count(DebtorMessage.id)).group_by(DebtorMessage.status)
        if business_id:
            stmt_msg = stmt_msg.where(DebtorMessage.business_id == business_id)
        msg_counts_res = await db.execute(stmt_msg)
        status_breakdown = dict(msg_counts_res.all())

        # Repeat tap audit count
        stmt_taps = select(func.count(DebtEvent.id)).where(DebtEvent.type == "tap")
        if business_id:
            stmt_taps = stmt_taps.where(DebtEvent.business_id == business_id)
        tap_count = (await db.execute(stmt_taps)).scalar() or 0

        return {
            "active_pauses_count": len(pauses),
            "stale_pauses_count": sum(1 for p in pauses if p["is_stale"]),
            "message_status_breakdown": status_breakdown,
            "total_debtor_taps_logged": tap_count,
            "monitored_at": now.isoformat(),
        }

    async def check_template_health_and_auto_pause(
        self,
        db: AsyncSession,
        template_name: str,
        window_hours: int = 24,
        min_samples: int = 5,
    ) -> dict[str, Any]:
        """
        Evaluates delivery failure rate and recipient STOP rate for a template over window_hours.
        If failure rate >= 8% or STOP rate >= 3% (with >= min_samples):
        1. Automatically pauses the template in _PAUSED_TEMPLATES.
        2. Sends high-priority WhatsApp alert to founder (settings.founder_phone).
        """
        now = datetime.now(UTC)
        cutoff = now - timedelta(hours=window_hours)

        # 1. Total attempts and failures for this template
        stmt = select(
            func.count(DebtorMessage.id).label("total_attempts"),
            func.coalesce(
                func.sum(case((DebtorMessage.status == "failed", 1), else_=0)), 0
            ).label("failed_count"),
        ).where(
            DebtorMessage.template_name == template_name,
            DebtorMessage.created_at >= cutoff,
        )
        res = (await db.execute(stmt)).first()
        total_attempts = res.total_attempts if res else 0
        failed_count = int(res.failed_count) if res else 0

        # 2. Recipient STOP/opt-out count for debts contacted by this template in the window
        stop_stmt = (
            select(func.count(DebtEvent.id))
            .join(DebtorMessage, DebtEvent.debt_id == DebtorMessage.debt_id)
            .where(
                DebtorMessage.template_name == template_name,
                DebtorMessage.created_at >= cutoff,
                DebtEvent.type.in_(["suppression", "debtor_stop", "opt_out"]),
                DebtEvent.created_at >= cutoff,
            )
        )
        stop_count = (await db.execute(stop_stmt)).scalar() or 0

        failure_rate = (failed_count / total_attempts) if total_attempts > 0 else 0.0
        stop_rate = (stop_count / total_attempts) if total_attempts > 0 else 0.0

        fail_thresh = getattr(settings, "meta_template_failure_threshold", 0.08)
        stop_thresh = getattr(settings, "meta_template_stop_threshold", 0.03)

        auto_paused = False
        reasons = []

        if total_attempts >= min_samples:
            if failure_rate >= fail_thresh:
                reasons.append(f"Failure rate {failure_rate:.1%} exceeds threshold {fail_thresh:.1%}")
            if stop_rate >= stop_thresh:
                reasons.append(f"STOP rate {stop_rate:.1%} exceeds threshold {stop_thresh:.1%}")

        if reasons:
            pause_reason = " & ".join(reasons)
            pause_template(template_name, pause_reason, {
                "total_attempts": total_attempts,
                "failed_count": failed_count,
                "failure_rate": failure_rate,
                "stop_count": stop_count,
                "stop_rate": stop_rate,
            })
            auto_paused = True
            logger.error("🚨 AUTO-PAUSED TEMPLATE %s: %s", template_name, pause_reason)

            # Alert founder via WhatsApp
            founder_phone = getattr(settings, "founder_phone", "2347065015924")
            if founder_phone:
                alert_text = (
                    f"🚨 *AUTOMATIC TEMPLATE PAUSE ALERT*\n\n"
                    f"Template: `{template_name}` has been automatically PAUSED to protect sender reputation.\n\n"
                    f"📊 *Metrics (last {window_hours}h):*\n"
                    f"• Attempts: {total_attempts}\n"
                    f"• Failures: {failed_count} ({failure_rate:.1%} vs max {fail_thresh:.1%})\n"
                    f"• STOP opt-outs: {stop_count} ({stop_rate:.1%} vs max {stop_thresh:.1%})\n\n"
                    f"⚠️ Cause: {pause_reason}\n"
                    f"Outbound messages using this template will be blocked until unpaused."
                )
                try:
                    await self.whatsapp.send_text(founder_phone, alert_text)
                except Exception as exc:
                    logger.warning("Failed to send founder alert for auto-paused template %s: %s", template_name, exc)

        return {
            "template_name": template_name,
            "total_attempts": total_attempts,
            "failed_count": failed_count,
            "failure_rate": failure_rate,
            "stop_count": stop_count,
            "stop_rate": stop_rate,
            "auto_paused": auto_paused,
            "is_paused": is_template_paused(template_name),
            "reasons": reasons,
        }

    async def handle_meta_template_status_webhook(
        self,
        db: AsyncSession,
        field: str,
        value: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Ingests Meta's webhook changes for:
        - message_template_status_update (event: APPROVED | REJECTED | PAUSED | DISABLED)
        - template_category_update (new_category: UTILITY | MARKETING | AUTHENTICATION)
        - message_template_quality_update (quality score: GREEN | YELLOW | RED)
        """
        template_name = value.get("message_template_name") or value.get("name") or "unknown"
        template_id = value.get("message_template_id") or value.get("id")
        event = value.get("event")
        new_category = value.get("new_category")
        reason = value.get("reason")
        quality = value.get("new_quality_score")

        logger.warning(
            "Meta Template Webhook Event: field=%s template=%s (id=%s) event=%s category=%s quality=%s reason=%s",
            field, template_name, template_id, event, new_category, quality, reason
        )

        alert_triggered = False
        alert_reason = None

        # Reclassification away from UTILITY can break utility sending policies & increase per-message costs
        if new_category and new_category.upper() != "UTILITY":
            alert_triggered = True
            alert_reason = f"Meta re-categorized template '{template_name}' from UTILITY to {new_category.upper()}."
            logger.error("🚨 CRITICAL META ALERT: %s", alert_reason)

        # Template disabled or paused by Meta due to recipient negative feedback
        if event in ("PAUSED", "DISABLED", "REJECTED"):
            alert_triggered = True
            alert_reason = f"Meta {event} template '{template_name}'. Reason: {reason or 'Recipient negative feedback threshold reached'}."
            logger.error("🚨 CRITICAL META ALERT: %s", alert_reason)

        if alert_triggered:
            pause_template(template_name, alert_reason or f"Meta {event}")
            founder_phone = getattr(settings, "founder_phone", "2347065015924")
            if founder_phone:
                alert_text = (
                    f"🚨 *META TEMPLATE STATUS ALERT*\n\n"
                    f"Template: `{template_name}` (ID: {template_id})\n"
                    f"Event: {event or 'CATEGORY_UPDATE'}\n"
                    f"Details: {alert_reason}\n"
                    f"Action: Template has been automatically paused."
                )
                try:
                    await self.whatsapp.send_text(founder_phone, alert_text)
                except Exception as exc:
                    logger.warning("Failed to send founder alert for Meta webhook: %s", exc)

        return {
            "template_name": template_name,
            "template_id": template_id,
            "field": field,
            "event": event,
            "new_category": new_category,
            "quality": quality,
            "alert_triggered": alert_triggered,
            "alert_reason": alert_reason,
            "received_at": datetime.now(UTC).isoformat(),
        }
