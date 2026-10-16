from datetime import UTC, datetime

from app.schemas.whatsapp import ParsedWhatsAppMessage, ParsedWhatsAppStatus


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


def parse_webhook_statuses(payload: dict) -> list[ParsedWhatsAppStatus]:
    statuses: list[ParsedWhatsAppStatus] = []
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            for status_item in value.get("statuses", []):
                parsed = _parse_status(status_item)
                if parsed:
                    statuses.append(parsed)
    return statuses


def _parse_status(status_item: dict) -> ParsedWhatsAppStatus | None:
    message_id = status_item.get("id")
    status = status_item.get("status")
    if not message_id or not status:
        return None

    recipient_id = status_item.get("recipient_id")
    timestamp = None
    if status_item.get("timestamp"):
        try:
            timestamp = datetime.fromtimestamp(int(status_item["timestamp"]), tz=UTC)
        except (ValueError, TypeError):
            pass

    error_code = None
    error_message = None
    errors = status_item.get("errors", [])
    if errors and isinstance(errors, list):
        first_err = errors[0]
        error_code = first_err.get("code")
        title_or_msg = first_err.get("title") or first_err.get("message")
        details = first_err.get("error_data", {}).get("details")
        if details and details != title_or_msg:
            error_message = f"{title_or_msg}: {details}"
        else:
            error_message = title_or_msg

    return ParsedWhatsAppStatus(
        message_id=message_id,
        recipient_id=recipient_id,
        status=status,
        timestamp=timestamp,
        error_code=error_code,
        error_message=error_message,
        raw_payload=status_item,
    )


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
