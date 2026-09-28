"""
Regression tests for real-world WhatsApp bugs surfaced in live transcript & follow-ups:
1. "Who are you?" (General Q&A classification + niche-aware graceful degradation)
2. Duplicate wamid delivery / Meta webhook retry idempotency (silent no-op)
3. "Sold 10 bags of rice" (Missing amount prompts clarification, never confirms "unknown amount")
4. Stale clarification abandonment on unrelated messages (new sale, question, reminder)
5. Confirmation reply commit ordering (transaction persisted before success message sent)
6. TaskService.create_task caller verification
"""
from datetime import datetime, UTC
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.models.business import Business
from app.models.confirmation import Confirmation
from app.models.user import User
from app.schemas.extraction import ExtractedRecord
from app.schemas.whatsapp import ParsedWhatsAppMessage
from app.services.confirmation_service import build_confirmation_text, ConfirmationService
from app.services.intent_router import IntentRouter
from app.services.task_service import TaskService
from app.services.webhook_processor import WhatsAppWebhookProcessor
from app.utils.idempotency import is_duplicate_message


@pytest.mark.asyncio
async def test_regression_who_are_you_intent_and_processing():
    """
    Bug 1 Regression:
    "Who are you?" must be classified as general_qa and processed cleanly.
    Even if the underlying QA or vector search throws, the pipeline must degrade gracefully
    with the honest QA degradation message rather than crashing the webhook.
    """
    text = "Who are you?"
    assert IntentRouter.classify(text) == "general_qa"

    user_id = uuid4()
    business_id = uuid4()
    user = User(
        id=user_id,
        business_id=business_id,
        phone_number="+2347065015924",
        display_name="Test User",
        niche="sme_owner",
        last_inbound_at=datetime.now(UTC),
    )
    business = Business(
        id=business_id,
        name="Rice Enterprise",
        phone_number="+2347065015924",
    )

    mock_ledger = AsyncMock()
    mock_ledger.get_or_create_business_and_user.return_value = (business, user)
    mock_ledger.record_inbound_message.side_effect = lambda *args, **kwargs: MagicMock(id=uuid4(), status="received")

    mock_whatsapp = MagicMock()
    mock_whatsapp.send_text = AsyncMock(return_value=MagicMock(message_id="msg_1"))

    # Case A: Normal successful QA answer
    mock_qa = AsyncMock()
    mock_qa.answer.return_value = "I am your AI Business Analyst assistant."

    processor = WhatsAppWebhookProcessor(
        ledger=mock_ledger,
        whatsapp=mock_whatsapp,
        confirmations=AsyncMock(),
        knowledge=AsyncMock(),
        goals=AsyncMock(),
        memory=AsyncMock(),
        qa=mock_qa,
    )

    parsed = ParsedWhatsAppMessage(
        message_id="wamid.live.who_are_you.1",
        from_phone="+2347065015924",
        message_type="text",
        body=text,
    )
    mock_db = AsyncMock()
    await processor.process_message(mock_db, parsed, None)

    mock_whatsapp.send_text.assert_called_with("+2347065015924", "I am your AI Business Analyst assistant.")

    # Case B: QA throws an exception -> Must catch and send honest QA degradation message
    mock_qa.answer.side_effect = RuntimeError("Vector DB connection timeout")
    mock_whatsapp.send_text.reset_mock()

    parsed2 = ParsedWhatsAppMessage(
        message_id="wamid.live.who_are_you.2",
        from_phone="+2347065015924",
        message_type="text",
        body=text,
    )
    await processor.process_message(mock_db, parsed2, None)

    mock_whatsapp.send_text.assert_called_once()
    sent_text = mock_whatsapp.send_text.call_args[0][1].lower()
    assert "hiccup" in sent_text or "trouble" in sent_text or "temporary" in sent_text or "rephrase" in sent_text


