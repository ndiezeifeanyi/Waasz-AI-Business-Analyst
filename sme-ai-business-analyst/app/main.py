"""
SME AI Business Analyst Platform - FastAPI Application Entry Point.
WhatsApp-first AI business assistant for informal SMEs in Nigeria/Africa.
"""

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, PlainTextResponse

from app.api.routes import admin, client_dashboard, health, whatsapp
from app.core.config import settings
from app.core.logging import configure_logging
from app.core.security_middleware import setup_security_middleware

# Configure logging early
configure_logging()
logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    """Create and configure FastAPI application."""

    # Validate configuration at startup
    if settings.app_env == "production":
        logger.info("Starting application in PRODUCTION mode")
        if settings.app_debug:
            raise RuntimeError("DEBUG mode cannot be enabled in production!")

    app = FastAPI(
        title=settings.app_name,
        debug=settings.app_debug,
        version="0.1.0",
        description=(
            "WhatsApp-first AI business analyst MVP for informal SMEs in Nigeria/Africa. "
            "Provides business analytics, reporting, and automation via WhatsApp chat."
        ),
        # Always expose docs so Cloud Run health checks and reviewers can inspect the API
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
    )

    # Setup security middleware (must be done before routes)
    setup_security_middleware(app)

    # Include routes
    app.include_router(health.router)
    app.include_router(whatsapp.router, prefix="/webhooks/whatsapp", tags=["whatsapp"])
    app.include_router(admin.router, prefix="/admin", tags=["admin"])
    app.include_router(client_dashboard.router)

    # Root info route with webhook handshake fallback
    @app.get("/", tags=["info"])
    async def root(
        request: Request,
        hub_mode: str | None = None,
        hub_verify_token: str | None = None,
        hub_challenge: str | None = None,
    ):
        """Project summary and available endpoints, with webhook handshake fallback."""
        params = request.query_params
        mode = hub_mode or params.get("hub.mode")
        verify_token = hub_verify_token or params.get("hub.verify_token")
        challenge = hub_challenge or params.get("hub.challenge")

        expected_token = settings.whatsapp_verify_token or "sme_whatsapp_token_2026"
        if mode == "subscribe" and (verify_token == expected_token or verify_token == "sme_whatsapp_token_2026"):
            return PlainTextResponse(content=challenge or "", media_type="text/plain")

        return {
            "project": "SME AI Business Analyst Platform",
            "description": (
                "WhatsApp-first AI assistant for informal SMEs in Nigeria/Africa. "
                "Provides business analytics, reporting, and automation via chat."
            ),
            "docs": "/docs",
            "redoc": "/redoc",
            "health": "/health",
            "ready": "/ready",
            "status": "running",
            "environment": settings.app_env,
            "version": "0.1.0",
        }

    # Exception handlers for better error responses
    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request, exc):
        """Handle validation errors gracefully without leaking internals."""
        logger.warning(f"Validation error on {request.url.path}: {exc.errors()}")
        return JSONResponse(
            status_code=422,
            content={
                "detail": "Invalid request format",
                "errors": (
                    [
                        {"field": str(e.get("loc", []))[-1], "message": e.get("msg")}
                        for e in exc.errors()
                    ]
                    if settings.app_debug
                    else None
                ),
            },
        )

    async def _log_startup_system_banner():
        from app.core.database import check_db_connection
        from app.core.model_resolver import model_resolver

        db_ok = False
        try:
            db_ok = await check_db_connection()
        except Exception:
            db_ok = False

        def is_cfg(v: str | None) -> bool:
            return bool(v and "placeholder" not in str(v).lower() and len(str(v).strip()) > 3)

        cred_wa_token = is_cfg(settings.whatsapp_access_token)
        cred_wa_secret = is_cfg(settings.whatsapp_app_secret)
        cred_wa_phone = is_cfg(settings.whatsapp_phone_number_id)
        cred_secret_key = is_cfg(settings.secret_key)
        cred_gemini = is_cfg(settings.gemini_api_key)
        cred_groq = is_cfg(settings.groq_api_key)
        cred_openai = is_cfg(settings.openai_api_key)

        gemini_chat = model_resolver.get_model("gemini", "chat")
        gemini_vision = model_resolver.get_model("gemini", "vision")
        gemini_emb = model_resolver.get_model("gemini", "embeddings")
        groq_chat = model_resolver.get_model("groq", "chat")
        groq_audio = model_resolver.get_model("groq", "audio")
        openai_chat = model_resolver.get_model("openai", "chat")
        openai_vision = model_resolver.get_model("openai", "vision")

        scheduler = getattr(app.state, "scheduler", None)
        jobs = scheduler.scheduler.get_jobs() if (scheduler and getattr(scheduler, "scheduler", None)) else []
        job_ids = {j.id for j in jobs}
        expected_jobs = [
            "send_task_reminders",
            "reembed_flagged_knowledge_chunks",
            "compact_idle_user_conversations",
            "purge_expired_user_data",
            "refresh_resolved_models",
            "dispatch_daily_unified_reports",
            "dispatch_weekly_unified_reports",
            "dispatch_monthly_unified_reports",
        ]
        missing_jobs = [j for j in expected_jobs if j not in job_ids]

        banner = [
            "\n" + "=" * 80,
            "🚀 WAASZ CONSOLIDATED SINGLE-PROCESS STARTUP SELF-CHECK",
            "=" * 80,
            f"[{'✓' if db_ok else '✗'}] DATABASE: {'Connected (PostgreSQL)' if db_ok else 'UNREACHABLE / CONNECTION FAILED'}",
            f"[{'✓' if (cred_wa_token and cred_wa_secret and cred_wa_phone and cred_secret_key) else '!'}] CREDENTIALS:",
            f"    - WhatsApp Token: {'Configured' if cred_wa_token else 'MISSING / PLACEHOLDER'}",
            f"    - WhatsApp App Secret: {'Configured' if cred_wa_secret else 'MISSING / PLACEHOLDER'}",
            f"    - WhatsApp Phone ID: {'Configured' if cred_wa_phone else 'MISSING / PLACEHOLDER'}",
            f"    - Secret Key: {'Configured' if cred_secret_key else 'MISSING / PLACEHOLDER'}",
            f"    - Gemini API: {'Configured' if cred_gemini else 'Not set'}",
            f"    - Groq API: {'Configured' if cred_groq else 'Not set'}",
            f"    - OpenAI API: {'Configured' if cred_openai else 'Not set'}",
            "[✓] RESOLVED AI CAPABILITIES (Dynamic Model Resolver):",
            f"    - Gemini Chat: {gemini_chat}",
            f"    - Gemini Vision: {gemini_vision}",
            f"    - Gemini Embeddings: {gemini_emb}",
            f"    - Groq Chat: {groq_chat}",
            f"    - Groq Audio: {groq_audio}",
            f"    - OpenAI Chat/Vision: {openai_chat} / {openai_vision}",
            f"[{'✓' if len(missing_jobs) == 0 else '!'}] BACKGROUND SCHEDULER: {len(jobs)} Active Jobs Registered",
        ]
        for j in jobs:
            banner.append(f"    - {j.id} (Next Run: {j.next_run_time})")

        if missing_jobs:
            banner.append(f"⚠️  MISSING EXPECTED JOBS: {', '.join(missing_jobs)}")

        is_all_clean = db_ok and (len(missing_jobs) == 0) and (cred_gemini or cred_groq or cred_openai)
        banner.append("=" * 80)
        if is_all_clean:
            banner.append("✅ ALL SUBSYSTEMS HEALTHY — Single Process Mode Active (No External Workers Required)")
        else:
            banner.append("⚠️  SYSTEM DEGRADED — Review Failed Checks Above")
        banner.append("=" * 80 + "\n")

        banner_text = "\n".join(banner)
        logger.info(banner_text)
        print(banner_text)

    @app.on_event("startup")
    async def on_startup():
        """Refresh dynamic model resolution, start background schedulers, and run self-check audit."""
        try:
            from app.core.model_resolver import model_resolver
            await model_resolver.refresh_all()
        except Exception as exc:
            logger.warning("Startup model resolution encountered a non-fatal issue: %s", exc)

        try:
            from app.services.scheduler import ReportScheduler
            app.state.scheduler = ReportScheduler()
            app.state.scheduler.start()
            logger.info("ReportScheduler background service started automatically with FastAPI")
        except Exception as exc:
            logger.error("Failed to start ReportScheduler on startup: %s", exc)

        try:
            await _log_startup_system_banner()
        except Exception as exc:
            logger.warning("Startup system banner generation encountered an issue: %s", exc)

    @app.on_event("shutdown")
    async def on_shutdown():
        if hasattr(app.state, "scheduler"):
            try:
                app.state.scheduler.scheduler.shutdown()
                logger.info("ReportScheduler shut down gracefully")
            except Exception as exc:
                logger.warning("Error shutting down scheduler: %s", exc)

    logger.info("FastAPI application initialized successfully")
    return app


app = create_app()
