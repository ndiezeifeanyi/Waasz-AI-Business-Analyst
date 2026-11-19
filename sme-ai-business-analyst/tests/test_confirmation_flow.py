from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
import pytest

from app.schemas.confirmation import ConfirmationDecision
from app.schemas.extraction import ExtractedRecord
from app.services.confirmation_service import build_confirmation_text, normalize_confirmation_reply


def test_confirmation_decision_schema_accepts_yes() -> None:
    decision = ConfirmationDecision(token="abc", decision="1")
    assert decision.decision == "1"


def test_confirmation_text_is_mandatory_for_extraction() -> None:
    text = build_confirmation_text(
        ExtractedRecord(
            record_type="sale",
            item_name="rice",
            quantity=5,
            unit="bags",
            amount=250000,
        )
    )
    assert "I understood" in text
    assert "Reply 1=Yes, 2=Edit" in text
    assert "₦250,000" in text


def test_confirmation_reply_normalization() -> None:
    assert normalize_confirmation_reply("1") == "confirm"
    assert normalize_confirmation_reply(interactive_reply_id="2") == "edit"
    assert normalize_confirmation_reply("maybe") == "unknown"


@pytest.mark.asyncio
async def test_stage_record_for_confirmation_supersedes_older_pending_confirmations() -> None:
    from app.services.ledger_service import LedgerService
    from app.models.confirmation import Confirmation
    from app.models.transaction import Transaction
    from app.models.extraction import AiExtraction
    from uuid import uuid4

    service = LedgerService()
    mock_db = AsyncMock()

    biz_id = uuid4()
    msg_id = uuid4()

    # Suppose there is an existing pending confirmation
    old_conf = Confirmation(
        id=uuid4(),
        business_id=biz_id,
        status="pending",
    )
    old_tx = Transaction(
        id=uuid4(),
        business_id=biz_id,
        confirmation_id=old_conf.id,
        status="pending_confirmation",
    )

    result_mock_conf = MagicMock()
    result_mock_conf.scalars.return_value.all.return_value = [old_conf]

    result_mock_tx = MagicMock()
    result_mock_tx.scalars.return_value.all.return_value = [old_tx]

    result_mock_mv = MagicMock()
    result_mock_mv.scalars.return_value.all.return_value = []

    mock_db.execute.side_effect = [
        result_mock_conf,
        result_mock_tx,
        result_mock_mv,
    ]

    extraction = AiExtraction(id=uuid4(), business_id=biz_id)
    new_record = ExtractedRecord(
        record_type="sale",
        item_name="rice",
        quantity=10,
        amount=20000,
    )

    new_conf = await service.stage_record_for_confirmation(
        mock_db, biz_id, msg_id, extraction, new_record
    )

    # Verify old confirmation and transaction were rejected/superseded
    assert old_conf.status == "rejected"
    assert old_tx.status == "rejected"
    assert new_conf.status == "pending"


@pytest.mark.asyncio
async def test_duplicate_wamid_inbound_is_idempotent() -> None:
    from app.services.ledger_service import LedgerService
    from app.schemas.whatsapp import ParsedWhatsAppMessage
    from app.models.business import Business
    from app.models.user import User
    from app.models.message import WhatsAppMessage
    from app.utils.idempotency import is_duplicate_message
    from uuid import uuid4

    service = LedgerService()
    mock_db = AsyncMock()

    biz = Business(id=uuid4(), name="Test Biz", phone_number="2348000000001")
    user = User(id=uuid4(), phone_number="2348000000001", business_id=biz.id)

    parsed = ParsedWhatsAppMessage(
        message_id="wamid.HBgNMjM0NzA2NTAxNTkyNBUCABIYFDNBOUEwNDAyRkRCNDgwQTI0MjY2AA==",
        from_phone="2348000000001",
        body="1 Yes",
        timestamp=datetime.now(UTC),
        message_type="text",
    )

    # First delivery: no existing row in database
    mock_res_none = MagicMock()
    mock_res_none.scalar_one_or_none.return_value = None
    mock_db.execute.return_value = mock_res_none

    inbound1 = await service.record_inbound_message(mock_db, parsed, biz, user)
    assert not is_duplicate_message(inbound1)

    # Second delivery (Meta duplicate retry of the same WAMID): database already has the record
    existing_msg = WhatsAppMessage(
        id=uuid4(),
        whatsapp_message_id=parsed.message_id,
        status="received",
    )
    mock_res_existing = MagicMock()
    mock_res_existing.scalar_one_or_none.return_value = existing_msg
    mock_db.execute.return_value = mock_res_existing

    inbound2 = await service.record_inbound_message(mock_db, parsed, biz, user)
    assert is_duplicate_message(inbound2)


@pytest.mark.asyncio
async def test_send_confirmation_splits_long_body_over_1024_chars(monkeypatch):
    """Batch confirmations > 1024 chars must send plain text first, then short interactive buttons."""
    from unittest.mock import AsyncMock
    from app.schemas.whatsapp import WhatsAppSendResult
    from app.services.whatsapp_client import WhatsAppClient

    client = WhatsAppClient()
    monkeypatch.setattr(WhatsAppClient, "_is_configured", property(lambda self: True))
    client._post = AsyncMock(
        side_effect=[
            WhatsAppSendResult(message_id="wamid.text_1", success=True),
            WhatsAppSendResult(message_id="wamid.btn_1", success=True),
        ]
    )

    long_body = "Please confirm 30 item sales:\n" + ("1. Spare part item - ₦25,000\n" * 40)
    assert len(long_body) > 1024

    res = await client.send_confirmation("2347065015924", long_body)
    assert res.success is True
    assert client._post.await_count == 2

    first_payload = client._post.await_args_list[0].args[0]
    second_payload = client._post.await_args_list[1].args[0]
    assert first_payload["type"] == "text"
    assert first_payload["text"]["body"] == long_body
    assert second_payload["type"] == "interactive"
    assert len(second_payload["interactive"]["body"]["text"]) <= 1024
    assert [b["reply"]["id"] for b in second_payload["interactive"]["action"]["buttons"]] == ["1", "2"]


@pytest.mark.asyncio
async def test_send_confirmation_falls_back_to_text_on_131009(monkeypatch):
    """If Meta rejects interactive confirmation with #131009, fall back to plain text."""
    from unittest.mock import AsyncMock
    from app.schemas.whatsapp import WhatsAppSendResult
    from app.services.whatsapp_client import WhatsAppClient

    client = WhatsAppClient()
    monkeypatch.setattr(WhatsAppClient, "_is_configured", property(lambda self: True))
    client._post = AsyncMock(
        side_effect=[
            WhatsAppSendResult(
                error_message="Meta Error (400): (#131009) Parameter value is not valid",
                success=False,
            ),
            WhatsAppSendResult(message_id="wamid.fallback_1", success=True),
        ]
    )

    short_body = "Sold 1 bag of rice for ₦80,000. Reply 1=Yes, 2=Edit, 3=Cancel."
    res = await client.send_confirmation("2347065015924", short_body)
    assert res.success is True
    assert res.message_id == "wamid.fallback_1"
    assert client._post.await_args_list[1].args[0]["type"] == "text"


