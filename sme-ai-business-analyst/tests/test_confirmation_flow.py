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