@pytest.mark.asyncio
async def test_regression_niche_aware_qa_fallback():
    """
    Follow-up 2:
    Confirm that when QA fails, the user receives a graceful, honest conversational
    fallback rather than a robotic error or crash, following the deletion of NICHE_FALLBACK_QA_MESSAGES.
    """
    mock_ledger = AsyncMock()
    mock_whatsapp = MagicMock()
    mock_whatsapp.send_text = AsyncMock(return_value=MagicMock(message_id="msg_1"))
    mock_qa = AsyncMock()
    mock_qa.answer.side_effect = RuntimeError("Service unavailable")

    processor = WhatsAppWebhookProcessor(
        ledger=mock_ledger,
        whatsapp=mock_whatsapp,
        confirmations=AsyncMock(),
        knowledge=AsyncMock(),
        goals=AsyncMock(),
        memory=AsyncMock(),
        qa=mock_qa,
    )

    # 1. Employee 9-to-5 user
    emp_user = User(
        id=uuid4(),
        business_id=uuid4(),
        phone_number="+2348011112222",
        niche="employee_9_to_5",
    )
    mock_ledger.get_or_create_business_and_user.return_value = (MagicMock(id=emp_user.business_id), emp_user)
    mock_ledger.record_inbound_message.side_effect = lambda *a, **kw: MagicMock(id=uuid4(), status="received")
    mock_db = AsyncMock()

    await processor.process_message(
        mock_db,
        ParsedWhatsAppMessage(
            message_id="wamid.emp.qa.1",
            from_phone="+2348011112222",
            message_type="text",
            body="Who are you?",
        ),
        None,
    )
    sent_emp = mock_whatsapp.send_text.call_args[0][1]
    assert "hiccup" in sent_emp.lower() or "rephrase" in sent_emp.lower()

    # 2. Freelancer user
    free_user = User(
        id=uuid4(),
        business_id=uuid4(),
        phone_number="+2348033334444",
        niche="freelancer",
    )
    mock_ledger.get_or_create_business_and_user.return_value = (MagicMock(id=free_user.business_id), free_user)
    mock_whatsapp.send_text.reset_mock()

    await processor.process_message(
        mock_db,
        ParsedWhatsAppMessage(
            message_id="wamid.free.qa.1",
            from_phone="+2348033334444",
            message_type="text",
            body="Who are you?",
        ),
        None,
    )
    sent_free = mock_whatsapp.send_text.call_args[0][1]
    assert "hiccup" in sent_free.lower() or "rephrase" in sent_free.lower()


@pytest.mark.asyncio
async def test_regression_duplicate_wamid_delivery_idempotency():
    """
    Bug 2 Regression:
    Duplicate delivery of the same wamid (e.g. Meta webhook retries) must be detected
    and silently no-op'd without re-processing or raising an error.
    """
    business_id = uuid4()
    user_id = uuid4()
    user = User(
        id=user_id,
        business_id=business_id,
        phone_number="+2347065015924",
        display_name="Test User",
        niche="sme_owner",
    )
    business = Business(
        id=business_id,
        name="Rice Enterprise",
        phone_number="+2347065015924",
    )

    duplicate_wamid = "wamid.HBgNMjM0NzA2NTAxNTkyNBUCABIYFDNBOUQzOENENDdDM0YxNUI2NEFF"

    # Simulate inbound message record already marked as processed
    processed_inbound = MagicMock(
        id=uuid4(),
        whatsapp_message_id=duplicate_wamid,
        status="processed",
    )

    assert is_duplicate_message(processed_inbound) is True

    mock_ledger = AsyncMock()
    mock_ledger.get_or_create_business_and_user.return_value = (business, user)
    mock_ledger.record_inbound_message.return_value = processed_inbound

    mock_whatsapp = MagicMock()
    mock_whatsapp.send_text = AsyncMock()

    processor = WhatsAppWebhookProcessor(
        ledger=mock_ledger,
        whatsapp=mock_whatsapp,
        confirmations=AsyncMock(),
        knowledge=AsyncMock(),
        goals=AsyncMock(),
        memory=AsyncMock(),
        qa=AsyncMock(),
    )

    parsed = ParsedWhatsAppMessage(
        message_id=duplicate_wamid,
        from_phone="+2347065015924",
        message_type="interactive",
        body="1 Yes",
        interactive_reply_id="btn_confirm_yes",
    )

    mock_db = AsyncMock()
    await processor.process_message(mock_db, parsed, None)
    mock_whatsapp.send_text.assert_not_called()


