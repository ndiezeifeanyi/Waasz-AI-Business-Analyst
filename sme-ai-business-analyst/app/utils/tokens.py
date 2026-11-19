import base64
import hashlib
import hmac
import json
import re
import time
from uuid import UUID

from app.core.config import settings

# Default token lifetime: 30 days
DEFAULT_TOKEN_TTL_SECONDS = 30 * 24 * 3600


def _get_signing_key() -> bytes:
    """Returns the cryptographic secret key used for HMAC signing."""
    secret = getattr(settings, "jwt_secret_key", None) or getattr(settings, "secret_key", "waasz_debt_secure_hmac_secret_key_v2")
    return secret.encode("utf-8")


from datetime import date, datetime, timezone

def generate_debt_token(
    debt_id: UUID,
    due_date: datetime | date | None = None,
    expires_in_seconds: int = DEFAULT_TOKEN_TTL_SECONDS,
    revocation_nonce: int = 0,
) -> str:
    """
    Generates a secure, URL-safe signed token for the debt details page and button payloads.
    Token payload: debt_id:created_at:expires_at:nonce:signature
    Ensures expiry is no earlier than due_date + 30 days.
    """
    now = int(time.time())
    base_expiry = now + expires_in_seconds
    if due_date:
        if isinstance(due_date, datetime):
            due_ts = int(due_date.timestamp())
        elif isinstance(due_date, date):
            due_ts = int(datetime.combine(due_date, datetime.min.time(), tzinfo=timezone.utc).timestamp())
        else:
            due_ts = 0
        due_plus_30 = due_ts + (30 * 24 * 3600)
        expires_at = max(base_expiry, due_plus_30)
    else:
        expires_at = base_expiry

    payload_str = f"{str(debt_id)}:{now}:{expires_at}:{revocation_nonce}"
    
    sig = hmac.new(_get_signing_key(), payload_str.encode("utf-8"), hashlib.sha256).hexdigest()[:32]
    full_token_str = f"{payload_str}:{sig}"
    
    # URL-safe base64 without padding
    return base64.urlsafe_b64encode(full_token_str.encode("utf-8")).decode("utf-8").rstrip("=")


def verify_debt_token(token: str) -> tuple[bool, UUID | None, str]:
    """
    Verifies a debt token.
    Returns: (is_valid, debt_id, reason)
    Reasons: 'valid', 'expired', 'invalid_signature', 'malformed'
    """
    if not token or not isinstance(token, str):
        return False, None, "malformed"

    try:
        # Add back padding
        padding = 4 - (len(token) % 4)
        if padding != 4:
            token += "=" * padding
        raw_str = base64.urlsafe_b64decode(token.encode("utf-8")).decode("utf-8")
        parts = raw_str.split(":")
        if len(parts) != 5:
            return False, None, "malformed"

        debt_id_str, created_at_str, expires_at_str, nonce_str, sig = parts
        debt_id = UUID(debt_id_str)
        expires_at = int(expires_at_str)
        payload_str = f"{debt_id_str}:{created_at_str}:{expires_at_str}:{nonce_str}"

        # Check signature with constant time comparison
        expected_sig = hmac.new(_get_signing_key(), payload_str.encode("utf-8"), hashlib.sha256).hexdigest()[:32]
        if not hmac.compare_digest(sig, expected_sig):
            return False, None, "invalid_signature"

        # Check expiration
        now = int(time.time())
        if now > expires_at:
            return False, debt_id, "expired"

        return True, debt_id, "valid"
    except Exception:
        return False, None, "malformed"


def mask_phone_number(phone: str | None) -> str:
    """
    Masks a phone number for privacy display on public cards.
    E.g. +2348031234567 or 08031234567 -> +234 ••• ••• 4567
    """
    if not phone:
        return "Not on file"
    from app.utils.phone import normalize_e164_phone
    norm = normalize_e164_phone(str(phone))
    if norm:
        digits = re.sub(r"\D+", "", norm)
        country = digits[:3]
        last4 = digits[-4:]
        return f"+{country} ••• ••• {last4}"

    digits = re.sub(r"\D+", "", str(phone))
    if len(digits) >= 10:
        last4 = digits[-4:]
        country = digits[:3] if digits.startswith("234") else "234"
        return f"+{country} ••• ••• {last4}"
    return "•••• ••••"


def generate_action_csrf_token(token: str, debt_id: UUID) -> str:
    """Generates an action CSRF token bound to the debt_id and the token."""
    key = _get_signing_key()
    msg = f"action_csrf:{token}:{debt_id}".encode("utf-8")
    return hmac.new(key, msg, hashlib.sha256).hexdigest()[:32]


def verify_action_csrf_token(token: str, debt_id: UUID, csrf_token: str) -> bool:
    """Verifies that an action CSRF token is valid and matches."""
    if not csrf_token or not isinstance(csrf_token, str):
        return False
    expected = generate_action_csrf_token(token, debt_id)
    return hmac.compare_digest(csrf_token, expected)

