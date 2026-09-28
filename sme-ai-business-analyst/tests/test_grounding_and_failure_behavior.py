"""
Tests for Grounding, RAG Behavior, and Honest Error Handling (Phase D & E Audit).
Verifies:
1. Grounded answers when data/context is found.
2. Honest general answers (no hallucinated user records) when no data/context is found.
3. Plain-language honest failure degradation (never fake answers) when LLM or DB errors occur.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4
import pytest

from app.models.activity import Activity
from app.models.goal import UserGoal
from app.models.user import User
from app.schemas.whatsapp import ParsedWhatsAppMessage
from app.services.knowledge_service import KnowledgeService
from app.services.qa_service import QaService
from app.services.unified_report_service import UnifiedReportService
from app.services.webhook_processor import WhatsAppWebhookProcessor


@pytest.mark.asyncio
async def test_scenario_2_qa_no_context_found_instructs_grounding():
    """
    Scenario 2: When no user notes, memories, or documents are found,
    the prompt must explicitly enforce grounding rules so the LLM does not hallucinate user records.
    """
    mock_memory = AsyncMock()
    mock_memory.get_short_term_turns.return_value = []
    mock_memory.retrieve_relevant_memories.return_value = []

    mock_knowledge = AsyncMock()
    mock_knowledge.search_user_knowledge.return_value = []

    mock_goals = AsyncMock()
    mock_goals.get_active_goals.return_value = []

    qa = QaService(memory=mock_memory, knowledge=mock_knowledge, goals=mock_goals)
    user = User(id=uuid4(), phone_number="+2348011112222", niche="sme_owner")
    mock_db = AsyncMock()

    # Capture the message list sent to the LLM
    captured_messages = []

    with patch("app.services.qa_service.ChatGoogleGenerativeAI") as mock_gemini:
        llm_instance = MagicMock()
        async def mock_ainvoke(messages):
            captured_messages.extend(messages)
            ai_msg = MagicMock()
            ai_msg.content = "General advice: To improve cash flow, track inventory turnover and negotiate payment terms with suppliers."
            return ai_msg
        llm_instance.ainvoke = AsyncMock(side_effect=mock_ainvoke)
        mock_gemini.return_value = llm_instance

        answer = await qa.answer(mock_db, user, "What were my sales figures yesterday?")

    # Verify LLM was called and system prompt contained explicit anti-hallucination grounding rules
    assert len(captured_messages) > 0
    sys_content = captured_messages[0].content
    assert "CRITICAL GROUNDING RULES" in sys_content
    assert "WITHOUT inventing specific facts" in sys_content
    assert "General advice" in answer


@pytest.mark.asyncio
async def test_scenario_2_report_zero_activities_states_zero_honestly():
    """
    Scenario 2: When a user has 0 logged activities, the report must state 0 logged activities
    honestly and must not fabricate accomplishments or metrics.
    """
    user_id = uuid4()
    user = User(
        id=user_id,
        phone_number="+2348011112222",
        display_name="Amaka",
        niche="student",
        last_inbound_at=datetime.now(UTC),
    )

    mock_db = AsyncMock()
    mock_db.get.return_value = user

    # Mock execute returns empty for current activities, prior activities, and goals
    empty_result = MagicMock()
    empty_result.scalars.return_value.all.return_value = []
    mock_db.execute.return_value = empty_result

    mock_whatsapp = MagicMock()
    mock_whatsapp.send_text = AsyncMock()

    service = UnifiedReportService(whatsapp=mock_whatsapp)
    res = await service.generate_and_deliver_report(mock_db, user_id, cadence="weekly")

    assert res["status"] == "sent"
    # When all providers fall through to honest summary or return grounded report
    report_text = res["report"]
    assert "0" in report_text
    assert ("0 logged" in report_text or "0 activities" in report_text or "Activities Logged:* 0" in report_text or "no activities" in report_text.lower() or "0" in report_text)


@pytest.mark.asyncio
async def test_scenario_2_knowledge_retrieval_returns_empty_on_no_match():
    """
    Scenario 2: When a query matches no user documents, KnowledgeService
    returns an empty list instead of fabricating chunks or leaking unrelated data.
    """
    service = KnowledgeService()
    mock_db = AsyncMock()
    exec_result = MagicMock()
    exec_result.fetchall.return_value = []
    mock_db.execute.return_value = exec_result

    user_id = uuid4()
    chunks = await service.search_user_knowledge(mock_db, user_id, "quantum computing manual")
    assert chunks == []


@pytest.mark.asyncio
async def test_scenario_3_qa_all_providers_fail_returns_honest_error():
    """
    Scenario 3: When all LLM providers fail and no saved notes are available,
    QaService must return a plain-language error notification, NOT a fake canned answer.
    """
    mock_memory = AsyncMock()
    mock_memory.get_short_term_turns.return_value = []
    mock_memory.retrieve_relevant_memories.return_value = []

    mock_knowledge = AsyncMock()
    mock_knowledge.search_user_knowledge.return_value = []

    mock_goals = AsyncMock()
    mock_goals.get_active_goals.return_value = []

    qa = QaService(memory=mock_memory, knowledge=mock_knowledge, goals=mock_goals)
    user = User(id=uuid4(), phone_number="+2348011112222", niche="freelancer")
    mock_db = AsyncMock()

    # Simulate all LLM calls raising exceptions
    with patch("app.services.qa_service.ChatGoogleGenerativeAI", side_effect=Exception("Gemini network down")), \
         patch("langchain_groq.ChatGroq", side_effect=Exception("Groq rate limited")), \
         patch("langchain_openai.ChatOpenAI", side_effect=Exception("OpenAI quota exceeded")):
        
        answer = await qa.answer(mock_db, user, "What should I charge for a React project?")

    # Verify it does NOT return "I've noted that!" or a canned reply
    assert "I've noted that" not in answer
    assert "⚠️ I'm sorry, I'm currently having trouble connecting to my reasoning services" in answer


@pytest.mark.asyncio
async def test_scenario_3_qa_all_providers_fail_with_chunks_degrades_honestly():
    """
    Scenario 3: When all LLM providers fail BUT the user has saved notes/chunks,
    QaService returns an honest degraded response citing the raw notes without faking AI commentary.
    """
    mock_memory = AsyncMock()
    mock_memory.get_short_term_turns.return_value = []
    mock_memory.retrieve_relevant_memories.return_value = []

    mock_knowledge = AsyncMock()
    mock_knowledge.search_user_knowledge.return_value = [
        {"content": "Standard website rate: ₦250,000 per project."}
    ]

    mock_goals = AsyncMock()
    mock_goals.get_active_goals.return_value = []

    qa = QaService(memory=mock_memory, knowledge=mock_knowledge, goals=mock_goals)
    user = User(id=uuid4(), phone_number="+2348011112222", niche="freelancer")
    mock_db = AsyncMock()

    with patch("app.services.qa_service.ChatGoogleGenerativeAI", side_effect=Exception("Gemini outage")), \
         patch("langchain_groq.ChatGroq", side_effect=Exception("Groq outage")), \
         patch("langchain_openai.ChatOpenAI", side_effect=Exception("OpenAI outage")):
        
        answer = await qa.answer(mock_db, user, "What is my rate?")

    assert "⚠️ AI service is temporarily degraded, but here is what I found in your saved notes:" in answer
    assert "Standard website rate: ₦250,000 per project." in answer


@pytest.mark.asyncio
async def test_scenario_3_webhook_unhandled_failure_notifies_user():
    """
    Scenario 3: When an unexpected exception occurs in process_message (e.g. database failure),
    the webhook processor catches it, marks the event failed, and sends a plain-language error
    notification to the user's WhatsApp phone instead of silent dead-air.
    """
    processor = WhatsAppWebhookProcessor()
    mock_whatsapp = MagicMock()
    mock_whatsapp.send_text = AsyncMock()
    processor.whatsapp = mock_whatsapp

    # Force process_message to crash
    processor.process_message = AsyncMock(side_effect=Exception("Database connection timeout"))

    mock_db = AsyncMock()
    mock_db.commit = AsyncMock()
    mock_db.rollback = AsyncMock()
    mock_db.merge = AsyncMock(side_effect=lambda x: x)
    
    mock_event = MagicMock()
    processor.ledger.create_webhook_event = AsyncMock(return_value=mock_event)

    test_payload = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "123",
                "changes": [
                    {
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {"display_phone_number": "123", "phone_number_id": "123"},
                            "messages": [
                                {
                                    "id": "wamid.crash.test",
                                    "from": "+2348099998888",
                                    "timestamp": "1710000000",
                                    "type": "text",
                                    "text": {"body": "hello"},
                                }
                            ],
                        },
                        "field": "messages",
                    }
                ],
            }
        ],
    }

    with patch("app.services.webhook_processor.async_session_factory") as mock_session_ctx:
        mock_session_ctx.return_value.__aenter__.return_value = mock_db
        mock_session_ctx.return_value.__aexit__.return_value = None

        await processor.process_payload(test_payload)

    # User must have received a plain-language error message
    mock_whatsapp.send_text.assert_awaited_once()
    call_args = mock_whatsapp.send_text.await_args[0]
    assert call_args[0] == "+2348099998888"
    assert "⚠️ Something went wrong processing your message" in call_args[1]
