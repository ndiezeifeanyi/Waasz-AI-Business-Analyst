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

    async def run_trial_lifecycle_check(self) -> int:
        """Periodic job (hourly): check businesses whose 14-day free trial has expired."""
        now_utc = datetime.now(UTC)
        self.last_run_times["check_trial_lifecycle"] = now_utc.isoformat()
        logger.info("Running scheduled trial lifecycle check at %s...", now_utc.isoformat())
        sent_count = 0
        try:
            from app.models.business import Business
            from app.models.user import User
            from app.models.transaction import Transaction
            from app.services.whatsapp_client import WhatsAppClient
            from sqlalchemy import func, select

            whatsapp = WhatsAppClient()
            async with async_session_factory() as db:
                stmt = select(Business)
                res = await db.execute(stmt)
                businesses = res.scalars().all()
                for biz in businesses:
                    b_settings = dict(biz.settings or {})
                    expires_str = b_settings.get("trial_expires_at")
                    already_notified = b_settings.get("trial_ended_notified", False)

                    if not expires_str or already_notified:
                        continue

                    try:
                        trial_expires = datetime.fromisoformat(expires_str)
                        if trial_expires.tzinfo is None:
                            trial_expires = trial_expires.replace(tzinfo=UTC)
                    except Exception:
                        continue

                    if now_utc >= trial_expires:
                        # Find owner user phone
                        u_stmt = select(User).where(User.business_id == biz.id).order_by(User.created_at.asc()).limit(1)
                        u_res = await db.execute(u_stmt)
                        owner_user = u_res.scalars().first()
                        if not owner_user or not owner_user.phone_number:
                            continue

                        # Calculate stats during the trial
                        tx_stmt = select(
                            func.count(Transaction.id).label("total_count"),
                            func.coalesce(
                                func.sum(Transaction.amount).filter(
                                    Transaction.transaction_type == "sale",
                                    Transaction.status == "confirmed",
                                ),
                                0,
                            ).label("total_sales"),
                        ).where(Transaction.business_id == biz.id)
                        tx_stats = (await db.execute(tx_stmt)).first()
                        total_tx = tx_stats.total_count if tx_stats else 0
                        total_sales = float(tx_stats.total_sales) if tx_stats else 0.0

                        summary_msg = (
                            f"🎉 *Your 14-Day Waasz Free Trial is Complete!*\n\n"
                            f"Here is what *{biz.name}* achieved on Waasz:\n"
                            f"• Total Sales Logged: ₦{total_sales:,.2f}\n"
                            f"• Total Transactions Recorded: {total_tx}\n"
                            f"• Automated Bookkeeping: 100% Paperless & Backed Up\n\n"
                            f"We hope Waasz has made managing your business effortless! "
                            f"To continue keeping your books, issuing receipts, and receiving real-time profit analytics, activate your subscription.\n\n"
                            f"💬 Reply *SUBSCRIBE* to keep your business running smoothly!"
                        )

                        await whatsapp.send_text(owner_user.phone_number, summary_msg)
                        b_settings["trial_ended_notified"] = True
                        b_settings["trial_status"] = "expired"
                        biz.settings = b_settings
                        sent_count += 1
                        logger.info("Sent 14-day trial completion notice to %s (%s)", biz.name, owner_user.phone_number)

                if sent_count > 0:
                    await db.commit()
            return sent_count
        except Exception as exc:
            logger.exception("Failed in trial lifecycle check: %s", exc)
            return 0

    async def run_backlogged_onboarding_dispatch(self) -> int:
        """
        One-off scheduled dispatch strictly for tomorrow (2026-10-09 at 9:00 AM WAT):
        Sends personalized responses to users previously turned down by private beta restriction,
        along with full 14-day trial welcome and shop setup flow.
        Cancels/aborts automatically if run beyond tomorrow (2026-10-09).
        """
        import zoneinfo
        from datetime import date
        try:
            wat_tz = zoneinfo.ZoneInfo(settings.local_timezone)
        except Exception:
            wat_tz = UTC
        now_local = datetime.now(wat_tz)

        # Strict constraint: only tomorrow (2026-10-09). Anything beyond gets cancelled!
        target_date = date(2026, 10, 9)
        if now_local.date() != target_date:
            logger.warning(
                "Backlogged onboarding dispatch cancelled: current date %s is not target date %s",
                now_local.date(),
                target_date,
            )
            return 0

        self.last_run_times["dispatch_backlogged_onboarding"] = now_local.isoformat()
        logger.info("Executing backlogged onboarding dispatch for target date %s at %s...", target_date, now_local.isoformat())

        from sqlalchemy import text
        from app.services.whatsapp_client import WhatsAppClient
        from app.services.ledger_service import LedgerService

        whatsapp = WhatsAppClient()
        ledger = LedgerService()
        sent_count = 0

        standard_body = (
            "\n\n📋 *How Waasz Works:*\n"
            "• Just text or send voice notes: \"Sold 2 bags of rice ₦70k\" or \"Bought fuel ₦15,000\"\n"
            "• Ask anytime: \"What is my profit today?\" or \"Who owes me money?\"\n"
            "• Get automated daily & weekly business summaries right here.\n\n"
            "🔒 *Data Privacy & Protection:*\n"
            "In full compliance with NDPA guidelines, your financial records are strictly private, encrypted, and never shared. We also support automated backups directly to your own Google Drive.\n\n"
            "What is the name of your shop or business? (e.g., 'Emeka Stores')\n\n"
            "*(Once you reply with your business name, we will also help you set up your store receipts so you can issue branded PDF receipts to your customers instantly!)*"
        )

        recipients = [
            {
                "phone": "2349129454869",
                "intro": (
                    "👋 Hello!\n\n"
                    "You recently reached out with a greeting while Waasz was in private testing. "
                    "We're excited to let you know that Waasz is now officially open, and you have been activated on a 14-Day Free Trial! 🎉"
                ),
            },
            {
                "phone": "2348141917654",
                "intro": (
                    "👋 Hello!\n\n"
                    "You recently sent us a message asking to try Waasz while our private testing gate was active. "
                    "Thank you so much for your patience—our platform is now officially open, and your 14-Day Free Trial is live! 🎉"
                ),
            },
            {
                "phone": "2348113179049",
                "intro": (
                    "👋 Hello!\n\n"
                    "You previously messaged us about tracking baking ingredient expenses and budgets for your bakery and catering business while we were in a restricted test. "
                    "We are thrilled to let you know that Waasz is now officially open, and your 14-Day Free Trial is active! 🎉"
                ),
            },
            {
                "phone": "2348165942327",
                "intro": (
                    "👋 Hello!\n\n"
                    "You previously asked what Waasz does when our private testing gate was active. "
                    "Waasz is your AI business manager right here on WhatsApp—helping Nigerian shop owners track daily sales, expenses, and customer debts without any paperwork. "
                    "We are now officially open, and your 14-Day Free Trial is live! 🎉"
                ),
            },
            {
                "phone": "2347031243018",
                "intro": (
                    "👋 Hello!\n\n"
                    "You asked what Waasz is about when our private testing was active. "
                    "Waasz is your AI business manager right here on WhatsApp—helping you track your daily sales, expenses, customer debts, and issue digital receipts 100% paperless. "
                    "We are now officially open, and your 14-Day Free Trial is live! 🎉"
                ),
            },
            {
                "phone": "2349161281648",
                "intro": (
                    "👋 Hello Nonso!\n\n"
                    "You previously chatted with us while Waasz was in private testing. "
                    "We're happy to let you know that Waasz is now officially open with a 14-Day Free Trial to track daily finances, business sales, expenses, and automated WhatsApp reminders! 🎉"
                ),
            },
        ]

        async with async_session_factory() as db:
            # Check idempotency guard
            check_res = await db.execute(
                text("SELECT value FROM system_settings WHERE key = 'backlogged_onboarding_sent_20261009'")
            )
            val = check_res.scalar_one_or_none()
            if val == "true":
                logger.info("Backlogged onboarding messages were already dispatched. Skipping.")
                return 0

            for r in recipients:
                phone = r["phone"]
                full_message = r["intro"] + standard_body
                try:
                    init_res = await ledger.get_or_create_business_and_user(db, phone)
                    if isinstance(init_res, tuple) and len(init_res) >= 2:
                        business, user = init_res[0], init_res[1]
                    else:
                        business, user = init_res

                    send_res = await whatsapp.send_text(phone, full_message)
                    await ledger.record_outbound_message(
                        db, business.id, phone, full_message, send_res, user_id=user.id
                    )
                    sent_count += 1
                    logger.info("Successfully dispatched backlogged onboarding to %s", phone)
                except Exception as exc:
                    logger.exception("Failed to dispatch backlogged onboarding to %s: %s", phone, exc)

            # Mark idempotency key so it will never run again
            await db.execute(
                text("""
                    INSERT INTO system_settings (key, value, updated_at)
                    VALUES ('backlogged_onboarding_sent_20261009', 'true', NOW())
                    ON CONFLICT (key) DO UPDATE SET value = 'true', updated_at = NOW();
                """)
            )
            await db.commit()

        logger.info("Finished backlogged onboarding dispatch: %d sent", sent_count)
        return sent_count

    def start(self) -> None:
        self.scheduler.add_job(
            self.run_daily_inactivity_nudges,
            CronTrigger(hour=18, minute=0, timezone=settings.local_timezone),
            id="dispatch_daily_inactivity_nudges",
            replace_existing=True,
        )
        self.scheduler.add_job(
            self.run_daily_unified_reports,
            CronTrigger(hour=settings.daily_report_hour_local, minute=0, timezone=settings.local_timezone),
            id="dispatch_daily_unified_reports",
            replace_existing=True,
        )
        self.scheduler.add_job(
            self.run_weekly_unified_reports,
            CronTrigger(
                day_of_week=settings.weekly_report_weekday,
                hour=settings.daily_report_hour_local,
                minute=0,
                timezone=settings.local_timezone,
            ),
            id="dispatch_weekly_unified_reports",
            replace_existing=True,
        )
        self.scheduler.add_job(
            self.run_monthly_unified_reports,
            CronTrigger(
                day=1,
                hour=settings.daily_report_hour_local,
                minute=0,
                timezone=settings.local_timezone,
            ),
            id="dispatch_monthly_unified_reports",
            replace_existing=True,
        )
        self.scheduler.add_job(
            self.run_monthly_google_drive_backups,
            CronTrigger(
                day=1,
                hour=settings.daily_report_hour_local,
                minute=0,
                timezone=settings.local_timezone,
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
                minute=0,
                timezone=settings.local_timezone,
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
                minute=0,
                timezone=settings.local_timezone,
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
        # Trial lifecycle check every 1 hour
        self.scheduler.add_job(
            self.run_trial_lifecycle_check,
            "interval",
            hours=1,
            id="check_trial_lifecycle",
            replace_existing=True,
        )
        # One-off backlogged onboarding dispatch: strictly for tomorrow (2026-10-09) at 9:00 AM WAT
        self.scheduler.add_job(
            self.run_backlogged_onboarding_dispatch,
            CronTrigger(
                year=2026,
                month=10,
                day=9,
                hour=9,
                minute=0,
                timezone=settings.local_timezone,
            ),
            id="dispatch_backlogged_onboarding_20261009",
            replace_existing=True,
        )
        self.scheduler.start()
