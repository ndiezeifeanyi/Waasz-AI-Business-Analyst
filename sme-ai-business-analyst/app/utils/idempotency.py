"""
Idempotency utility for webhook delivery and request deduplication.
"""
from typing import Any


def idempotency_key(*parts: str) -> str:
    """Generate a colon-delimited idempotency key from multiple string components."""
    return ":".join(part.strip() for part in parts if part and part.strip())


def is_duplicate_message(message: Any) -> bool:
    """
    Check if an existing recorded message has already been processed or is a duplicate delivery.
    Used to silently no-op duplicate Meta webhook retries and rapid concurrent deliveries.
    """
    if message is None:
        return False
    if getattr(message, "_is_duplicate", None) is True:
        return True
    return getattr(message, "status", None) == "processed"
