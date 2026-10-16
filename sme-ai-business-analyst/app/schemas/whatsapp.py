from datetime import datetime

from pydantic import BaseModel, model_validator


class ParsedWhatsAppMessage(BaseModel):
    message_id: str
    from_phone: str
    message_type: str
    body: str | None = None
    media_id: str | None = None
    interactive_reply_id: str | None = None
    timestamp: datetime | None = None
    raw_payload: dict = {}


class ParsedWhatsAppStatus(BaseModel):
    message_id: str
    recipient_id: str | None = None
    status: str
    timestamp: datetime | None = None
    error_code: int | None = None
    error_message: str | None = None
    raw_payload: dict = {}


class WhatsAppSendResult(BaseModel):
    message_id: str | None = None
    skipped: bool = False
    error_message: str | None = None
    success: bool | None = None

    @model_validator(mode="after")
    def compute_success(self):
        if self.success is None:
            self.success = bool(self.message_id and not self.error_message and not self.skipped)
        return self
