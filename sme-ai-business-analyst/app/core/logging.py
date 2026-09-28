"""
Logging configuration with security measures.
Prevents secrets and sensitive data from appearing in logs.
"""

import logging
import re
from typing import Any

from app.core.config import settings


# Patterns that indicate secrets or sensitive data
SECRETS_PATTERNS = [
    (r"api[_-]?key['\"]?\s*[:=]\s*['\"]?([^'\"\s]+)", "API_KEY"),
    (r"token['\"]?\s*[:=]\s*['\"]?([^'\"\s]+)", "TOKEN"),
    (r"password['\"]?\s*[:=]\s*['\"]?([^'\"\s]+)", "PASSWORD"),
    (r"secret['\"]?\s*[:=]\s*['\"]?([^'\"\s]+)", "SECRET"),
    (r"bearer\s+([^\s]+)", "BEARER_TOKEN"),
    (r"authorization['\"]?\s*[:=]\s*Bearer\s+([^\s]+)", "AUTH_TOKEN"),
]


class SecretsMaskingFormatter(logging.Formatter):
    """
    Custom formatter that masks sensitive information in logs.
    Prevents accidental leakage of API keys, tokens, and passwords.
    """

    def format(self, record: logging.LogRecord) -> str:
        """Format log record, masking sensitive data."""
        # Format the message first
        message = super().format(record)
        
        # Mask secrets
        message = self._mask_secrets(message)
        
        return message

    @staticmethod
    def _mask_secrets(text: str) -> str:
        """Replace sensitive patterns with masked versions."""
        masked = text
        for pattern, label in SECRETS_PATTERNS:
            # Find all matches
            matches = re.finditer(pattern, masked, re.IGNORECASE)
            for match in matches:
                secret = match.group(1) if match.lastindex else match.group(0)
                # Create masked version: show first and last 3 chars
                if len(secret) > 6:
                    masked_secret = f"{secret[:3]}...{secret[-3:]}"
                else:
                    masked_secret = "***"
                # Replace in text
                masked = masked.replace(secret, masked_secret, 1)
        
        return masked


def configure_logging() -> None:
    """Configure application logging with security measures."""
    # Get logging config
    log_level = getattr(logging, settings.log_level.upper(), logging.INFO)
    
    # Create root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)
    
    # Clear existing handlers
    root_logger.handlers.clear()
    
    # Console handler with security masking
    console_handler = logging.StreamHandler()
    console_handler.setLevel(log_level)
    
    # Format includes timestamp, level, logger name, message
    formatter = SecretsMaskingFormatter(
        fmt="%(asctime)s %(levelname)-8s [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)
    
    # Reduce noise from third-party libraries
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    logging.getLogger("langchain").setLevel(logging.WARNING)
    
    logger = logging.getLogger(__name__)
    logger.info(f"Logging configured: level={settings.log_level}, env={settings.app_env}")

