from datetime import UTC, datetime

from app.schemas.whatsapp import ParsedWhatsAppMessage


def parse_webhook_payload(payload: dict) -> list[ParsedWhatsAppMessage]:
    messages: list[ParsedWhatsAppMessage] = []
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            for message in value.get("messages", []):
                parsed = _parse_message(message)
                if parsed:
                    messages.append(parsed)
    return messages


def _parse_message(message: dict) -> ParsedWhatsAppMessage | None:
    message_id = message.get("id")
    from_phone = message.get("from")
    if not message_id or not from_phone:
        return None

    message_type = message.get("type", "unknown")
    body: str | None = None
    media_id: str | None = None
    interactive_reply_id: str | None = None

    if message_type == "text":
        body = message.get("text", {}).get("body")
    elif message_type in {"image", "audio", "document"}:
        media = message.get(message_type, {})
        media_id = media.get("id")
        body = media.get("caption")
    elif message_type == "interactive":
        interactive = message.get("interactive", {})
        button_reply = interactive.get("button_reply") or {}
        list_reply = interactive.get("list_reply") or {}
        interactive_reply_id = button_reply.get("id") or list_reply.get("id")
        body = button_reply.get("title") or list_reply.get("title")
    elif message_type == "button":
        button = message.get("button", {})
        interactive_reply_id = button.get("payload")
        body = button.get("text")

    timestamp = None
    if message.get("timestamp"):
        timestamp = datetime.fromtimestamp(int(message["timestamp"]), tz=UTC)

    return ParsedWhatsAppMessage(
        message_id=message_id,
        from_phone=from_phone,
        message_type=message_type,
        body=body,
        media_id=media_id,
        interactive_reply_id=interactive_reply_id,
        timestamp=timestamp,
        raw_payload=message,
    )
