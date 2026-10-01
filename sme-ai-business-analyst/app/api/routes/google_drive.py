import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.services.google_drive_service import GoogleDriveService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/integrations/google-drive", tags=["Google Drive Integration"])
drive_service = GoogleDriveService()


@router.get("/callback", response_class=HTMLResponse)
async def google_drive_oauth_callback(
    code: str | None = Query(None),
    state: str | None = Query(None),
    error: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """
    Handles Google OAuth redirect after user consents to drive.file permissions.
    """
    if error:
        logger.warning("Google Drive OAuth denied or error: %s", error)
        return HTMLResponse(
            content=f"""
            <html>
                <body style="font-family: Arial, sans-serif; text-align: center; padding: 50px;">
                    <h2 style="color: #ef4444;">Google Drive Connection Cancelled</h2>
                    <p>Authorization was not completed ({error}). You can retry anytime via WhatsApp.</p>
                </body>
            </html>
            """,
            status_code=400,
        )

    if not code or not state:
        raise HTTPException(status_code=400, detail="Missing code or state in OAuth callback")

    try:
        integration = await drive_service.exchange_code_and_store(db, code=code, state=state)
        email_str = f" for <b>{integration.connected_email}</b>" if integration.connected_email else ""
        return HTMLResponse(
            content=f"""
            <html>
                <body style="font-family: Arial, sans-serif; text-align: center; padding: 50px;">
                    <div style="max-width: 500px; margin: auto; padding: 24px; border: 1px solid #e5e7eb; border-radius: 12px; box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1);">
                        <h2 style="color: #10b981; margin-bottom: 12px;">✅ Google Drive Connected!</h2>
                        <p style="color: #374151; font-size: 16px;">
                            Your Google account{email_str} has been securely connected.
                        </p>
                        <p style="color: #6b7280; font-size: 14px;">
                            Waasz will now automatically back up your monthly transaction summaries to your personal Google Drive in the <b>Waasz Backups</b> folder.
                        </p>
                        <div style="margin-top: 24px; padding: 12px; background-color: #f3f4f6; border-radius: 8px;">
                            <p style="margin: 0; font-weight: bold; color: #1f2937;">You can safely close this window and return to WhatsApp.</p>
                        </div>
                    </div>
                </body>
            </html>
            """
        )
    except Exception as exc:
        logger.error("Failed to process Google OAuth callback: %s", exc)
        return HTMLResponse(
            content=f"""
            <html>
                <body style="font-family: Arial, sans-serif; text-align: center; padding: 50px;">
                    <h2 style="color: #ef4444;">Connection Error</h2>
                    <p>{str(exc)}</p>
                    <p>Please try connecting again from WhatsApp.</p>
                </body>
            </html>
            """,
            status_code=500,
        )


@router.get("/status/{business_id}")
async def get_drive_status(
    business_id: UUID,
    db: AsyncSession = Depends(get_db),
):
    integration = await drive_service.get_integration(db, business_id)
    if not integration:
        return {"connected": False}
    return {
        "connected": True,
        "email": integration.connected_email,
        "folder_id": integration.folder_id,
        "last_backup_at": integration.last_backup_at.isoformat() if integration.last_backup_at else None,
    }


@router.post("/trigger-backup/{business_id}")
async def trigger_manual_backup(
    business_id: UUID,
    db: AsyncSession = Depends(get_db),
):
    result = await drive_service.export_monthly_backup_for_business(db, business_id)
    return result
