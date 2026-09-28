"""
Input validation and sanitization utilities.
Prevents injection attacks, XSS, and other input-based vulnerabilities.
"""

import logging
import re
from decimal import Decimal
from typing import Any

logger = logging.getLogger(__name__)


class SanitizationError(ValueError):
    """Raised when input sanitization fails."""


def sanitize_phone_number(phone: str) -> str:
    """
    Sanitize and validate phone number.
    Keeps only digits, +, and allows standard international format.
    """
    if not phone:
        raise SanitizationError("Phone number cannot be empty")
    
    if len(phone) > 20:
        raise SanitizationError("Phone number too long")
    
    # Keep only digits, +, and spaces
    sanitized = re.sub(r"[^\d+\s]", "", phone)
    
    # Remove spaces
    sanitized = sanitized.replace(" ", "")
    
    # Must have at least 8 digits
    digit_count = len(re.sub(r"\D", "", sanitized))
    if digit_count < 8:
        raise SanitizationError("Phone number must have at least 8 digits")
    
    return sanitized


def sanitize_text(text: str, max_length: int = 4000) -> str:
    """
    Sanitize user text input.
    Removes potentially dangerous characters but preserves natural language.
    """
    if not text:
        return ""
    
    # Limit length
    if len(text) > max_length:
        logger.warning(f"Text exceeded max length: {len(text)} > {max_length}")
        text = text[:max_length]
    
    # Remove null bytes
    text = text.replace("\x00", "")
    
    # Remove control characters except newlines and tabs
    text = "".join(char for char in text if char.isprintable() or char in "\n\t")
    
    # Normalize whitespace
    text = re.sub(r"\s+", " ", text).strip()
    
    return text


def sanitize_business_name(name: str) -> str:
    """Sanitize business name."""
    sanitized = sanitize_text(name, max_length=100)
    if not sanitized:
        raise SanitizationError("Business name cannot be empty")
    return sanitized


def sanitize_currency_amount(amount: str | float | Decimal) -> Decimal:
    """
    Sanitize and validate currency amount.
    Returns Decimal with 2 places precision.
    """
    try:
        if isinstance(amount, str):
            # Remove currency symbols and spaces
            amount = re.sub(r"[₦$€£\s]", "", amount)
            amount = Decimal(amount)
        elif isinstance(amount, float):
            amount = Decimal(str(amount))
        elif isinstance(amount, Decimal):
            pass
        else:
            raise SanitizationError(f"Invalid amount type: {type(amount)}")
        
        # Validate range (0 to 999,999,999.99)
        if amount < 0:
            raise SanitizationError("Amount cannot be negative")
        if amount > Decimal("999999999.99"):
            raise SanitizationError("Amount too large")
        
        # Round to 2 decimal places
        return amount.quantize(Decimal("0.01"))
    
    except (ValueError, InvalidOperation) as exc:
        raise SanitizationError(f"Invalid amount: {amount}") from exc


def sanitize_url(url: str) -> str:
    """
    Sanitize URL to prevent injection attacks.
    Only allows http/https URLs.
    """
    if not url:
        raise SanitizationError("URL cannot be empty")
    
    url = url.strip()
    
    # Must start with http:// or https://
    if not re.match(r"^https?://", url):
        raise SanitizationError("URL must start with http:// or https://")
    
    # No null bytes or control characters
    if "\x00" in url or any(ord(c) < 32 for c in url):
        raise SanitizationError("URL contains invalid characters")
    
    # Reasonable length limit
    if len(url) > 2048:
        raise SanitizationError("URL too long")
    
    return url


def detect_prompt_injection(text: str) -> bool:
    """
    Detect common prompt injection attack patterns.
    Returns True if suspicious patterns detected.
    """
    suspicious_patterns = [
        r"ignore previous",
        r"disregard (all )?instructions",
        r"forget (all )?system prompts?",
        r"system override",
        r"pretend (you are|you're)",
        r"act as",
        r"roleplay",
        r"(set|change) (the )?system prompt",
        r"jailbreak",
        r"bypass",
        r"my (custom )?instructions",
        r"your instructions say",
        r"you are now",
    ]
    
    lower_text = text.lower()
    for pattern in suspicious_patterns:
        if re.search(pattern, lower_text):
            logger.warning(f"Potential prompt injection detected: matched pattern '{pattern}'")
            return True
    
    return False


def validate_extraction_input(user_input: str) -> str:
    """
    Validate and sanitize user input before AI extraction.
    
    Args:
        user_input: User message or text
    
    Returns:
        Sanitized, validated input safe for AI processing
    
    Raises:
        SanitizationError: If input is invalid or suspicious
    """
    # Sanitize basic format
    sanitized = sanitize_text(user_input, max_length=4000)
    
    if not sanitized:
        raise SanitizationError("Input cannot be empty")
    
    # Check for prompt injection attempts
    if detect_prompt_injection(sanitized):
        logger.warning("Prompt injection attempt blocked")
        raise SanitizationError("Invalid input detected")
    
    return sanitized


def sanitize_metadata(metadata: dict | None) -> dict:
    """
    Sanitize metadata dictionary to prevent injection via metadata fields.
    """
    if not metadata:
        return {}
    
    sanitized = {}
    for key, value in metadata.items():
        # Limit key length
        if len(str(key)) > 100:
            continue
        
        # Sanitize value
        if isinstance(value, str):
            value = sanitize_text(value, max_length=1000)
        elif isinstance(value, (int, float, bool)):
            pass
        else:
            continue  # Skip complex types
        
        sanitized[key] = value
    
    return sanitized


# Import at end to avoid circular imports
from decimal import InvalidOperation
