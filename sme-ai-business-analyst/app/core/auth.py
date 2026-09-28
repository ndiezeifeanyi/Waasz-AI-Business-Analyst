"""
Authentication and authorization utilities.
Implements JWT token validation, role-based access control, and secure password hashing.
"""

import logging
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from typing import Literal

import jwt
from passlib.context import CryptContext
from pydantic import BaseModel, Field

from app.core.config import settings
from app.core.exceptions import AppError

logger = logging.getLogger(__name__)

# Password hashing configuration
pwd_context = CryptContext(
    schemes=["argon2"],
    deprecated="auto",
)

# JWT configuration
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30
REFRESH_TOKEN_EXPIRE_DAYS = 7


class TokenData(BaseModel):
    """JWT token claims."""

    sub: str  # subject (user ID)
    role: Literal["admin", "business_owner", "staff", "system"]
    business_id: str | None = None
    exp: datetime = Field(default_factory=lambda: datetime.now(UTC) + timedelta(minutes=30))
    iat: datetime = Field(default_factory=lambda: datetime.now(UTC))
    jti: str | None = None  # JWT ID for token revocation


class AuthenticationError(AppError):
    """Raised when authentication fails."""


class AuthorizationError(AppError):
    """Raised when user lacks required permissions."""


def hash_password(password: str) -> str:
    """Hash a password using Argon2."""
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    """Verify a plain password against hashed version."""
    return pwd_context.verify(plain, hashed)


def create_access_token(token_data: TokenData) -> str:
    """Create a signed JWT access token."""
    if not settings.secret_key or settings.secret_key == "change-me-before-deploy":
        raise ValueError("SECRET_KEY not properly configured")
    
    # Ensure exp is set
    if not token_data.exp or token_data.exp <= datetime.now(UTC):
        token_data.exp = datetime.now(UTC) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    
    payload = token_data.model_dump(exclude_none=True)
    # Convert datetime to Unix timestamp for JWT
    payload["exp"] = int(token_data.exp.timestamp())
    payload["iat"] = int(token_data.iat.timestamp())
    
    encoded = jwt.encode(payload, settings.secret_key, algorithm=ALGORITHM)
    return encoded


def create_refresh_token(user_id: str) -> str:
    """Create a refresh token."""
    exp = datetime.now(UTC) + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
    token_data = TokenData(
        sub=user_id,
        role="refresh",
        exp=exp,
    )
    return create_access_token(token_data)


def verify_token(token: str) -> TokenData:
    """Verify and decode a JWT token."""
    if not settings.secret_key or settings.secret_key == "change-me-before-deploy":
        raise AuthenticationError("Token validation not configured")
    
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])
        # Convert Unix timestamp back to datetime
        payload["exp"] = datetime.fromtimestamp(payload["exp"], tz=UTC)
        payload["iat"] = datetime.fromtimestamp(payload["iat"], tz=UTC)
        return TokenData(**payload)
    except jwt.ExpiredSignatureError:
        raise AuthenticationError("Token has expired")
    except jwt.InvalidTokenError as exc:
        raise AuthenticationError(f"Invalid token: {exc}")


class RoleBasedAccessControl:
    """RBAC helper for checking permissions."""

    # Define role permissions
    PERMISSIONS = {
        "admin": {
            "read_all_businesses",
            "read_metrics",
            "read_reports",
            "create_reports",
            "view_settings",
            "manage_users",
        },
        "business_owner": {
            "read_own_business",
            "read_own_records",
            "read_own_reports",
            "manage_staff",
        },
        "staff": {
            "read_own_business",
            "read_own_records",
        },
        "system": {
            "execute_system_tasks",
        },
    }

    @staticmethod
    def has_permission(token_data: TokenData, required_permission: str) -> bool:
        """Check if user has required permission."""
        permissions = RoleBasedAccessControl.PERMISSIONS.get(token_data.role, set())
        return required_permission in permissions

    @staticmethod
    def check_business_access(token_data: TokenData, business_id: str) -> None:
        """Verify user can access a specific business."""
        if token_data.role == "admin":
            return  # Admins can access any business
        if token_data.role in ("business_owner", "staff"):
            if token_data.business_id != business_id:
                raise AuthorizationError(f"Access denied to business {business_id}")
            return
        raise AuthorizationError(f"Role {token_data.role} cannot access businesses")


def validate_secret_key_configured() -> None:
    """Validate that secret key is properly configured."""
    if not settings.secret_key:
        raise ValueError("SECRET_KEY environment variable not set")
    if settings.secret_key == "change-me-before-deploy":
        if settings.app_env == "production":
            raise ValueError("SECRET_KEY not changed from default in production!")
        logger.warning("SECRET_KEY is using default value - change before production")


@lru_cache(maxsize=1)
def get_security_config() -> dict:
    """Get security configuration, validating at startup."""
    validate_secret_key_configured()
    return {
        "access_token_expire_minutes": ACCESS_TOKEN_EXPIRE_MINUTES,
        "refresh_token_expire_days": REFRESH_TOKEN_EXPIRE_DAYS,
        "algorithm": ALGORITHM,
    }
