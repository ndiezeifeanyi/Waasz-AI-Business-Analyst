"""
Dependency injection for authentication and authorization.
Provides secured endpoints with role-based access control.
"""

from typing import Annotated

from fastapi import Depends, Header, HTTPException, status

from app.core.auth import AuthenticationError, AuthorizationError, RoleBasedAccessControl, TokenData, verify_token
from app.core.exceptions import AppError


async def get_token_from_header(authorization: str | None = Header(None)) -> str:
    """Extract and validate Bearer token from Authorization header."""
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization header missing",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authorization header format",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    return parts[1]


async def get_current_user(token: Annotated[str, Depends(get_token_from_header)]) -> TokenData:
    """Verify token and return token data."""
    try:
        return verify_token(token)
    except AuthenticationError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        )


async def require_admin(current_user: Annotated[TokenData, Depends(get_current_user)]) -> TokenData:
    """Require admin role."""
    if current_user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )
    return current_user


async def require_business_owner(
    current_user: Annotated[TokenData, Depends(get_current_user)],
) -> TokenData:
    """Require business owner role."""
    if current_user.role not in ("admin", "business_owner"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Business owner access required",
        )
    return current_user


async def require_permission(
    required_permission: str,
) -> callable:
    """Create a dependency that requires a specific permission."""
    
    async def check_permission(
        current_user: Annotated[TokenData, Depends(get_current_user)],
    ) -> TokenData:
        if not RoleBasedAccessControl.has_permission(current_user, required_permission):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Permission '{required_permission}' required",
            )
        return current_user
    
    return check_permission


async def require_business_access(
    business_id: str,
) -> callable:
    """Create a dependency that verifies business access."""
    
    async def check_business_access(
        current_user: Annotated[TokenData, Depends(get_current_user)],
    ) -> TokenData:
        try:
            RoleBasedAccessControl.check_business_access(current_user, business_id)
        except AuthorizationError as exc:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=str(exc),
            )
        return current_user
    
    return check_business_access
