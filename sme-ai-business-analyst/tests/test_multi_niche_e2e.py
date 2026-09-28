from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4
import pytest

from app.models.business import Business
from app.models.confirmation import Confirmation
from app.models.user import User
from app.schemas.whatsapp import ParsedWhatsAppMessage
from app.services.intent_router import IntentRouter
from app.services.webhook_processor import WhatsAppWebhookProcessor


@pytest.mark.asyncio
async def test_sme_owner_pipeline_scenario():
    """
    End-to-End scenario for an SME Owner:
    1. Transaction extraction (sale)
    2. Shared knowledge storage (price list)
    3. Business goal creation
    4. Business Q&A with retail/SME persona adaptation
    """
    biz_id = uuid4()
    user_id = uuid4()
    sme_user = User(
        id=user_id,
        business_id=biz_id,
        phone_number="+2348011112222",
        display_name="Ibrahim Groceries",
        niche="sme_owner",
        last_inbound_at=datetime.now(UTC),
    )
    business = Business(
        id=biz_id,
        name="Ibrahim Groceries Ltd",
        phone_number="+2348011112222",
    )

    # 1. Test Intent Classification for SME Owner inputs
    assert IntentRouter.classify("Sold 5 cartons of Indomie for 35000") == "transaction"
    assert IntentRouter.classify("price list: Indomie carton is 7000 NGN, Golden Penny is 48000 NGN") == "knowledge_store"
    assert IntentRouter.classify("my goal is reach 500,000 NGN weekly revenue") == "goal"
    assert IntentRouter.classify("How can I improve my store margins?") == "general_qa"

    # 2. Mock processor components
    mock_ledger = AsyncMock()
    mock_ledger.get_or_create_business_and_user.return_value = (business, sme_user)
    mock_ledger.record_inbound_message.side_effect = lambda *args, **kwargs: MagicMock(id=uuid4(), status="received")

    mock_whatsapp = MagicMock()
    mock_whatsapp.send_text = AsyncMock(return_value=MagicMock(message_id="msg_text_1"))
    mock_whatsapp.send_confirmation = AsyncMock(return_value=MagicMock(message_id="msg_conf_1"))

    mock_knowledge = AsyncMock()
    mock_goals = AsyncMock()
    mock_qa = AsyncMock()
    mock_qa.answer.return_value = "As a retail business analyst, to improve your margins: focus on high-turnover goods like Indomie and renegotiate supplier terms."

    mock_confirmations = AsyncMock()
    mock_confirmations.latest_actionable.return_value = None

    processor = WhatsAppWebhookProcessor(
        ledger=mock_ledger,
        whatsapp=mock_whatsapp,
        confirmations=mock_confirmations,
        knowledge=mock_knowledge,
        goals=mock_goals,
        qa=mock_qa,
    )

    mock_db = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    mock_db.execute.return_value = mock_result
    mock_event = MagicMock(id=uuid4())

    # Execute Message 2: Price list knowledge store
    msg_knowledge = ParsedWhatsAppMessage(
        message_id="wamid.sme.1",
        from_phone="+2348011112222",
        message_type="text",
        body="price list: Indomie carton is 7000 NGN, Golden Penny is 48000 NGN",
    )
    await processor.process_message(mock_db, msg_knowledge, mock_event)
    mock_knowledge.store_user_document.assert_called_once()
    assert "Indomie" in mock_knowledge.store_user_document.call_args[1]["content"]

    # Execute Message 3: Goal
    msg_goal = ParsedWhatsAppMessage(
        message_id="wamid.sme.2",
        from_phone="+2348011112222",
        message_type="text",
        body="my goal is reach 500,000 NGN weekly revenue",
    )
    await processor.process_message(mock_db, msg_goal, mock_event)
    mock_goals.create_goal.assert_called_once()
    assert "500,000" in mock_goals.create_goal.call_args[1]["title"]

    # Execute Message 4: Q&A
    msg_qa = ParsedWhatsAppMessage(
        message_id="wamid.sme.3",
        from_phone="+2348011112222",
        message_type="text",
        body="How can I improve my store margins?",
    )
    await processor.process_message(mock_db, msg_qa, mock_event)
    mock_qa.answer.assert_called_once()
    assert mock_qa.answer.call_args[0][1].niche == "sme_owner"
    mock_whatsapp.send_text.assert_called()