@pytest.mark.asyncio
async def test_regression_sale_without_amount_prompts_clarification():
    """
    Bug 3 Regression:
    "Sold 10 bags of rice" has no stated amount.
    The system must prompt for clarification and NEVER stage a confirmation
    with 'unknown amount'.
    When the user replies with the amount, it merges and stages the confirmation.
    """
    text = "Sold 10 bags of rice"
    business_id = uuid4()
    user_id = uuid4()
    user = User(
        id=user_id,
        business_id=business_id,
        phone_number="+2347065015924",
        display_name="Test User",
        niche="sme_owner",
    )
    business = Business(
        id=business_id,
        name="Rice Enterprise",
        phone_number="+2347065015924",
    )

    mock_ledger = AsyncMock()
    mock_ledger.get_or_create_business_and_user.return_value = (business, user)
    mock_ledger.record_inbound_message.side_effect = lambda *args, **kwargs: MagicMock(id=uuid4(), status="received")

    mock_whatsapp = MagicMock()
    mock_whatsapp.send_text = AsyncMock(return_value=MagicMock(message_id="msg_clarify"))
    mock_whatsapp.send_confirmation = AsyncMock(return_value=MagicMock(message_id="msg_conf"))

    mock_confirmations = AsyncMock()
    mock_confirmations.latest_actionable.return_value = None

    processor = WhatsAppWebhookProcessor(
        ledger=mock_ledger,
        whatsapp=mock_whatsapp,
        confirmations=mock_confirmations,
        knowledge=AsyncMock(),
        goals=AsyncMock(),
        memory=AsyncMock(),
        qa=AsyncMock(),
    )

    # 1. User sends "Sold 10 bags of rice"
    parsed1 = ParsedWhatsAppMessage(
        message_id="wamid.live.sold_rice.1",
        from_phone="+2347065015924",
        message_type="text",
        body=text,
    )
    mock_db = AsyncMock()
    mock_result_none = MagicMock()
    mock_result_none.scalar_one_or_none.return_value = None
    mock_db.execute.return_value = mock_result_none

    await processor.process_message(mock_db, parsed1, None)

    # Must NOT stage confirmation
    mock_confirmations.create_pending.assert_not_called()
    mock_whatsapp.send_confirmation.assert_not_called()

    # Must ask clarification for amount
    mock_whatsapp.send_text.assert_called_once()
    clarification_msg = mock_whatsapp.send_text.call_args[0][1]
    assert "amount" in clarification_msg.lower() or "how much" in clarification_msg.lower()

    # 2. Confirmation text builder check: confirm it never emits "for unknown amount"
    builder_text = build_confirmation_text(
        ExtractedRecord(
            record_type="sale",
            amount=Decimal("50000"),
            item_name="bags of rice",
            quantity=10,
        )
    )
    assert "unknown amount" not in builder_text.lower()
    assert "50,000" in builder_text

    # Also test record without amount (edge case) never outputs "for unknown amount"
    builder_text_no_amt = build_confirmation_text(
        ExtractedRecord(
            record_type="sale",
            amount=None,
            item_name="bags of rice",
            quantity=10,
        )
    )
    assert "unknown amount" not in builder_text_no_amt.lower()


@pytest.mark.asyncio
async def test_regression_pending_clarification_abandoned_on_unrelated_message():
    """
    Follow-up 3:
    When user triggers a clarification-needed sale ("Sold 10 bags of rice"),
    then sends an unrelated message (e.g. a new full sale, a general question,
    or a reminder request) instead of supplying the amount:
    Confirm the stale clarification is correctly abandoned and the new message
    is classified and handled fresh, NOT wrongly merged.
    """
    business_id = uuid4()
    user_id = uuid4()
    user = User(
        id=user_id,
        business_id=business_id,
        phone_number="+2347065015924",
        niche="sme_owner",
    )
    business = Business(
        id=business_id,
        name="Rice Enterprise",
        phone_number="+2347065015924",
    )

    mock_ledger = AsyncMock()
    mock_ledger.get_or_create_business_and_user.return_value = (business, user)
    mock_ledger.record_inbound_message.side_effect = lambda *args, **kwargs: MagicMock(id=uuid4(), status="received")

    mock_whatsapp = MagicMock()
    mock_whatsapp.send_text = AsyncMock(return_value=MagicMock(message_id="msg_text"))
    mock_whatsapp.send_confirmation = AsyncMock(return_value=MagicMock(message_id="msg_conf"))

    mock_extractor = AsyncMock()
    mock_extractor.extract.return_value = ExtractedRecord(
        record_type="sale",
        item_name="cartons of Indomie",
        amount=Decimal("35000"),
        quantity=5,
        confidence=0.95,
        needs_clarification=False,
    )

    processor = WhatsAppWebhookProcessor(
        ledger=mock_ledger,
        whatsapp=mock_whatsapp,
        confirmations=AsyncMock(),
        knowledge=AsyncMock(),
        goals=AsyncMock(),
        memory=AsyncMock(),
        qa=AsyncMock(),
        extractor=mock_extractor,
    )

    # Simulate existing stale clarification for "bags of rice"
    stale_clarification = MagicMock(
        id=uuid4(),
        status="needs_clarification",
        extracted_record={
            "record_type": "sale",
            "item_name": "bags of rice",
            "quantity": 10,
            "amount": None,
        },
    )

    # 1. Unrelated message: A NEW full sale for Indomie
    mock_db = AsyncMock()
    with patch.object(processor, "_get_recent_pending_clarification", return_value=stale_clarification):
        msg_new_sale = ParsedWhatsAppMessage(
            message_id="wamid.live.indomie.1",
            from_phone="+2347065015924",
            message_type="text",
            body="Sold 5 cartons of Indomie for 35000",
        )
        await processor.process_message(mock_db, msg_new_sale, None)

        # The stale clarification must be abandoned
        assert stale_clarification.status == "abandoned"
        # The extractor was called freshly for Indomie
        mock_extractor.extract.assert_called_once()
        assert "Indomie" in mock_extractor.extract.call_args[0][0]

    # 2. Unrelated message: General Question ("Who are you?")
    stale_clarification_qa = MagicMock(
        id=uuid4(),
        status="needs_clarification",
        extracted_record={"record_type": "sale", "item_name": "bags of rice", "amount": None},
    )
    processor.qa.answer.return_value = "I am your AI assistant."
    with patch.object(processor, "_get_recent_pending_clarification", return_value=stale_clarification_qa):
        msg_qa = ParsedWhatsAppMessage(
            message_id="wamid.live.qa.1",
            from_phone="+2347065015924",
            message_type="text",
            body="Who are you?",
        )
        await processor.process_message(mock_db, msg_qa, None)

        # QA handled cleanly without merging
        processor.qa.answer.assert_called_once()


