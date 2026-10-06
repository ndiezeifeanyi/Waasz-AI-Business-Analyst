"""
Permanent pytest regression tests for the four core system fixes:
1. Core identity never centers on "business" as primary description
2. Formatting rules (single asterisks, no headers, blank lines) present in prompt
3. Image handling routes to multimodal vision, not dropped by OCR
4. Daily unified reports dispatch per niche using UnifiedReportService
5. NICHE_FALLBACK_QA_MESSAGES confirmed deleted
"""

from datetime import UTC, datetime
import inspect
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4
import pytest
from langchain_core.messages import HumanMessage

from app.models.activity import Activity
from app.models.business import Business
from app.models.goal import UserGoal
from app.models.user import User
from app.services.agent_service import AgentService
from app.services.ai_client import AiClient
from app.services.scheduler import ReportScheduler
from app.services.unified_report_service import UnifiedReportService


def test_niche_fallback_qa_messages_deleted():
    """Confirm NICHE_FALLBACK_QA_MESSAGES is completely deleted from the codebase."""
    import app.services.webhook_processor as wp
    assert not hasattr(wp, "NICHE_FALLBACK_QA_MESSAGES"), "NICHE_FALLBACK_QA_MESSAGES should be deleted from webhook_processor"

    import app.services.agent_service as ag
    src = inspect.getsource(ag)
    assert "NICHE_FALLBACK_QA_MESSAGES" not in src, "agent_service should not reference NICHE_FALLBACK_QA_MESSAGES"


def test_core_identity_is_business_first_with_supporting_personal_capabilities():
    """Verify core identity is primarily business-first with personal features as supporting capabilities."""
    agent = AgentService()
    now_utc = datetime.now(UTC)
    business = Business(id=uuid4(), name="Test Trading")

    niches = ["sme_owner", "student", "employee_9_to_5", "freelancer", "personal"]
    expected_core = (
        "You are Waasz, an AI business assistant built primarily to help business owners run, track, and grow their businesses."
    )

    for niche in niches:
        user = User(id=uuid4(), business_id=business.id, display_name="Alex", niche=niche)
        prompt = agent._build_system_prompt(business, user, now_utc, "Africa/Lagos")

        # Must contain the business-first primary identity
        assert expected_core in prompt
        assert "Your core, primary identity is business-first" in prompt
        assert "strictly secondary, supporting capabilities" in prompt
        assert "Never claim an equal dual identity" in prompt


def test_system_prompt_whatsapp_formatting_rules_present():
    """Verify explicit WhatsApp formatting directives are enforced in system prompt."""
    agent = AgentService()
    now_utc = datetime.now(UTC)
    business = Business(id=uuid4(), name="Test Biz")
    user = User(id=uuid4(), business_id=business.id, display_name="Amina", niche="sme_owner")

    prompt = agent._build_system_prompt(business, user, now_utc, "Africa/Lagos")

    assert "### WHATSAPP FORMATTING RULES:" in prompt
    assert "Use single *asterisks* for bold sparingly" in prompt
    assert "NEVER use markdown headers (no '#', '##', or '###')" in prompt
    assert "Use clean blank lines between paragraphs" in prompt
    assert "single leading emoji or dash" in prompt


@pytest.mark.asyncio
async def test_image_routes_to_multimodal_vision():
    """Verify image bytes are passed into multimodal HumanMessage blocks rather than dropped."""
    ai_client = AiClient()
    # Confirm provider_chain supports capability='vision' dynamically through model_resolver
    vision_providers = ai_client.provider_chain(capability="vision")
    assert len(vision_providers) > 0, "Vision provider chain should have models configured"

    # Confirm agent process_user_message builds list-type HumanMessage when image_bytes are passed
    agent = AgentService()
    test_bytes = b"\xff\xd8\xff\xe0\x00\x10JFIF"  # minimal dummy JPEG header

    # Mock invoke_agent to inspect the messages it receives
    mock_invoke = AsyncMock()
    mock_result = MagicMock()
    mock_result.message = MagicMock(content="I see a photo of a wooden table.")
    mock_result.message.tool_calls = []
    mock_invoke.return_value = mock_result

    with patch.object(agent.ai_client, "invoke_agent", mock_invoke), \
         patch.object(agent.memory, "get_short_term_turns", AsyncMock(return_value=[])), \
         patch.object(agent.memory, "retrieve_relevant_memories", AsyncMock(return_value=[])), \
         patch.object(agent.knowledge, "search_user_knowledge", AsyncMock(return_value=[])), \
         patch.object(agent.goals, "get_active_goals", AsyncMock(return_value=[])), \
         patch.object(agent.confirmations, "latest_actionable", AsyncMock(return_value=None)):

        db = AsyncMock()
        business = Business(id=uuid4(), name="Test Biz")
        user = User(id=uuid4(), business_id=business.id, display_name="Test User", niche="student")

        await agent.process_user_message(
            db=db,
            business=business,
            user=user,
            user_message="What is this?",
            image_bytes=test_bytes,
        )

        # Verify invoke_agent was called with multimodal content
        assert mock_invoke.called
        call_kwargs = mock_invoke.call_args.kwargs
        messages = call_kwargs.get("messages")
        assert messages is not None
        human_msg = next((m for m in messages if isinstance(m, HumanMessage)), None)
        assert human_msg is not None
        assert isinstance(human_msg.content, list)
        assert any(item.get("type") == "image_url" for item in human_msg.content)
        assert any(item.get("type") == "text" for item in human_msg.content)


