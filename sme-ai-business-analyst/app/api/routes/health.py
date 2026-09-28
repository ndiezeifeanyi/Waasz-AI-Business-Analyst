"""
Health check endpoints for monitoring and orchestration.
"""

import logging

from typing import Any
from fastapi import APIRouter, HTTPException, Request, status

from app.core.config import settings
from app.core.database import check_db_connection

logger = logging.getLogger(__name__)
router = APIRouter(tags=["health"])


@router.get("/health")
@router.get("/api/health")
async def health_check(request: Request) -> dict[str, Any]:
    """
    Basic health check including scheduler status snapshot.
    Returns 200 if application is running.
    """
    scheduler = getattr(request.app.state, "scheduler", None)
    scheduler_status = scheduler.get_status() if (scheduler and hasattr(scheduler, "get_status")) else None
    return {
        "status": "ok",
        "environment": settings.app_env,
        "version": "0.1.0",
        "scheduler": scheduler_status,
    }


@router.get("/health/scheduler")
@router.get("/api/health/scheduler")
async def scheduler_health_check(request: Request) -> dict[str, Any]:
    """
    Dedicated scheduler health check endpoint.
    Returns scheduler operational state, tick counts, timestamps, and active jobs.
    """
    scheduler = getattr(request.app.state, "scheduler", None)
    if not scheduler or not getattr(scheduler, "scheduler", None) or not scheduler.scheduler.running:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Scheduler is not running",
        )
    return scheduler.get_status()


@router.get("/ready")
@router.get("/api/ready")
async def readiness_check() -> dict[str, str]:
    """
    Readiness check including database connectivity.
    Returns 200 only if application is ready to handle requests.
    """
    try:
        # Check database connection
        if not await check_db_connection():
            logger.error("Database not ready")
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Database not available",
            )
        
        # All checks passed
        return {"status": "ready"}
    
    except Exception as exc:
        logger.exception("Readiness check failed")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Service not ready",
        ) from exc


@router.get("/health/system")
@router.get("/api/health/system")
async def system_health_check(request: Request) -> dict[str, Any]:
    """
    Comprehensive single-process system health check.
    Verifies on-demand:
    1. Database connectivity
    2. Scheduled background jobs inventory and last run times
    3. Live resolved models for every provider/capability
    4. Credential validity (verifying real non-placeholder secrets)
    """
    from app.core.model_resolver import model_resolver

    # 1. Database Connectivity
    db_reachable = False
    try:
        db_reachable = await check_db_connection()
    except Exception as exc:
        logger.warning("System health check: DB connection check threw: %s", exc)

    # 2. Scheduler & Jobs
    scheduler = getattr(request.app.state, "scheduler", None)
    scheduler_running = bool(scheduler and getattr(scheduler, "scheduler", None) and scheduler.scheduler.running)
    scheduler_status = scheduler.get_status() if (scheduler and hasattr(scheduler, "get_status")) else {}

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
    registered_job_ids = {j["id"] for j in scheduler_status.get("jobs", [])}
    missing_jobs = [j for j in expected_jobs if j not in registered_job_ids]

    # 3. Model Resolution
    models_map = {
        "gemini": {
            "chat": model_resolver.get_model("gemini", "chat"),
            "vision": model_resolver.get_model("gemini", "vision"),
            "embeddings": model_resolver.get_model("gemini", "embeddings"),
        },
        "groq": {
            "chat": model_resolver.get_model("groq", "chat"),
            "audio": model_resolver.get_model("groq", "audio"),
        },
        "openai": {
            "chat": model_resolver.get_model("openai", "chat"),
            "vision": model_resolver.get_model("openai", "vision"),
            "embeddings": model_resolver.get_model("openai", "embeddings"),
            "audio": model_resolver.get_model("openai", "audio"),
        },
    }

    # 4. Credentials Verification
    def is_configured(val: str | None) -> bool:
        return bool(val and "placeholder" not in str(val).lower() and len(str(val).strip()) > 3)

    credentials_map = {
        "whatsapp_access_token": is_configured(settings.whatsapp_access_token),
        "whatsapp_app_secret": is_configured(settings.whatsapp_app_secret),
        "whatsapp_phone_number_id": is_configured(settings.whatsapp_phone_number_id),
        "secret_key": is_configured(settings.secret_key),
        "database_url": is_configured(settings.database_url),
        "gemini_api_key": is_configured(settings.gemini_api_key),
        "groq_api_key": is_configured(settings.groq_api_key),
        "openai_api_key": is_configured(settings.openai_api_key),
    }

    has_active_ai_key = any([
        credentials_map["gemini_api_key"],
        credentials_map["groq_api_key"],
        credentials_map["openai_api_key"],
    ])

    is_healthy = bool(
        db_reachable
        and scheduler_running
        and len(missing_jobs) == 0
        and has_active_ai_key
    )

    result = {
        "status": "healthy" if is_healthy else "degraded",
        "single_process_mode": True,
        "database": {
            "connected": db_reachable,
        },
        "scheduler": {
            "running": scheduler_running,
            "jobs_registered": len(registered_job_ids),
            "expected_jobs_count": len(expected_jobs),
            "missing_jobs": missing_jobs,
            "jobs": scheduler_status.get("jobs", []),
            "last_run_times": scheduler_status.get("last_run_times", {}),
        },
        "resolved_models": models_map,
        "credentials": credentials_map,
    }

    if not is_healthy:
        logger.warning("System health check degraded: %s", result)

    return result

