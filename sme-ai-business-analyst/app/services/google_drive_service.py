import base64
from datetime import UTC, datetime
import hashlib
import json
import logging
from typing import Any
from urllib.parse import urlencode
from uuid import UUID

from cryptography.fernet import Fernet
import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.business import Business
from app.models.google_drive_integration import GoogleDriveIntegration
from app.models.user import User

logger = logging.getLogger(__name__)

DRIVE_FILE_SCOPE = "https://www.googleapis.com/auth/drive.file"
GOOGLE_OAUTH_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_OAUTH_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_OAUTH_REVOKE_URL = "https://oauth2.googleapis.com/revoke"
GOOGLE_DRIVE_API_BASE = "https://www.googleapis.com/drive/v3"
GOOGLE_DRIVE_UPLOAD_BASE = "https://www.googleapis.com/upload/drive/v3"


class GoogleDriveService:
    """
    Manages Google Drive OAuth integration, least-privilege token storage (Fernet encrypted),
    and automated monthly PDF report uploads.
    """

    def __init__(self):
        self._cipher = self._build_cipher()

    def _build_cipher(self) -> Fernet:
        secret = settings.secret_key or "default-insecure-key-change-me"
        key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest())
        return Fernet(key)

    def encrypt_token(self, token: str) -> str:
        return self._cipher.encrypt(token.encode("utf-8")).decode("utf-8")

    def decrypt_token(self, encrypted_token: str) -> str:
        return self._cipher.decrypt(encrypted_token.encode("utf-8")).decode("utf-8")

    def generate_state(self, business_id: UUID, user_id: UUID | None = None) -> str:
        payload = {
            "business_id": str(business_id),
            "user_id": str(user_id) if user_id else None,
            "ts": datetime.now(UTC).timestamp(),
        }
        raw_json = json.dumps(payload)
        return self.encrypt_token(raw_json)

    def parse_state(self, state_str: str) -> dict[str, Any]:
        raw_json = self.decrypt_token(state_str)
        return json.loads(raw_json)

    def generate_auth_url(self, business_id: UUID, user_id: UUID | None = None) -> str:
        """
        Builds Google OAuth authorization URL requesting least-privilege drive.file scope.
        """
        state = self.generate_state(business_id=business_id, user_id=user_id)
        params = {
            "client_id": settings.google_client_id or "GOOGLE_CLIENT_ID_PLACEHOLDER",
            "redirect_uri": settings.google_oauth_redirect_uri,
            "response_type": "code",
            "scope": f"{DRIVE_FILE_SCOPE} https://www.googleapis.com/auth/userinfo.email",
            "access_type": "offline",
            "prompt": "consent",
            "state": state,
        }
        return f"{GOOGLE_OAUTH_AUTH_URL}?{urlencode(params)}"

    async def get_integration(
        self, db: AsyncSession, business_id: UUID
    ) -> GoogleDriveIntegration | None:
        stmt = select(GoogleDriveIntegration).where(
            GoogleDriveIntegration.business_id == business_id,
            GoogleDriveIntegration.is_active == True,  # noqa: E712
        )
        res = await db.execute(stmt)
        return res.scalar_one_or_none()

    async def exchange_code_and_store(
        self,
        db: AsyncSession,
        code: str,
        state: str,
    ) -> GoogleDriveIntegration:
        """
        Exchanges authorization code for tokens, encrypts refresh token, and saves integration.
        """
        state_data = self.parse_state(state)
        business_id = UUID(state_data["business_id"])
        user_id = UUID(state_data["user_id"]) if state_data.get("user_id") else None

        async with httpx.AsyncClient(timeout=15.0) as client:
            token_resp = await client.post(
                GOOGLE_OAUTH_TOKEN_URL,
                data={
                    "code": code,
                    "client_id": settings.google_client_id,
                    "client_secret": settings.google_client_secret,
                    "redirect_uri": settings.google_oauth_redirect_uri,
                    "grant_type": "authorization_code",
                },
            )
            token_data = token_resp.json()
            if token_resp.status_code != 200:
                err_msg = token_data.get("error_description") or token_data.get("error") or "OAuth exchange failed"
                raise ValueError(f"Google OAuth token exchange failed: {err_msg}")

            refresh_token = token_data.get("refresh_token")
            access_token = token_data.get("access_token")

            # Fetch user email for display
            connected_email = None
            if access_token:
                try:
                    userinfo_resp = await client.get(
                        "https://www.googleapis.com/oauth2/v2/userinfo",
                        headers={"Authorization": f"Bearer {access_token}"},
                    )
                    if userinfo_resp.status_code == 200:
                        connected_email = userinfo_resp.json().get("email")
                except Exception as exc:
                    logger.warning("Could not fetch Google user email: %s", exc)

        # Existing integration check
        stmt = select(GoogleDriveIntegration).where(GoogleDriveIntegration.business_id == business_id)
        res = await db.execute(stmt)
        integration = res.scalar_one_or_none()

        encrypted_rf = self.encrypt_token(refresh_token) if refresh_token else (
            integration.encrypted_refresh_token if integration else ""
        )
        if not encrypted_rf:
            raise ValueError("No refresh token received from Google. User may need to revoke access and reconnect.")

        if not integration:
            integration = GoogleDriveIntegration(
                business_id=business_id,
                user_id=user_id,
                encrypted_refresh_token=encrypted_rf,
                connected_email=connected_email,
                is_active=True,
            )
            db.add(integration)
        else:
            integration.encrypted_refresh_token = encrypted_rf
            if connected_email:
                integration.connected_email = connected_email
            integration.is_active = True
            integration.updated_at = datetime.now(UTC)

        await db.commit()
        await db.refresh(integration)
        return integration

    async def disconnect(self, db: AsyncSession, business_id: UUID) -> bool:
        """
        Disconnects Google Drive: revokes token with Google and deactivates database record.
        """
        integration = await self.get_integration(db, business_id)
        if not integration:
            return False

        # Attempt token revocation at Google
        try:
            refresh_token = self.decrypt_token(integration.encrypted_refresh_token)
            async with httpx.AsyncClient(timeout=10.0) as client:
                await client.post(
                    GOOGLE_OAUTH_REVOKE_URL,
                    params={"token": refresh_token},
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
        except Exception as exc:
            logger.warning("Error revoking Google token: %s", exc)

        integration.is_active = False
        integration.encrypted_refresh_token = ""
        await db.commit()
        return True

    async def get_valid_access_token(self, integration: GoogleDriveIntegration) -> str:
        """
        Refreshes and returns a valid access token using the decrypted refresh token.
        """
        refresh_token = self.decrypt_token(integration.encrypted_refresh_token)
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                GOOGLE_OAUTH_TOKEN_URL,
                data={
                    "client_id": settings.google_client_id,
                    "client_secret": settings.google_client_secret,
                    "refresh_token": refresh_token,
                    "grant_type": "refresh_token",
                },
            )
            data = resp.json()
            if resp.status_code != 200:
                raise ValueError(f"Failed to refresh Google access token: {data.get('error')}")
            return data["access_token"]

    async def ensure_backup_folder(
        self, access_token: str, business_name: str
    ) -> str:
        """
        Finds or creates a dedicated 'Waasz Backups - {business_name}' folder in user's Drive.
        """
        folder_name = f"Waasz Backups - {business_name}"
        headers = {"Authorization": f"Bearer {access_token}"}

        async with httpx.AsyncClient(timeout=15.0) as client:
            # Search existing
            query = f"name = '{folder_name}' and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
            search_resp = await client.get(
                f"{GOOGLE_DRIVE_API_BASE}/files",
                headers=headers,
                params={"q": query, "spaces": "drive", "fields": "files(id, name)"},
            )
            if search_resp.status_code == 200:
                files = search_resp.json().get("files", [])
                if files:
                    return files[0]["id"]

            # Create folder
            create_resp = await client.post(
                f"{GOOGLE_DRIVE_API_BASE}/files",
                headers={**headers, "Content-Type": "application/json"},
                json={
                    "name": folder_name,
                    "mimeType": "application/vnd.google-apps.folder",
                },
            )
            create_data = create_resp.json()
            if create_resp.status_code not in (200, 201):
                raise ValueError(f"Failed to create Google Drive folder: {create_data}")
            return create_data["id"]

    async def upload_pdf(
        self,
        access_token: str,
        folder_id: str,
        filename: str,
        pdf_bytes: bytes,
    ) -> dict[str, Any]:
        """
        Uploads PDF to user's Google Drive inside the specified folder.
        Uses multipart upload for metadata + binary payload.
        """
        metadata = {
            "name": filename,
            "parents": [folder_id],
            "mimeType": "application/pdf",
        }

        boundary = "===============7330837574892837472=="
        body = (
            f"--{boundary}\r\n"
            f"Content-Type: application/json; charset=UTF-8\r\n\r\n"
            f"{json.dumps(metadata)}\r\n"
            f"--{boundary}\r\n"
            f"Content-Type: application/pdf\r\n\r\n"
        ).encode("utf-8") + pdf_bytes + f"\r\n--{boundary}--\r\n".encode("utf-8")

        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": f"multipart/related; boundary={boundary}",
            "Content-Length": str(len(body)),
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            upload_resp = await client.post(
                f"{GOOGLE_DRIVE_UPLOAD_BASE}/files?uploadType=multipart",
                headers=headers,
                content=body,
            )
            data = upload_resp.json()
            if upload_resp.status_code not in (200, 201):
                raise ValueError(f"Google Drive upload failed: {data}")
            return data

    async def export_monthly_backup_for_business(
        self,
        db: AsyncSession,
        business_id: UUID,
    ) -> dict[str, Any]:
        """
        Executes monthly backup: generates monthly activity PDF and uploads to Google Drive.
        """
        integration = await self.get_integration(db, business_id)
        if not integration:
            return {"status": "skipped", "reason": "Drive not connected"}

        biz = await db.get(Business, business_id)
        biz_name = biz.name if biz else "Business"

        # Obtain valid token
        access_token = await self.get_valid_access_token(integration)

        # Ensure folder exists
        folder_id = integration.folder_id
        if not folder_id:
            folder_id = await self.ensure_backup_folder(access_token, biz_name)
            integration.folder_id = folder_id
            await db.commit()

        # Generate monthly PDF report (reuses ReceiptService ReportLab pipeline)
        from app.services.receipt_service import ReceiptService
        receipt_svc = ReceiptService()
        pdf_bytes, rec_filename, _ = await receipt_svc.generate_receipt_pdf(db, business_id)

        now = datetime.now(UTC)
        backup_filename = f"waasz_monthly_backup_{now.strftime('%Y_%m')}_{business_id.hex[:6]}.pdf"

        # Upload
        file_info = await self.upload_pdf(
            access_token=access_token,
            folder_id=folder_id,
            filename=backup_filename,
            pdf_bytes=pdf_bytes,
        )

        integration.last_backup_at = datetime.now(UTC)
        await db.commit()

        return {
            "status": "success",
            "file_id": file_info.get("id"),
            "filename": backup_filename,
            "folder_id": folder_id,
        }

    async def run_all_monthly_backups(self, db: AsyncSession) -> dict[str, Any]:
        """
        Scheduled job runner: iterates all businesses with active Drive integrations and uploads backups.
        """
        stmt = select(GoogleDriveIntegration).where(GoogleDriveIntegration.is_active == True)  # noqa: E712
        res = await db.execute(stmt)
        active_integrations = res.scalars().all()

        results = {"total": len(active_integrations), "succeeded": 0, "failed": 0, "details": []}
        for item in active_integrations:
            try:
                res_item = await self.export_monthly_backup_for_business(db, item.business_id)
                results["succeeded"] += 1
                results["details"].append({"business_id": str(item.business_id), "result": res_item})
            except Exception as exc:
                results["failed"] += 1
                results["details"].append({"business_id": str(item.business_id), "error": str(exc)})
                logger.error("Failed Google Drive monthly backup for business %s: %s", item.business_id, exc)

        return results