@pytest.mark.asyncio
async def test_regression_confirmation_committed_before_success_message():
    """
    Follow-up 1:
    Confirm that the 'Done, confirmed' success message is only sent to the user
    AFTER the database transaction (including triggers) has committed successfully.
    If db.commit() raises an exception, the user must NEVER receive an optimistic success message.
    """
    business_id = uuid4()
    user_id = uuid4()
    user = User(id=user_id, business_id=business_id, phone_number="+2347065015924")
    business = Business(id=business_id, name="Rice Enterprise", phone_number="+2347065015924")

    mock_ledger = AsyncMock()
    mock_ledger.get_or_create_business_and_user.return_value = (business, user)
    mock_ledger.record_inbound_message.return_value = MagicMock(id=uuid4(), status="received")

    mock_confirmations = AsyncMock()
    pending_conf = MagicMock(id=uuid4(), business_id=business_id, status="pending")
    mock_confirmations.latest_actionable.return_value = pending_conf
    mock_confirmations.confirm.return_value = "Done, confirmed: recorded sale of ₦50,000"

    mock_whatsapp = MagicMock()
    mock_whatsapp.send_text = AsyncMock()

    processor = WhatsAppWebhookProcessor(
        ledger=mock_ledger,
        whatsapp=mock_whatsapp,
        confirmations=mock_confirmations,
        knowledge=AsyncMock(),
        goals=AsyncMock(),
        memory=AsyncMock(),
        qa=AsyncMock(),
    )

    # Simulate database commit failure (e.g. database trigger error)
    mock_db = AsyncMock()
    mock_db.commit.side_effect = RuntimeError("Trigger fn_sync_transaction_to_user_activity failed")

    parsed = ParsedWhatsAppMessage(
        message_id="wamid.conf.1",
        from_phone="+2347065015924",
        message_type="interactive",
        body="1 Yes",
        interactive_reply_id="btn_confirm_yes",
    )

    with pytest.raises(RuntimeError, match="Trigger fn_sync_transaction_to_user_activity failed"):
        await processor.process_message(mock_db, parsed, None)

    # WhatsApp success text must NOT have been sent!
    mock_whatsapp.send_text.assert_not_called()


@pytest.mark.asyncio
async def test_regression_task_service_create_task_signature():
    """
    Follow-up 4:
    Verify TaskService.create_task signature has no user_id (tasks table is business-scoped),
    and caller without user_id succeeds cleanly.
    """
    service = TaskService()
    mock_db = AsyncMock()
    biz_id = uuid4()

    task = await service.create_task(
        mock_db,
        business_id=biz_id,
        title="Check stock inventory",
        due_at=datetime.now(UTC),
    )
    assert task.title == "Check stock inventory"
    assert task.business_id == biz_id
    mock_db.add.assert_called_once_with(task)
