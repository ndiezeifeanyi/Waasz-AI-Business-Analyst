import logging
from datetime import UTC, datetime
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.core.config import settings
from app.core.database import async_session_factory
from app.services.memory_service import MemoryService
from app.services.report_delivery import ReportDeliveryService
from app.services.unified_report_service import UnifiedReportService

logger = logging.getLogger(__name__)


class ReportScheduler:
    def __init__(
        self,
        delivery: ReportDeliveryService | None = None,
        memory: MemoryService | None = None,
        unified_reports: UnifiedReportService | None = None,
    ) -> None:
        self.delivery = delivery or ReportDeliveryService()
        self.memory = memory or MemoryService()
        self.unified_reports = unified_reports or UnifiedReportService()
        self.scheduler = AsyncIOScheduler(timezone=settings.local_timezone)
        self.last_reminder_check_at: datetime | None = None
        self.last_reminder_sent_count: int = 0
        self.reminder_check_count: int = 0
        self.last_run_times: dict[str, str] = {}

    async def run_task_reminders_job(self) -> int:
        """Periodic job (every 30s): check and dispatch due task reminders."""
        now_utc = datetime.now(UTC)
        self.last_reminder_check_at = now_utc
        self.last_run_times["send_task_reminders"] = now_utc.isoformat()
        self.reminder_check_count += 1
        try:
            sent = await self.delivery.send_task_reminders()
            self.last_reminder_sent_count = sent
            logger.info(
                "Task reminder tick #%d: checked at %s (dispatched=%d)",
                self.reminder_check_count,
                now_utc.isoformat(),
                sent,
            )
            return sent
        except Exception:
            logger.exception("Task reminder tick #%d failed at %s", self.reminder_check_count, now_utc.isoformat())
            return 0

    def get_status(self) -> dict:
        """Return a lightweight health & visibility status snapshot for monitoring."""
        is_running = bool(self.scheduler.running) if hasattr(self.scheduler, "running") else False
        jobs = []
        try:
            for job in self.scheduler.get_jobs():
                jobs.append({
                    "id": job.id,
                    "next_run_time": job.next_run_time.isoformat() if getattr(job, "next_run_time", None) else None,
                    "last_run_time": self.last_run_times.get(job.id),
                })
        except Exception as exc:
            logger.warning("Failed to collect scheduler jobs for status snapshot: %s", exc)
        return {
            "running": is_running,
            "last_reminder_check_at": self.last_reminder_check_at.isoformat() if self.last_reminder_check_at else None,
            "last_reminder_sent_count": self.last_reminder_sent_count,
            "reminder_check_count": self.reminder_check_count,
            "last_run_times": self.last_run_times,
            "jobs_count": len(jobs),
            "jobs": jobs,
        }

    async def run_compaction_job(self) -> None:
        """Periodic job: compact idle conversations (>30 min idle, >=10 uncompacted turns)."""
        self.last_run_times["compact_idle_user_conversations"] = datetime.now(UTC).isoformat()
        logger.info("Running scheduled memory compaction job...")
        async with async_session_factory() as db:
            compacted = await self.memory.compact_idle_conversations(db)
            logger.info("Compacted idle conversations for %d users", compacted)

    async def run_retention_purge_job(self) -> None:
        """Daily job: purge records exceeding user data_retention_days."""
        self.last_run_times["purge_expired_user_data"] = datetime.now(UTC).isoformat()
        logger.info("Running daily data retention purge job...")
        async with async_session_factory() as db:
            purged = await self.memory.purge_expired_user_data(db)
            logger.info("Purged %d expired records based on retention policy", purged)

    async def run_reembedding_job(self) -> int:
        """Periodic job (hourly): re-embed any knowledge chunks flagged with needs_reembedding=True."""
        now_utc = datetime.now(UTC)
        self.last_run_times["reembed_flagged_knowledge_chunks"] = now_utc.isoformat()
        logger.info("Running scheduled knowledge chunk re-embedding check...")
        try:
            from app.services.knowledge_service import KnowledgeService
            knowledge_service = KnowledgeService()
            async with async_session_factory() as db:
                count = await knowledge_service.reembed_flagged_chunks(db)
                if count > 0:
                    logger.info("Automatically re-embedded %d flagged knowledge chunks", count)
                return count
        except Exception:
            logger.exception("Scheduled knowledge re-embedding job failed")
            return 0

    async def run_daily_unified_reports(self) -> None:
        """Daily job: dispatch personalized daily reports across all user niches."""
        self.last_run_times["dispatch_daily_unified_reports"] = datetime.now(UTC).isoformat()
        logger.info("Dispatching scheduled daily unified reports across all niches...")
        async with async_session_factory() as db:
            sent = await self.unified_reports.dispatch_scheduled_reports(db, cadence="daily")
            logger.info("Dispatched %d daily reports", sent)

    async def run_weekly_unified_reports(self) -> None:
        """Weekly job: dispatch personalized weekly reports & roadmaps across all user niches."""
        self.last_run_times["dispatch_weekly_unified_reports"] = datetime.now(UTC).isoformat()
        logger.info("Dispatching scheduled weekly unified reports across all niches...")
        async with async_session_factory() as db:
            sent = await self.unified_reports.dispatch_scheduled_reports(db, cadence="weekly")
            logger.info("Dispatched %d weekly reports", sent)

    async def run_monthly_unified_reports(self) -> None:
        """Monthly job: dispatch personalized monthly reports & roadmaps across all user niches."""
        self.last_run_times["dispatch_monthly_unified_reports"] = datetime.now(UTC).isoformat()
        logger.info("Dispatching scheduled monthly unified reports across all niches...")
        async with async_session_factory() as db:
            sent = await self.unified_reports.dispatch_scheduled_reports(db, cadence="monthly")
            logger.info("Dispatched %d monthly reports", sent)

    async def run_quarterly_unified_reports(self) -> None:
        """Quarterly job: dispatch personalized quarterly reports & roadmaps across all user niches."""
        self.last_run_times["dispatch_quarterly_unified_reports"] = datetime.now(UTC).isoformat()
        logger.info("Dispatching scheduled quarterly unified reports across all niches...")
        async with async_session_factory() as db:
            sent = await self.unified_reports.dispatch_scheduled_reports(db, cadence="quarterly")
            logger.info("Dispatched %d quarterly reports", sent)

    async def run_yearly_unified_reports(self) -> None:
        """Yearly job: dispatch personalized yearly reports & roadmaps across all user niches."""
        self.last_run_times["dispatch_yearly_unified_reports"] = datetime.now(UTC).isoformat()
        logger.info("Dispatching scheduled yearly unified reports across all niches...")
        async with async_session_factory() as db:
            sent = await self.unified_reports.dispatch_scheduled_reports(db, cadence="yearly")
            logger.info("Dispatched %d yearly reports", sent)

    async def run_monthly_google_drive_backups(self) -> dict:
        """Monthly scheduled job: for each business with Google Drive connected, export monthly PDF and upload."""
        now_utc = datetime.now(UTC)
        self.last_run_times["dispatch_monthly_google_drive_backups"] = now_utc.isoformat()
        try:
            from app.services.google_drive_service import GoogleDriveService
            drive_svc = GoogleDriveService()
            async with async_session_factory() as session:
                return await drive_svc.run_all_monthly_backups(session)
        except Exception as exc:
            logger.exception("Monthly Google Drive backup job failed at %s: %s", now_utc.isoformat(), exc)
            return {"status": "error", "error": str(exc)}

    async def run_daily_inactivity_nudges(self) -> int:
        """Daily job (6pm local time): send gentle nudge to users with no activity today."""
        now_utc = datetime.now(UTC)
        self.last_run_times["dispatch_daily_inactivity_nudges"] = now_utc.isoformat()
        logger.info("Running daily 6pm inactivity nudge check...")
        try:
            from app.services.nudge_service import InactivityNudgeService
            nudge_svc = InactivityNudgeService()
            async with async_session_factory() as db:
                sent = await nudge_svc.dispatch_daily_inactivity_nudges(db, now_override=now_utc)
                logger.info("Dispatched %d 6pm inactivity nudges", sent)
                return sent
        except Exception as exc:
            logger.exception("Daily inactivity nudge job failed: %s", exc)
            return 0

    def start(self) -> None:
        self.scheduler.add_job(
            self.run_daily_inactivity_nudges,
            CronTrigger(hour=18, minute=0, timezone=settings.local_timezone),
            id="dispatch_daily_inactivity_nudges",
            replace_existing=True,
        )
        self.scheduler.add_job(
            self.run_daily_unified_reports,
            CronTrigger(hour=settings.daily_report_hour_local),
            id="dispatch_daily_unified_reports",
            replace_existing=True,
        )
        self.scheduler.add_job(
            self.run_weekly_unified_reports,
            CronTrigger(
                day_of_week=settings.weekly_report_weekday,
                hour=settings.daily_report_hour_local,
            ),
            id="dispatch_weekly_unified_reports",
            replace_existing=True,
        )
        self.scheduler.add_job(
            self.run_monthly_unified_reports,
            CronTrigger(
                day=1,
                hour=settings.daily_report_hour_local,
            ),
            id="dispatch_monthly_unified_reports",
            replace_existing=True,
        )
        self.scheduler.add_job(
            self.run_monthly_google_drive_backups,
            CronTrigger(
                day=1,
                hour=settings.daily_report_hour_local,
            ),
            id="dispatch_monthly_google_drive_backups",
            replace_existing=True,
        )
        self.scheduler.add_job(
            self.run_quarterly_unified_reports,
            CronTrigger(
                month="1,4,7,10",
                day=1,
                hour=settings.daily_report_hour_local,
            ),
            id="dispatch_quarterly_unified_reports",
            replace_existing=True,
        )
        self.scheduler.add_job(
            self.run_yearly_unified_reports,
            CronTrigger(
                month=1,
                day=1,
                hour=settings.daily_report_hour_local,
            ),
            id="dispatch_yearly_unified_reports",
            replace_existing=True,
        )
        self.scheduler.add_job(
            self.run_task_reminders_job,
            "interval",
            seconds=30,
            id="send_task_reminders",
            replace_existing=True,
        )
        # Memory compaction every 15 minutes
        self.scheduler.add_job(
            self.run_compaction_job,
            "interval",
            minutes=15,
            id="compact_idle_user_conversations",
            replace_existing=True,
        )
        # Daily retention purge at 03:00 UTC
        self.scheduler.add_job(
            self.run_retention_purge_job,
            CronTrigger(hour=3, minute=0, timezone="UTC"),
            id="purge_expired_user_data",
            replace_existing=True,
        )
        # Knowledge chunk re-embedding every 1 hour
        self.scheduler.add_job(
            self.run_reembedding_job,
            "interval",
            hours=1,
            id="reembed_flagged_knowledge_chunks",
            replace_existing=True,
        )
        # Refresh dynamic model discovery every 24 hours
        async def _refresh_models_job():
            try:
                self.last_run_times["refresh_resolved_models"] = datetime.now(UTC).isoformat()
                from app.core.model_resolver import model_resolver
                await model_resolver.refresh_all()
            except Exception as exc:
                logger.warning("Scheduled model resolution refresh failed: %s", exc)

        self.scheduler.add_job(
            _refresh_models_job,
            "interval",
            hours=24,
            id="refresh_resolved_models",
            replace_existing=True,
        )
        self.scheduler.start()
