"""
WhatsApp webhook routes.
Handles webhook verification and message ingestion.
"""

import json
import logging

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request, status
from fastapi.responses import PlainTextResponse

from app.core.config import settings
from app.core.rate_limit import InMemoryRateLimiter
from app.core.security import constant_time_equals, verify_whatsapp_signature
from app.services.webhook_processor import process_whatsapp_webhook

logger = logging.getLogger(__name__)
router = APIRouter()
rate_limiter = InMemoryRateLimiter(settings.rate_limit_per_minute)


@router.get("", response_class=PlainTextResponse)
@router.get("/", response_class=PlainTextResponse)
async def verify_webhook(
    hub_mode: str | None = Query(default=None, alias="hub.mode"),
    hub_verify_token: str | None = Query(default=None, alias="hub.verify_token"),
    hub_challenge: str | None = Query(default=None, alias="hub.challenge"),
):
    """
    Verify WhatsApp webhook subscription.
    Called by Meta during webhook setup.
    Verifies handshake using ONLY WHATSAPP_VERIFY_TOKEN.
    Must return the hub.challenge as raw plain text (not JSON).
    """
    if not settings.whatsapp_verify_token:
        logger.error("WhatsApp webhook verification attempted without WHATSAPP_VERIFY_TOKEN configured")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Webhook verification unconfigured",
        )

    if hub_mode == "subscribe" and constant_time_equals(
        hub_verify_token or "", settings.whatsapp_verify_token
    ):
        logger.info("WhatsApp webhook verified successfully")
        return PlainTextResponse(content=hub_challenge or "", media_type="text/plain")
    
    logger.warning(
        f"WhatsApp webhook verification failed: mode={hub_mode}, "
        f"token_received={hub_verify_token}"
    )
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Invalid webhook verification token"
    )


@router.post("")
@router.post("/")
async def receive_webhook(request: Request, background_tasks: BackgroundTasks) -> dict[str, str]:
    """
    Receive WhatsApp webhook messages.
    Validates signature if secret configured, rate limits, and queues for processing.
    """
    # Extract client IP for rate limiting
    client_ip = request.client.host if request.client else "unknown"
    
    # Rate limiting
    if not rate_limiter.allow(client_ip):
        logger.warning(f"Rate limit exceeded for {client_ip}")
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit exceeded",
        )

    # Get raw body
    raw_body = await request.body()
    
    # Payload size validation
    if len(raw_body) > 1_000_000:
        logger.warning(f"Payload too large: {len(raw_body)} bytes from {client_ip}")
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Payload too large",
        )

    # Signature verification (fail-secure: rejects with 403 if unconfigured or invalid)
    if not settings.whatsapp_app_secret:
        logger.warning(f"Inbound webhook rejected: WHATSAPP_APP_SECRET unconfigured ({client_ip})")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Webhook signature verification unconfigured",
        )

    signature = request.headers.get("x-hub-signature-256")
    if not verify_whatsapp_signature(settings.whatsapp_app_secret, raw_body, signature):
        logger.warning(f"Invalid WhatsApp signature from {client_ip}")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid webhook signature",
        )

    # Parse JSON
    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError as exc:
        logger.warning(f"Invalid JSON in webhook from {client_ip}: {exc}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid JSON"
        ) from exc

    # Queue for background processing
    background_tasks.add_task(process_whatsapp_webhook, payload)
    logger.debug(f"Webhook accepted from {client_ip}, queued for processing")
    
    return {"status": "accepted"}