def test_image_receipt_vs_photo_routing_instructions_in_prompt():
    """Verify prompt explicitly instructs how to route receipts (tools) vs photos (vision)."""
    agent = AgentService()
    now_utc = datetime.now(UTC)
    business = Business(id=uuid4(), name="Test Biz")
    user = User(id=uuid4(), business_id=business.id, display_name="Test User", niche="sme_owner")

    prompt = agent._build_system_prompt(business, user, now_utc, "Africa/Lagos")

    assert "### IMAGE & RECEIPT UNDERSTANDING:" in prompt
    assert "RECEIPT / INVOICE / EXPENSE" in prompt
    assert "call the `record_transaction` tool" in prompt
    assert "GENERAL PHOTO / SCENE / OBJECT" in prompt


def test_scheduler_registers_daily_unified_report_and_removes_legacy():
    """Verify scheduler registers dispatch_daily_unified_reports and no legacy financial jobs."""
    report_scheduler = ReportScheduler()
    with patch.object(report_scheduler.scheduler, "start"):
        report_scheduler.start()

    jobs = [job.id for job in report_scheduler.scheduler.get_jobs()]

    assert "dispatch_daily_unified_reports" in jobs
    assert "dispatch_weekly_unified_reports" in jobs
    assert "dispatch_monthly_unified_reports" in jobs

    # Ensure legacy jobs are NOT registered
    assert "send_daily_reports" not in jobs
    assert "send_weekly_reports" not in jobs
    assert "daily_report_job" not in jobs


@pytest.mark.asyncio
async def test_unified_report_daily_cadence_branches_by_niche():
    """Verify UnifiedReportService supports daily cadence and tailors content by user niche."""
    service = UnifiedReportService()

    # Student Activity
    student_user = User(id=uuid4(), display_name="Chioma", niche="student")
    student_activity = Activity(
        id=uuid4(),
        user_id=student_user.id,
        activity_type="study_session",
        title="Reviewed Organic Chemistry",
    )
    student_goal = UserGoal(
        id=uuid4(),
        user_id=student_user.id,
        title="Finish syllabus",
        target_value=100,
        current_value=40,
    )

    prompt_captured = {}

    async def mock_ainvoke(messages):
        prompt_captured["text"] = messages[0].content
        mock_resp = MagicMock()
        mock_resp.content = "Student Daily Report"
        return mock_resp

    with patch("app.services.unified_report_service.ChatGoogleGenerativeAI") as MockGemini:
        mock_llm_instance = MagicMock()
        mock_llm_instance.ainvoke = AsyncMock(side_effect=mock_ainvoke)
        MockGemini.return_value = mock_llm_instance

        res = await service._synthesize_report(
            student_user,
            "daily",
            [student_activity],
            [],
            [student_goal],
        )

        assert "profile is 'student'" in prompt_captured["text"]
        assert "Daily Report" in prompt_captured["text"]
        assert "Daily Highlights" in prompt_captured["text"]
        assert "Reviewed Organic Chemistry" in prompt_captured["text"]
        assert "Finish syllabus: 40 / 100" in prompt_captured["text"]
        assert "CRITICAL GROUNDING RULES:" in prompt_captured["text"]
        assert res == "Student Daily Report"


@pytest.mark.asyncio
async def test_system_health_endpoint_reports_comprehensive_status():
    """Verify /health/system endpoint audits database, scheduler jobs, models, and credentials."""
    from httpx import ASGITransport, AsyncClient
    from app.main import app

    scheduler = ReportScheduler()
    with patch.object(scheduler.scheduler, "start"):
        scheduler.start()
    scheduler.scheduler._state = 1  # STATE_RUNNING
    app.state.scheduler = scheduler

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/health/system")
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("single_process_mode") is True
        assert "database" in data
        assert "scheduler" in data
        assert "resolved_models" in data
        assert "credentials" in data
        assert data["scheduler"]["jobs_registered"] >= 8
        assert len(data["scheduler"]["missing_jobs"]) == 0
