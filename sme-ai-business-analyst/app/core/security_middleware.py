"""
Security middleware for FastAPI application.
Implements defense-in-depth with rate limiting, input validation, and security headers.
"""

import logging
import re
from typing import Callable

from fastapi import Request, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.core.config import settings
from app.core.exceptions import AppError
from app.core.rate_limit import InMemoryRateLimiter

logger = logging.getLogger(__name__)

# Rate limiters by endpoint type
webhook_rate_limiter = InMemoryRateLimiter(settings.rate_limit_per_minute)
api_rate_limiter = InMemoryRateLimiter(settings.rate_limit_per_minute * 2)
admin_rate_limiter = InMemoryRateLimiter(10)  # Stricter for admin


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add security headers to all responses."""

    async def dispatch(self, request: Request, call_next: Callable) -> any:
        response = await call_next(request)
        # Prevent clickjacking
        response.headers["X-Frame-Options"] = "DENY"
        # Prevent MIME type sniffing
        response.headers["X-Content-Type-Options"] = "nosniff"
        # Enable XSS protection
        response.headers["X-XSS-Protection"] = "1; mode=block"
        # Referrer policy
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        # Permissions policy
        response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
        # Content Security Policy (allow Chart.js CDN and inline scripts/styles for dashboard)
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src 'self' https://fonts.gstatic.com data:; "
            "img-src 'self' data: https:; "
            "connect-src 'self'"
        )
        # HSTS (only in production)
        if settings.app_env == "production":
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Rate limit by IP address and endpoint."""

    async def dispatch(self, request: Request, call_next: Callable) -> any:
        client_ip = self._get_client_ip(request)
        path = request.url.path

        # Determine which rate limiter to use
        if path.startswith("/webhooks/whatsapp"):
            limiter = webhook_rate_limiter
        elif path.startswith("/admin"):
            limiter = admin_rate_limiter
        else:
            limiter = api_rate_limiter

        if not limiter.allow(client_ip):
            logger.warning(f"Rate limit exceeded for {client_ip} on {path}")
            return JSONResponse(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                content={"detail": "Rate limit exceeded. Please try again later."},
            )

        return await call_next(request)

    @staticmethod
    def _get_client_ip(request: Request) -> str:
        """Extract client IP, considering proxies."""
        if request.client:
            return request.client.host
        # Check for proxy headers
        if x_forwarded_for := request.headers.get("x-forwarded-for"):
            return x_forwarded_for.split(",")[0].strip()
        return "unknown"


class PayloadSizeMiddleware(BaseHTTPMiddleware):
    """Enforce payload size limits."""

    async def dispatch(self, request: Request, call_next: Callable) -> any:
        content_length = request.headers.get("content-length")
        if content_length:
            size_mb = int(content_length) / (1024 * 1024)
            if size_mb > 50:
                return JSONResponse(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    content={"detail": "Payload too large. Maximum 50MB allowed."},
                )
        return await call_next(request)


class InputSanitizationMiddleware(BaseHTTPMiddleware):
    """Sanitize and validate input to prevent injection attacks."""

    # Patterns that indicate potential injection attempts
    SQL_INJECTION_PATTERNS = [
        r"(\bselect\b|\bunion\b|\bdrop\b|\binsert\b|\bupdate\b|\bdelete\b)",
        r"(--|;|\/\*|\*\/)",
    ]

    async def dispatch(self, request: Request, call_next: Callable) -> any:
        # Check query parameters
        for key, value in request.query_params.items():
            if self._is_suspicious(value):
                logger.warning(f"Suspicious query parameter detected: {key}={value[:50]}")
                return JSONResponse(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    content={"detail": "Invalid input detected."},
                )

        return await call_next(request)

    @classmethod
    def _is_suspicious(cls, value: str) -> bool:
        """Check if value contains suspicious patterns."""
        if not isinstance(value, str):
            return False
        for pattern in cls.SQL_INJECTION_PATTERNS:
            if re.search(pattern, value, re.IGNORECASE):
                return True
        return False


class ExceptionHandlingMiddleware(BaseHTTPMiddleware):
    """Handle exceptions and prevent stack trace leakage in production."""

    async def dispatch(self, request: Request, call_next: Callable) -> any:
        try:
            return await call_next(request)
        except AppError as exc:
            logger.info(f"Application error: {exc}")
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"detail": str(exc)},
            )
        except Exception as exc:
            logger.exception(f"Unhandled exception: {exc}")
            # Don't expose full error in production
            detail = str(exc) if settings.app_debug else "An error occurred. Please try again."
            return JSONResponse(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                content={"detail": detail},
            )


def setup_security_middleware(app) -> None:
    """Configure all security middleware on FastAPI app."""
    # Trust proxy headers in production
    if settings.app_env == "production":
        app.add_middleware(
            TrustedHostMiddleware,
            allowed_hosts=settings.allowed_hosts.split(","),
        )

    # CORS must come after TrustedHostMiddleware
    origins = [origin.strip() for origin in settings.cors_origins.split(",") if origin.strip()]
    has_wildcard = "*" in origins
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins if not has_wildcard else ["*"],
        allow_credentials=not has_wildcard,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
        max_age=600,
    )

    # Custom security middleware (order matters - last added is first executed)
    app.add_middleware(ExceptionHandlingMiddleware)
    app.add_middleware(InputSanitizationMiddleware)
    app.add_middleware(PayloadSizeMiddleware)
    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)
