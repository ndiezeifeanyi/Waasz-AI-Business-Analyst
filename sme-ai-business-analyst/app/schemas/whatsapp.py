from datetime import datetime

from pydantic import BaseModel


class ParsedWhatsAppMessage(BaseModel):
    message_id: str
    from_phone: str
    message_type: str
    body: str | None = None
    media_id: str | None = None
    interactive_reply_id: str | None = None
    timestamp: datetime | None = None
    raw_payload: dict = {}


class WhatsAppSendResult(BaseModel):
    message_id: str | None = None
    skipped: bool = False
    error_message: str | None = None
