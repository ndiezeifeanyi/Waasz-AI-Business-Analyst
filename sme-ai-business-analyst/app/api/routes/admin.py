"""
Admin API routes.
Protected endpoints for founder dashboard and system management.
Requires admin authentication.
"""

from datetime import UTC, datetime, timedelta
import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth_deps import get_current_user, require_admin
from app.api.deps import get_db_session
from app.core.auth import TokenData
from app.services.access_control_service import AccessControlService
from app.services.invite_service import InviteCodeService
from app.services.report_service import ReportService

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/status")
async def admin_status(current_user: Annotated[TokenData, Depends(require_admin)]) -> dict[str, str]:
    """Check admin API status. Requires admin authentication."""
    logger.info(f"Admin status check by {current_user.sub}")
    return {"status": "admin-api-ready", "user": current_user.sub, "role": current_user.role}


@router.get("/metrics")
async def founder_metrics(
    current_user: Annotated[TokenData, Depends(require_admin)],
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Get founder dashboard metrics. Requires admin authentication."""
    try:
        result = await db.execute(text("select * from v_founder_dashboard_metrics"))
        row = result.mappings().first()
        logger.info(f"Metrics retrieved by {current_user.sub}")
        return dict(row or {})
    except Exception as exc:
        logger.exception(f"Error fetching metrics: {exc}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to fetch metrics")


@router.post("/businesses/{business_id}/reports/daily")
async def create_daily_report(
    business_id: UUID,
    current_user: Annotated[TokenData, Depends(require_admin)],
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, str]:
    """Generate daily report for business. Requires admin authentication."""
    try:
        logger.info(f"Daily report requested for business {business_id} by {current_user.sub}")
        report = await ReportService().generate_daily_report(db, business_id)
        await db.commit()
        return {"report_id": str(report.id), "body": report.body}
    except Exception as exc:
        logger.exception(f"Error generating daily report: {exc}")
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to generate report")


@router.post("/businesses/{business_id}/reports/weekly")
async def create_weekly_report(
    business_id: UUID,
    current_user: Annotated[TokenData, Depends(require_admin)],
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, str]:
    """Generate weekly report for business. Requires admin authentication."""
    try:
        logger.info(f"Weekly report requested for business {business_id} by {current_user.sub}")
        report = await ReportService().generate_weekly_report(db, business_id)
        await db.commit()
        return {"report_id": str(report.id), "body": report.body}
    except Exception as exc:
        logger.exception(f"Error generating weekly report: {exc}")
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to generate report")


class CreateInviteCodeRequest(BaseModel):
    code: str | None = Field(default=None, description="Custom code name (auto-generated if empty)")
    max_uses: int = Field(default=1, ge=1, description="Maximum number of redemptions allowed")
    expires_in_days: int | None = Field(default=None, ge=1, description="Days until expiration")


@router.post("/invite-codes")
async def create_invite_code(
    payload: CreateInviteCodeRequest,
    current_user: Annotated[TokenData, Depends(require_admin)],
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Generate a new invite code. Requires admin authentication."""
    try:
        service = InviteCodeService()
        expires_at = (
            datetime.now(UTC) + timedelta(days=payload.expires_in_days)
            if payload.expires_in_days
            else None
        )
        invite = await service.create_code(
            db,
            code=payload.code,
            max_uses=payload.max_uses,
            expires_at=expires_at,
        )
        return {
            "id": str(invite.id),
            "code": invite.code,
            "max_uses": invite.max_uses,
            "use_count": invite.use_count,
            "is_active": invite.is_active,
            "expires_at": invite.expires_at.isoformat() if invite.expires_at else None,
            "created_at": invite.created_at.isoformat() if invite.created_at else None,
        }
    except Exception as exc:
        logger.exception("Error creating invite code: %s", exc)
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@router.get("/invite-codes")
async def list_invite_codes(
    current_user: Annotated[TokenData, Depends(require_admin)],
    active_only: bool = False,
    db: AsyncSession = Depends(get_db_session),
) -> list[dict]:
    """List all invite codes and their usage stats. Requires admin authentication."""
    service = InviteCodeService()
    codes = await service.list_codes(db, active_only=active_only)
    return [
        {
            "id": str(c.id),
            "code": c.code,
            "max_uses": c.max_uses,
            "use_count": c.use_count,
            "is_active": c.is_active,
            "expires_at": c.expires_at.isoformat() if c.expires_at else None,
            "created_at": c.created_at.isoformat() if c.created_at else None,
            "created_by_user_id": str(c.created_by_user_id) if c.created_by_user_id else None,
        }
        for c in codes
    ]


@router.post("/invite-codes/{code_id}/deactivate")
async def deactivate_invite_code(
    code_id: UUID,
    current_user: Annotated[TokenData, Depends(require_admin)],
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Deactivate an invite code instantly. Requires admin authentication."""
    service = InviteCodeService()
    success = await service.deactivate_code(db, code_id)
    if not success:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invite code not found")
    return {"status": "deactivated", "id": str(code_id)}


@router.get("/waitlist")
async def list_waitlist(
    current_user: Annotated[TokenData, Depends(require_admin)],
    db: AsyncSession = Depends(get_db_session),
) -> list[dict]:
    """List waitlist registrations. Requires admin authentication."""
    service = InviteCodeService()
    entries = await service.list_waitlist(db)
    return [
        {
            "id": str(e.id),
            "phone_number": e.phone_number,
            "requested_at": e.requested_at.isoformat() if e.requested_at else None,
        }
        for e in entries
    ]


class AddApprovedTesterRequest(BaseModel):
    phone_number: str = Field(..., min_length=5, max_length=32)
    label: str | None = Field(default=None, max_length=255)


@router.get("/access-control/status")
async def get_access_control_status(
    current_user: Annotated[TokenData, Depends(require_admin)],
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Get live access control metrics and capacity status."""
    service = AccessControlService()
    return await service.get_status(db)


@router.get("/approved-testers")
async def list_approved_testers(
    current_user: Annotated[TokenData, Depends(require_admin)],
    db: AsyncSession = Depends(get_db_session),
) -> list[dict]:
    """List all approved testers on allowlist."""
    service = AccessControlService()
    testers = await service.list_approved_testers(db)
    return [
        {
            "id": str(t.id),
            "phone_number": t.phone_number,
            "label": t.label,
            "added_at": t.added_at.isoformat() if t.added_at else None,
            "added_by": t.added_by,
        }
        for t in testers
    ]


@router.post("/approved-testers", status_code=status.HTTP_201_CREATED)
async def add_approved_tester(
    payload: AddApprovedTesterRequest,
    current_user: Annotated[TokenData, Depends(require_admin)],
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Add a tester phone number to the allowlist."""
    service = AccessControlService()
    tester = await service.add_approved_tester(
        db, phone=payload.phone_number, label=payload.label, added_by=current_user.sub
    )
    return {
        "status": "added",
        "id": str(tester.id),
        "phone_number": tester.phone_number,
        "label": tester.label,
    }


@router.delete("/approved-testers/{phone_number}")
async def delete_approved_tester(
    phone_number: str,
    current_user: Annotated[TokenData, Depends(require_admin)],
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """Remove a phone number from the allowlist."""
    service = AccessControlService()
    deleted = await service.remove_approved_tester(db, phone_number)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Approved tester not found")
    return {"status": "deleted", "phone_number": phone_number}


class AccountRecoveryRequest(BaseModel):
    business_id: UUID = Field(..., description="ID of the business account being reclaimed")
    new_phone_number: str = Field(..., description="New phone number to associate with this business")
    verification_method: str = Field(default="admin_verified", description="Method used to verify identity (e.g. CAC document, email, secret phrase)")
    notes: str | None = Field(default=None, description="Audit notes explaining the recovery")


@router.post("/recover-account")
async def recover_account(
    payload: AccountRecoveryRequest,
    current_user: Annotated[TokenData, Depends(require_admin)],
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """
    Reclaim and relink an existing business account to a new phone number.
    Preserves all historical transactions, messages, and reports.
    """
    from app.services.account_recovery_service import AccountRecoveryError, AccountRecoveryService

    service = AccountRecoveryService()
    try:
        result = await service.relink_phone_number(
            db=db,
            business_id=payload.business_id,
            new_phone_number=payload.new_phone_number,
            verification_method=payload.verification_method,
            notes=payload.notes,
            operator=current_user.sub,
        )
        return result
    except AccountRecoveryError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except Exception as exc:
        logger.exception("Unexpected error during account recovery: %s", exc)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Account recovery failed")



