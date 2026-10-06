import logging
import httpx

from app.core.config import settings
from app.schemas.whatsapp import WhatsAppSendResult

logger = logging.getLogger(__name__)


class WhatsAppClient:
    def __init__(self) -> None:
        self.base_url = (
            f"https://graph.facebook.com/{settings.whatsapp_api_version}/"
            f"{settings.whatsapp_phone_number_id}/messages"
        )

    async def send_text(self, to_phone: str, body: str) -> WhatsAppSendResult:
        if not self._is_configured:
            return WhatsAppSendResult(
                skipped=True, error_message="WhatsApp credentials are not set"
            )
        payload = {
            "messaging_product": "whatsapp",
            "to": to_phone,
            "type": "text",
            "text": {"preview_url": False, "body": body},
        }
        return await self._post(payload)

    async def send_confirmation(self, to_phone: str, body: str) -> WhatsAppSendResult:
        if not self._is_configured:
            return WhatsAppSendResult(
                skipped=True, error_message="WhatsApp credentials are not set"
            )
        payload = {
            "messaging_product": "whatsapp",
            "to": to_phone,
            "type": "interactive",
            "interactive": {
                "type": "button",
                "body": {"text": body},
                "action": {
                    "buttons": [
                        {"type": "reply", "reply": {"id": "1", "title": "1 Yes"}},
                        {"type": "reply", "reply": {"id": "2", "title": "2 Edit"}},
                    ]
                },
            },
        }
        return await self._post(payload)

    async def send_alarm_reminder(
        self, to_phone: str, body: str, task_id: str | None = None
    ) -> WhatsAppSendResult:
        """Send an alarm-mode reminder with interactive 'Turn Off' and 'Snooze 10 min' buttons."""
        if not self._is_configured:
            return WhatsAppSendResult(
                skipped=True, error_message="WhatsApp credentials are not set"
            )
        turn_off_id = f"alarm_off_{task_id}" if task_id else "alarm_off"
        snooze_id = f"alarm_snooze_{task_id}" if task_id else "alarm_snooze"
        payload = {
            "messaging_product": "whatsapp",
            "to": to_phone,
            "type": "interactive",
            "interactive": {
                "type": "button",
                "body": {"text": body},
                "action": {
                    "buttons": [
                        {"type": "reply", "reply": {"id": turn_off_id, "title": "🔕 Turn Off"}},
                        {"type": "reply", "reply": {"id": snooze_id, "title": "⏰ Snooze 10 min"}},
                    ]
                },
            },
        }
        return await self._post(payload)

    async def send_image(
        self, to_phone: str, media_id: str, caption: str | None = None
    ) -> WhatsAppSendResult:
        if not self._is_configured:
            return WhatsAppSendResult(
                skipped=True, error_message="WhatsApp credentials are not set"
            )
        payload = {
            "messaging_product": "whatsapp",
            "to": to_phone,
            "type": "image",
            "image": {"id": media_id, "caption": caption} if caption else {"id": media_id},
        }
        return await self._post(payload)

    async def send_document(
        self, to_phone: str, media_id: str, filename: str, caption: str | None = None
    ) -> WhatsAppSendResult:
        if not self._is_configured:
            return WhatsAppSendResult(
                skipped=True, error_message="WhatsApp credentials are not set"
            )
        payload = {
            "messaging_product": "whatsapp",
            "to": to_phone,
            "type": "document",
            "document": {
                "id": media_id,
                "filename": filename,
                "caption": caption,
            },
        }
        return await self._post(payload)

    async def send_document_bytes(
        self,
        to_phone: str,
        file_bytes: bytes,
        filename: str,
        caption: str | None = None,
        mime_type: str = "application/pdf",
    ) -> WhatsAppSendResult:
        """Upload document bytes to Meta and send to the specified WhatsApp phone number."""
        if not self._is_configured:
            return WhatsAppSendResult(
                skipped=True, error_message="WhatsApp credentials are not set"
            )
        media_id = await self.upload_media(file_bytes, mime_type)
        if not media_id:
            return WhatsAppSendResult(error_message="Failed to upload document to WhatsApp media service")
        return await self.send_document(to_phone, media_id, filename, caption)

    async def send_interactive_buttons(
        self, to_phone: str, body: str, buttons: list[tuple[str, str]]
    ) -> WhatsAppSendResult:
        """Send a message with custom quick-reply buttons (id, label)."""
        if not self._is_configured:
            return WhatsAppSendResult(
                skipped=True, error_message="WhatsApp credentials are not set"
            )
        btn_payloads = [
            {"type": "reply", "reply": {"id": b_id, "title": b_title[:20]}}
            for b_id, b_title in buttons[:3]
        ]
        payload = {
            "messaging_product": "whatsapp",
            "to": to_phone,
            "type": "interactive",
            "interactive": {
                "type": "button",
                "body": {"text": body},
                "action": {"buttons": btn_payloads},
            },
        }
        return await self._post(payload)

    async def upload_media(self, file_content: bytes, mime_type: str) -> str | None:
        """Upload media to Meta and return the media ID."""
        if not self._is_configured:
            return None
        
        url = f"https://graph.facebook.com/{settings.whatsapp_api_version}/{settings.whatsapp_phone_number_id}/media"
        headers = {"Authorization": f"Bearer {settings.whatsapp_access_token}"}
        
        files = {
            "file": ("file", file_content, mime_type),
            "type": (None, mime_type),
            "messaging_product": (None, "whatsapp"),
        }
        
        async with httpx.AsyncClient(timeout=30) as client:
            try:
                response = await client.post(url, files=files, headers=headers)
                response.raise_for_status()
                return response.json().get("id")
            except Exception as exc:
                logger.error(f"WhatsApp media upload failed: {exc}")
                return None

    @property
    def _is_configured(self) -> bool:
        return bool(settings.whatsapp_access_token and settings.whatsapp_phone_number_id)

    async def _post(self, payload: dict) -> WhatsAppSendResult:
        headers = {"Authorization": f"Bearer {settings.whatsapp_access_token}"}
        async with httpx.AsyncClient(timeout=20) as client:
            try:
                response = await client.post(self.base_url, json=payload, headers=headers)
                response.raise_for_status()
                data = response.json()
                message_id = (data.get("messages") or [{}])[0].get("id")
                return WhatsAppSendResult(message_id=message_id, success=True)
            except httpx.HTTPStatusError as exc:
                try:
                    err_json = exc.response.json()
                    msg = err_json.get("error", {}).get("message") or exc.response.text
                    return WhatsAppSendResult(error_message=f"Meta Error ({exc.response.status_code}): {msg}", success=False)
                except Exception:
                    return WhatsAppSendResult(error_message=f"HTTP {exc.response.status_code}: {exc.response.text or str(exc)}", success=False)
            except httpx.HTTPError as exc:
                return WhatsAppSendResult(error_message=str(exc), success=False)

    async def send_template(
        self,
        to_phone: str,
        template_name: str,
        language_code: str = "en",
        components: list[dict] | None = None,
    ) -> WhatsAppSendResult:
        """
        Send a pre-approved Meta message template (mandatory for messages outside the 24h window).
        """
        if not self._is_configured:
            return WhatsAppSendResult(
                skipped=True, error_message="WhatsApp credentials are not set"
            )
        payload = {
            "messaging_product": "whatsapp",
            "to": to_phone,
            "type": "template",
            "template": {
                "name": template_name,
                "language": {"code": language_code},
                "components": components or [],
            },
        }
        return await self._post(payload)

    async def verify_waba_oba_status(self) -> dict:
        """
        Explicit live check against Meta Graph API for Official Business Account (OBA) status.
        Requires live credentials.
        """
        if not self._is_configured:
            return {
                "configured": False,
                "is_official_business_account": False,
                "reason": "Credentials are unconfigured or placeholder",
            }
        url = (
            f"https://graph.facebook.com/{settings.whatsapp_api_version}/"
            f"{settings.whatsapp_phone_number_id}?fields=is_official_business_account,verified_name,code_verification_status"
        )
        headers = {"Authorization": f"Bearer {settings.whatsapp_access_token}"}
        async with httpx.AsyncClient(timeout=15) as client:
            try:
                res = await client.get(url, headers=headers)
                if res.status_code == 200:
                    data = res.json()
                    return {
                        "configured": True,
                        "is_official_business_account": bool(data.get("is_official_business_account", False)),
                        "verified_name": data.get("verified_name"),
                        "raw": data,
                    }
                return {
                    "configured": True,
                    "is_official_business_account": False,
                    "error": f"HTTP {res.status_code}: {res.text}",
                }
            except Exception as exc:
                return {
                    "configured": True,
                    "is_official_business_account": False,
                    "error": str(exc),
                }
