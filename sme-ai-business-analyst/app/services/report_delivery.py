import logging
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import async_session_factory
from app.models.business import Business
from app.models.task import Task
from app.models.user import User
from app.services.ledger_service import LedgerService
from app.services.report_service import ReportService
from app.services.whatsapp_client import WhatsAppClient
from app.services.visual_reports import VisualReportService
from app.services.magic_link_service import MagicLinkService
from app.services.task_service import TaskService

logger = logging.getLogger(__name__)


class ReportDeliveryService:
    def __init__(
        self,
        reports: ReportService | None = None,
        whatsapp: WhatsAppClient | None = None,
        ledger: LedgerService | None = None,
        visual_reports: VisualReportService | None = None,
        magic_links: MagicLinkService | None = None,
        tasks: TaskService | None = None,
    ) -> None:
        self.reports = reports or ReportService()
        self.whatsapp = whatsapp or WhatsAppClient()
        self.ledger = ledger or LedgerService()
        self.visual_reports = visual_reports or VisualReportService()
        self.magic_links = magic_links or MagicLinkService()
        self.tasks = tasks or TaskService()

    async def send_daily_reports(self, business_id: UUID | None = None) -> int:
        return await self._send_reports("daily", business_id=business_id)

    async def send_weekly_reports(self, business_id: UUID | None = None) -> int:
        return await self._send_reports("weekly", business_id=business_id)

    async def _resolve_task_recipient(
        self, db: AsyncSession, task: Task, business: Business
    ) -> tuple[str, bool]:
        """Resolve recipient phone and determine if within 24-hour WhatsApp customer care window."""
        recipient_phone = business.phone_number
        if getattr(task, "user_id", None):
            task_user = await db.get(User, task.user_id)
            if task_user and task_user.phone_number:
                recipient_phone = task_user.phone_number

        from app.models.message import WhatsAppMessage
        last_inbound_stmt = (
            select(WhatsAppMessage)
            .where(
                WhatsAppMessage.from_phone == recipient_phone,
                WhatsAppMessage.direction == "inbound",
            )
            .order_by(WhatsAppMessage.created_at.desc())
            .limit(1)
        )
        res_inbound = await db.execute(last_inbound_stmt)
        last_inbound = None
        if hasattr(res_inbound, "scalar_one_or_none"):
            cand = res_inbound.scalar_one_or_none()
            if isinstance(cand, WhatsAppMessage):
                last_inbound = cand

        now = datetime.now(UTC)
        is_within_24h = True
        if last_inbound and hasattr(last_inbound, "created_at") and last_inbound.created_at:
            msg_time = (
                last_inbound.created_at.replace(tzinfo=UTC)
                if last_inbound.created_at.tzinfo is None
                else last_inbound.created_at
            )
            is_within_24h = (now - msg_time) <= timedelta(hours=24)
        return recipient_phone, is_within_24h

    async def send_task_reminders(self) -> int:
        sent_count = 0
        from datetime import timedelta
        from app.utils.reminder_parser import format_confirmation_time

        async with async_session_factory() as db:
            # 1. First-time reminder dispatches
            tasks_to_remind = await self.tasks.get_reminders_to_send(db)
            for task in tasks_to_remind:
                try:
                    # Atomically claim task reminder to prevent double-send across concurrent runs/restarts
                    claimed = await self.tasks.mark_reminder_sent(
                        db, task.id, is_alarm_mode=bool(getattr(task, "is_alarm_mode", False))
                    )
                    if not claimed:
                        continue

                    business = await db.get(Business, task.business_id)
                    if not business:
                        continue

                    recipient_phone, is_within_24h = await self._resolve_task_recipient(db, task, business)

                    # Check if missed while app was down (>15 minutes overdue)
                    now = datetime.now(UTC)
                    task_due = task.due_at.replace(tzinfo=UTC) if task.due_at.tzinfo is None else task.due_at
                    is_late = bool(task_due and (now - task_due) > timedelta(minutes=15))

                    if is_late:
                        formatted_due = format_confirmation_time(task_due, settings.local_timezone)
                        message = f"⏰ LATE REMINDER (was due {formatted_due}): {task.title}"
                    else:
                        message = f"⏰ REMINDER: {task.title}"

                    if task.description:
                        message += f"\n\n{task.description}"

                    if is_within_24h:
                        if getattr(task, "is_alarm_mode", False):
                            result = await self.whatsapp.send_alarm_reminder(
                                recipient_phone, message, task_id=str(task.id)
                            )
                        else:
                            result = await self.whatsapp.send_text(recipient_phone, message)
                    else:
                        # Outside 24h window: dispatch approved Meta Utility Template
                        formatted_time_val = format_confirmation_time(task_due, settings.local_timezone)
                        template_components = [
                            {
                                "type": "body",
                                "parameters": [
                                    {"type": "text", "text": task.title},
                                    {"type": "text", "text": formatted_time_val},
                                ],
                            }
                        ]
                        result = await self.whatsapp.send_template(
                            recipient_phone,
                            template_name="task_reminder_alert_v1",
                            language_code="en",
                            components=template_components,
                        )

                    sent_count += 1
                    await self.ledger.record_outbound_message(
                        db, business.id, recipient_phone, message, result
                    )
                    await db.commit()
                except Exception:
                    await db.rollback()
                    logger.exception("Failed to send reminder for task %s", task.id)

            # 2. Repeating alarm dispatches
            alarm_repeats = []
            if hasattr(self.tasks, "get_alarm_repeats_to_send"):
                repeats_call = self.tasks.get_alarm_repeats_to_send(db)
                if hasattr(repeats_call, "__await__"):
                    alarm_repeats = await repeats_call
                elif isinstance(repeats_call, list):
                    alarm_repeats = repeats_call
            for task in alarm_repeats:
                try:
                    is_final = bool(task.repeat_count + 1 >= task.max_repeats)
                    claimed = await self.tasks.claim_alarm_repeat(
                        db,
                        task.id,
                        current_repeat_count=task.repeat_count,
                        expected_last_sent=task.last_repeat_sent_at,
                        is_final=is_final,
                    )
                    if not claimed:
                        continue

                    business = await db.get(Business, task.business_id)
                    if not business:
                        continue

                    recipient_phone, is_within_24h = await self._resolve_task_recipient(db, task, business)

                    if is_final:
                        stop_msg = f"⏰ I'll stop reminding you about this now — reply to reschedule: '{task.title}'"
                        if is_within_24h:
                            result = await self.whatsapp.send_text(recipient_phone, stop_msg)
                        else:
                            template_components = [
                                {
                                    "type": "body",
                                    "parameters": [
                                        {"type": "text", "text": f"Stopped: {task.title}"},
                                        {"type": "text", "text": "Reply to reschedule"},
                                    ],
                                }
                            ]
                            result = await self.whatsapp.send_template(
                                recipient_phone,
                                template_name="task_reminder_alert_v1",
                                language_code="en",
                                components=template_components,
                            )
                        sent_count += 1
                        await self.ledger.record_outbound_message(
                            db, business.id, recipient_phone, stop_msg, result
                        )
                        await db.commit()
                        continue

                    # Resend reminder text with buttons
                    message = f"⏰ REMINDER: {task.title}"
                    if task.description:
                        message += f"\n\n{task.description}"

                    if is_within_24h:
                        result = await self.whatsapp.send_alarm_reminder(
                            recipient_phone, message, task_id=str(task.id)
                        )
                    else:
                        task_due = task.due_at.replace(tzinfo=UTC) if task.due_at.tzinfo is None else task.due_at
                        formatted_time_val = format_confirmation_time(task_due, settings.local_timezone)
                        template_components = [
                            {
                                "type": "body",
                                "parameters": [
                                    {"type": "text", "text": task.title},
                                    {"type": "text", "text": formatted_time_val},
                                ],
                            }
                        ]
                        result = await self.whatsapp.send_template(
                            recipient_phone,
                            template_name="task_reminder_alert_v1",
                            language_code="en",
                            components=template_components,
                        )

                    sent_count += 1
                    await self.ledger.record_outbound_message(
                        db, business.id, recipient_phone, message, result
                    )
                    await db.commit()
                except Exception:
                    await db.rollback()
                    logger.exception("Failed to send repeat reminder for alarm task %s", task.id)
        return sent_count

    async def _send_reports(self, report_type: str, business_id: UUID | None = None) -> int:
        sent_count = 0
        async with async_session_factory() as db:
            query = select(Business).where(Business.deleted_at.is_(None))
            if business_id:
                query = query.where(Business.id == business_id)
            query = query.order_by(Business.created_at)
            businesses = (await db.execute(query)).scalars().all()
            for business in businesses:
                try:
                    if report_type == "daily":
                        report = await self.reports.generate_daily_report(db, business.id)
                    else:
                        report = await self.reports.generate_weekly_report(db, business.id)

                    # Send text summary
                    result = await self.whatsapp.send_text(business.phone_number, report.body)
                    outbound = await self.ledger.record_outbound_message(
                        db, business.id, business.phone_number, report.body, result
                    )
                    
                    # For weekly reports, send extra visuals
                    if report_type == "weekly" and not result.error_message:
                        # 1. Chart
                        chart_bytes = await self.visual_reports.generate_weekly_chart(db, business.id)
                        if chart_bytes:
                            media_id = await self.whatsapp.upload_media(chart_bytes, "image/png")
                            if media_id:
                                await self.whatsapp.send_image(business.phone_number, media_id, "Your Weekly Performance Chart")
                        
                        # 2. PDF Report
                        pdf_bytes = await self.visual_reports.generate_pdf_report(db, business.id, business.name)
                        if pdf_bytes:
                            media_id = await self.whatsapp.upload_media(pdf_bytes, "application/pdf")
                            if media_id:
                                await self.whatsapp.send_document(business.phone_number, media_id, f"Report_{business.name}.pdf", "Detailed Business Report")
                        
                        # 3. Magic Link
                        token = await self.magic_links.create_link(db, business.id)
                        base_url = settings.app_base_url.rstrip("/")
                        if not base_url.startswith("http://") and not base_url.startswith("https://"):
                            base_url = f"https://{base_url}"
                        link_message = f"View your interactive dashboard here (valid for 2 days): {base_url}/reports/dashboard/{token}"
                        await self.whatsapp.send_text(business.phone_number, link_message)

                    report.whatsapp_message_id = outbound.id
                    report.status = "sent" if not result.error_message else "failed"
                    report.sent_at = datetime.now(UTC) if report.status == "sent" else None
                    sent_count += 1 if report.status == "sent" or result.skipped else 0
                    await db.commit()
                except Exception:
                    await db.rollback()
                    logger.exception(
                        "Failed to send %s report for business %s",
                        report_type,
                        business.id,
                    )
        return sent_count
