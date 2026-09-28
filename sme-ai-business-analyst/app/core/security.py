"""
Security utilities for cryptographic operations and signature verification.
"""

import hmac
import logging
from hashlib import sha256

logger = logging.getLogger(__name__)


def constant_time_equals(left: str, right: str) -> bool:
    """
    Compare two strings in constant time to prevent timing attacks.
    
    Args:
        left: First string to compare
        right: Second string to compare
    
    Returns:
        True if strings are equal, False otherwise
    """
    return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))


def verify_whatsapp_signature(app_secret: str, raw_body: bytes, signature: str | None) -> bool:
    """
    Verify WhatsApp webhook signature.
    
    CRITICAL: This implements fail-secure behavior. If the app secret is not configured,
    verification MUST fail (return False), not skip validation.
    
    Args:
        app_secret: WhatsApp app secret
        raw_body: Raw request body bytes
        signature: X-Hub-Signature-256 header value
    
    Returns:
        True if signature is valid, False otherwise
    
    Security Notes:
        - Empty app_secret causes verification to FAIL (not skip)
        - Uses constant-time comparison to prevent timing attacks
        - Validates signature format (must start with "sha256=")
    """
    # FAIL-SECURE: If app_secret is empty, verification must fail
    if not app_secret:
        logger.warning("WhatsApp signature verification attempted without app_secret configured")
        return False
    
    # Validate signature header format
    if not signature or not signature.startswith("sha256="):
        logger.warning(f"Invalid signature format: {signature}")
        return False
    
    # Compute expected signature
    try:
        digest = hmac.new(app_secret.encode("utf-8"), raw_body, sha256).hexdigest()
        expected = f"sha256={digest}"
        
        # Use constant-time comparison
        is_valid = constant_time_equals(expected, signature)
        
        if not is_valid:
            logger.warning("WhatsApp signature verification failed - signature mismatch")
        
        return is_valid
    except Exception as exc:
        logger.exception(f"Error during signature verification: {exc}")
        return False

