from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import app
from app.services.whatsapp_parser import parse_webhook_payload


def test_default_whatsapp_verify_token_is_configurable() -> None:
    assert settings.whatsapp_verify_token


def test_whatsapp_webhook_verification_accepts_valid_token() -> None:
    client = TestClient(app)
    response = client.get(
        "/webhooks/whatsapp",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": settings.whatsapp_verify_token,
            "hub.challenge": "challenge-123",
        },
    )
    assert response.status_code == 200
    assert response.text == "challenge-123"


def test_whatsapp_parser_handles_text_message() -> None:
    payload = {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "messages": [
                                {
                                    "from": "2348000000000",
                                    "id": "wamid.test",
                                    "timestamp": "1710000000",
                                    "type": "text",
                                    "text": {"body": "Sold 5 bags rice for 250000"},
                                }
                            ]
                        }
                    }
                ]
            }
        ]
    }
    messages = parse_webhook_payload(payload)
    assert len(messages) == 1
    assert messages[0].body == "Sold 5 bags rice for 250000"
    assert messages[0].message_type == "text"
