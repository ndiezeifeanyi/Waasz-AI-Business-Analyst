#!/usr/bin/env python3
"""
Application startup verification and health check.
Validates configuration, database connectivity, and required services.
Run this before deploying to ensure everything is properly configured.
"""

import asyncio
import logging
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.auth import validate_secret_key_configured
from app.core.config import settings
from app.core.database import check_db_connection

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)-8s [%(name)s] %(message)s",
)
logger = logging.getLogger(__name__)


class StartupVerifier:
    """Verifies application is ready for deployment."""

    def __init__(self):
        self.checks = []
        self.failures = []

    def add_check(self, name: str, result: bool, details: str = ""):
        """Record a check result."""
        status = "✓ PASS" if result else "✗ FAIL"
        self.checks.append((name, result, details))
        logger.info(f"{status}: {name}" + (f" - {details}" if details else ""))
        if not result:
            self.failures.append(name)

    async def run(self) -> bool:
        """Run all verification checks."""
        logger.info("=" * 70)
        logger.info("SME AI Business Analyst Platform - Startup Verification")
        logger.info("=" * 70)

        # Environment checks
        logger.info("\n🔍 Environment Configuration:")
        self._check_app_environment()

        # Security checks
        logger.info("\n🔒 Security Configuration:")
        self._check_security_config()

        # Database checks
        logger.info("\n💾 Database Configuration:")
        await self._check_database_config()

        # AI Provider checks
        logger.info("\n🤖 AI Provider Configuration:")
        self._check_ai_providers()

        # Dynamic Model Resolution checks
        logger.info("\n🔄 Dynamic Model Resolution:")
        await self._check_model_resolution()

        # Background Scheduler checks
        logger.info("\n⏱️ Consolidated Background Scheduler:")
        self._check_scheduler_configuration()

        # WhatsApp checks
        logger.info("\n💬 WhatsApp Configuration:")
        self._check_whatsapp_config()

        # Print summary
        logger.info("\n" + "=" * 70)
        self._print_summary()

        return len(self.failures) == 0

    def _check_app_environment(self):
        """Check application environment configuration."""
        self.add_check(
            "Environment set",
            settings.app_env in ("local", "test", "staging", "production"),
            f"APP_ENV={settings.app_env}",
        )
        
        self.add_check(
            "Debug mode disabled in production",
            not (settings.app_debug and settings.app_env == "production"),
            f"APP_DEBUG={settings.app_debug}",
        )
        
        self.add_check(
            "Base URL configured",
            bool(settings.app_base_url),
            f"APP_BASE_URL={settings.app_base_url}",
        )

    def _check_security_config(self):
        """Check security configuration."""
        try:
            validate_secret_key_configured()
            secret_ok = True
            secret_msg = "SECRET_KEY configured"
        except ValueError as exc:
            secret_ok = False
            secret_msg = str(exc)

        self.add_check("Secret key configured", secret_ok, secret_msg)
        
        self.add_check(
            "CORS origins configured",
            bool(settings.cors_origins),
            f"CORS_ORIGINS={settings.cors_origins}",
        )
        
        self.add_check(
            "Allowed hosts configured",
            bool(settings.allowed_hosts),
            f"ALLOWED_HOSTS={settings.allowed_hosts}",
        )

    async def _check_database_config(self):
        """Check database configuration and connectivity."""
        self.add_check(
            "Database URL configured",
            bool(settings.database_url) and settings.database_url != "sqlite:///:memory:",
            f"DATABASE_URL={settings.database_url[:30]}...",
        )
        
        # Try to connect
        try:
            connected = await check_db_connection()
            self.add_check(
                "Database connection successful",
                connected,
                "Connected to PostgreSQL",
            )
        except Exception as exc:
            self.add_check(
                "Database connection successful",
                False,
                f"Connection failed: {exc}",
            )

    def _check_ai_providers(self):
        """Check AI provider configuration."""
        has_gemini = bool(settings.gemini_api_key)
        has_groq = bool(settings.groq_api_key)
        has_openai = bool(settings.openai_api_key)

        self.add_check(
            "At least one AI provider configured",
            has_gemini or has_groq or has_openai,
            f"Gemini: {has_gemini}, Groq: {has_groq}, OpenAI: {has_openai}",
        )

        if has_gemini:
            self.add_check("Gemini API key set", True, "✓")
        if has_groq:
            self.add_check("Groq API key set", True, "✓")
        if has_openai:
            self.add_check("OpenAI API key set", True, "✓")

    async def _check_model_resolution(self):
        """Verify dynamic model resolution across providers and capabilities."""
        try:
            from app.core.model_resolver import model_resolver
            await model_resolver.refresh_all()
            gemini_chat = model_resolver.get_model("gemini", "chat")
            gemini_vision = model_resolver.get_model("gemini", "vision")
            groq_chat = model_resolver.get_model("groq", "chat")
            groq_audio = model_resolver.get_model("groq", "audio")

            self.add_check("Gemini Chat model resolved", bool(gemini_chat), gemini_chat)
            self.add_check("Gemini Vision model resolved", bool(gemini_vision), gemini_vision)
            self.add_check("Groq Chat model resolved", bool(groq_chat), groq_chat)
            self.add_check("Groq Audio model resolved", bool(groq_audio), groq_audio)
        except Exception as exc:
            self.add_check("Dynamic Model Resolution check", False, str(exc))

    def _check_scheduler_configuration(self):
        """Verify ReportScheduler registers all 8 required background jobs."""
        try:
            from app.services.scheduler import ReportScheduler
            scheduler = ReportScheduler()
            # Start scheduler (without blocking loop) to inspect registered jobs
            import unittest.mock
            with unittest.mock.patch.object(scheduler.scheduler, "start"):
                scheduler.start()

            jobs = scheduler.scheduler.get_jobs()
            job_ids = {j.id for j in jobs}

            expected = [
                "send_task_reminders",
                "reembed_flagged_knowledge_chunks",
                "compact_idle_user_conversations",
                "purge_expired_user_data",
                "refresh_resolved_models",
                "dispatch_daily_unified_reports",
                "dispatch_weekly_unified_reports",
                "dispatch_monthly_unified_reports",
            ]

            all_present = all(j in job_ids for j in expected)
            self.add_check(
                "Background Scheduler jobs registered",
                all_present,
                f"{len(jobs)}/8 jobs present: {', '.join(job_ids)}",
            )
        except Exception as exc:
            self.add_check("Background Scheduler configuration", False, str(exc))

    def _check_whatsapp_config(self):
        """Check WhatsApp configuration."""
        if settings.app_env in ("staging", "production"):
            required = [
                ("WHATSAPP_VERIFY_TOKEN", settings.whatsapp_verify_token),
                ("WHATSAPP_APP_SECRET", settings.whatsapp_app_secret),
                ("WHATSAPP_ACCESS_TOKEN", settings.whatsapp_access_token),
                ("WHATSAPP_PHONE_NUMBER_ID", settings.whatsapp_phone_number_id),
            ]

            for name, value in required:
                self.add_check(f"{name} set", bool(value), "✓" if value else "MISSING")
        else:
            logger.info("ℹ️  WhatsApp setup not required in development")

    def _print_summary(self):
        """Print verification summary."""
        total = len(self.checks)
        passed = total - len(self.failures)
        failed = len(self.failures)

        logger.info(f"\nResults: {passed}/{total} checks passed")

        if self.failures:
            logger.error(f"\n❌ {failed} check(s) failed:")
            for failure in self.failures:
                logger.error(f"  - {failure}")
            return False
        else:
            logger.info("\n✅ All checks passed! Application is ready for deployment.")
            return True


async def main():
    """Run startup verification."""
    verifier = StartupVerifier()
    success = await verifier.run()

    if not success:
        logger.error("\n⚠️  Please fix the issues above before deploying.")
        sys.exit(1)
    else:
        logger.info("\n🚀 Ready to deploy!")
        sys.exit(0)


if __name__ == "__main__":
    asyncio.run(main())