@pytest.mark.asyncio
async def test_employee_9_to_5_pipeline_scenario():
    """
    End-to-End scenario for a 9-to-5 Corporate Employee:
    1. Career note storage
    2. Career promotion goal tracking
    3. Career coaching Q&A adaptation
    4. Privacy forget-me purge execution
    """
    user_id = uuid4()
    emp_user = User(
        id=user_id,
        business_id=None,
        phone_number="+2348033334444",
        display_name="Chioma Engineer",
        niche="employee_9_to_5",
        last_inbound_at=datetime.now(UTC),
    )
    mock_biz = Business(
        id=uuid4(),
        name="Personal Account",
        phone_number="+2348033334444",
    )

    # 1. Test Intent Classification for Employee inputs
    assert IntentRouter.classify("save note: Led cross-functional sprint demo on payment microservice") == "knowledge_store"
    assert IntentRouter.classify("my goal is get promoted to Staff Engineer by Q4") == "goal"
    assert IntentRouter.classify("What should I highlight in my 1-on-1 performance review?") == "general_qa"
    assert IntentRouter.classify("forget me") == "forget_me"
    assert IntentRouter.classify("CONFIRM DELETE") == "forget_me"

    # 2. Mock processor components
    mock_ledger = AsyncMock()
    mock_ledger.get_or_create_business_and_user.return_value = (mock_biz, emp_user)
    mock_ledger.record_inbound_message.side_effect = lambda *args, **kwargs: MagicMock(id=uuid4(), status="received")

    mock_whatsapp = MagicMock()
    mock_whatsapp.send_text = AsyncMock(return_value=MagicMock(message_id="msg_text_2"))

    mock_knowledge = AsyncMock()
    mock_goals = AsyncMock()
    mock_memory = AsyncMock()
    mock_qa = AsyncMock()
    mock_qa.answer.return_value = "As a corporate career strategist, quantify the business impact of your payment microservice in your 1-on-1 review."

    mock_confirmations = AsyncMock()
    mock_confirmations.latest_actionable.return_value = None

    processor = WhatsAppWebhookProcessor(
        ledger=mock_ledger,
        whatsapp=mock_whatsapp,
        confirmations=mock_confirmations,
        knowledge=mock_knowledge,
        goals=mock_goals,
        memory=mock_memory,
        qa=mock_qa,
    )

    mock_db = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    mock_db.execute.return_value = mock_result
    mock_event = MagicMock(id=uuid4())

    # Execute Message 1: Career note store
    msg_note = ParsedWhatsAppMessage(
        message_id="wamid.emp.1",
        from_phone="+2348033334444",
        message_type="text",
        body="save note: Led cross-functional sprint demo on payment microservice",
    )
    await processor.process_message(mock_db, msg_note, mock_event)
    mock_knowledge.store_user_document.assert_called_once()
    assert "cross-functional" in mock_knowledge.store_user_document.call_args[1]["content"]

    # Execute Message 2: Career goal
    msg_goal = ParsedWhatsAppMessage(
        message_id="wamid.emp.2",
        from_phone="+2348033334444",
        message_type="text",
        body="my goal is get promoted to Staff Engineer by Q4",
    )
    await processor.process_message(mock_db, msg_goal, mock_event)
    mock_goals.create_goal.assert_called_once()
    assert "Staff Engineer" in mock_goals.create_goal.call_args[1]["title"]

    # Execute Message 3: Career Q&A
    msg_qa = ParsedWhatsAppMessage(
        message_id="wamid.emp.3",
        from_phone="+2348033334444",
        message_type="text",
        body="What should I highlight in my 1-on-1 performance review?",
    )
    await processor.process_message(mock_db, msg_qa, mock_event)
    mock_qa.answer.assert_called_once()
    assert mock_qa.answer.call_args[0][1].niche == "employee_9_to_5"

    # Execute Message 4: Privacy Forget-Me Intent
    # Step A: user asks "forget me" -> system prompts for CONFIRM DELETE
    msg_forget = ParsedWhatsAppMessage(
        message_id="wamid.emp.4",
        from_phone="+2348033334444",
        message_type="text",
        body="forget me",
    )
    await processor.process_message(mock_db, msg_forget, mock_event)
    mock_memory.purge_user_data.assert_not_called()
    sent_warning = mock_whatsapp.send_text.call_args[0][1]
    assert "CONFIRM DELETE" in sent_warning

    # Step B: user confirms "CONFIRM DELETE" -> system executes atomic purge
    msg_confirm_delete = ParsedWhatsAppMessage(
        message_id="wamid.emp.5",
        from_phone="+2348033334444",
        message_type="text",
        body="CONFIRM DELETE",
    )
    await processor.process_message(mock_db, msg_confirm_delete, mock_event)
    mock_memory.purge_user_data.assert_called_once_with(mock_db, user_id)
    sent_confirmation = mock_whatsapp.send_text.call_args[0][1]
    assert "permanently deleted" in sent_confirmation
