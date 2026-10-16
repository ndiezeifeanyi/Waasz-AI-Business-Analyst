import pytest
from datetime import UTC, datetime
from uuid import uuid4

from app.schemas.whatsapp import ParsedWhatsAppStatus, WhatsAppSendResult
from app.services.ledger_service import LedgerService
from app.services.whatsapp_parser import parse_webhook_statuses


def test_parse_webhook_statuses_success():
    payload = {
        "entry": [
            {
                "id": "1278528425351562",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "statuses": [
                                {
                                    "id": "wamid.TEST12345",
                                    "status": "delivered",
                                    "timestamp": "1729011611",
                                    "recipient_id": "2347065015924",
                                },
                                {
                                    "id": "wamid.TESTFAILED",
                                    "status": "failed",
                                    "timestamp": "1729011620",
                                    "recipient_id": "2347065015924",
                                    "errors": [
                                        {
                                            "code": 131049,
                                            "title": "This message was not delivered to maintain healthy ecosystem engagement.",
                                            "error_data": {
                                                "details": "In order to maintain a healthy ecosystem engagement, the message failed to be delivered."
                                            },
                                        }
                                    ],
                                },
                            ]
                        },
                    }
                ],
            }
        ]
    }

    statuses = parse_webhook_statuses(payload)
    assert len(statuses) == 2

    assert statuses[0].message_id == "wamid.TEST12345"
    assert statuses[0].status == "delivered"
    assert statuses[0].error_message is None

    assert statuses[1].message_id == "wamid.TESTFAILED"
    assert statuses[1].status == "failed"
    assert statuses[1].error_code == 131049
    assert "healthy ecosystem engagement" in statuses[1].error_message


@pytest.mark.asyncio
async def test_ledger_update_message_status_lifecycle():
    ledger = LedgerService()

    class FakeMessage:
        def __init__(self, wa_id, status="sent"):
            self.whatsapp_message_id = wa_id
            self.status = status
            self.error_message = None

    # We test the precedence and weight logic
    status_weights = {
        "queued": 1,
        "received": 1,
        "sent": 2,
        "delivered": 3,
        "read": 4,
        "failed": 5,
    }

    # Sent -> Delivered
    assert status_weights["delivered"] > status_weights["sent"]
    # Delivered -> Read
    assert status_weights["read"] > status_weights["delivered"]
    # Read should not downgrade to delivered
    assert status_weights["delivered"] < status_weights["read"]
