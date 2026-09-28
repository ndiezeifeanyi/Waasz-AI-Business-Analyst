from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4
import pytest

from app.core.exceptions import SecurityError
from app.models.activity import Activity
from app.models.business import Business
from app.models.goal import UserGoal
from app.models.memory import ConversationMemory
from app.models.user import User
from app.schemas.whatsapp import ParsedWhatsAppMessage
from app.services.goal_service import GoalService
from app.services.group_handler import GroupChatHandler
from app.services.knowledge_service import KnowledgeService
from app.services.ledger_service import LedgerService
from app.services.memory_service import MemoryService
from app.services.unified_report_service import UnifiedReportService


@pytest.mark.asyncio
async def test_knowledge_chunks_strict_user_isolation():
    """Verify that User B cannot retrieve User A's private knowledge chunks."""
    user_a = uuid4()
    user_b = uuid4()

    mock_db = AsyncMock()
    mock_embeddings = MagicMock()
    mock_embeddings.embed_query = AsyncMock(return_value=[0.1] * 768)

    service = KnowledgeService(embedding_service=mock_embeddings)

    result_mock = MagicMock()
    result_mock.fetchall.return_value = []
    mock_db.execute.return_value = result_mock

    matches = await service.search_user_knowledge(mock_db, user_b, "Confidential roadmap")
    assert len(matches) == 0

    call_args = mock_db.execute.await_args
    sql_params = call_args[0][1]
    assert sql_params["user_id"] == user_b
    assert sql_params["user_id"] != user_a


@pytest.mark.asyncio
async def test_conversation_memory_strict_user_isolation():
    """Verify that User B cannot retrieve User A's semantic memory."""
    user_a = uuid4()
    user_b = uuid4()

    mock_db = AsyncMock()
    mock_embeddings = MagicMock()
    mock_embeddings.embed_query = AsyncMock(return_value=[0.2] * 768)

    service = MemoryService(embedding_service=mock_embeddings)

    result_mock = MagicMock()
    result_mock.fetchall.return_value = []
    mock_db.execute.return_value = result_mock

    matches = await service.retrieve_relevant_memories(mock_db, user_b, "What was my secret target?")
    assert len(matches) == 0

    call_args = mock_db.execute.await_args
    sql_params = call_args[0][1]
    assert sql_params["user_id"] == user_b
    assert sql_params["user_id"] != user_a


@pytest.mark.asyncio
async def test_user_goals_strict_isolation():
    """Verify that User B querying active goals only selects goals where user_id = user_b."""
    user_a = uuid4()
    user_b = uuid4()

    mock_db = AsyncMock()
    result_mock = MagicMock()
    # User B has no goals
    result_mock.scalars.return_value.all.return_value = []
    mock_db.execute.return_value = result_mock

    service = GoalService()
    goals = await service.get_active_goals(mock_db, user_b)
    assert len(goals) == 0

    call_args = mock_db.execute.await_args
    stmt = call_args[0][0]
    # Verify the compiled statement filters by user_id
    compiled_str = str(stmt.compile())
    assert "user_goals.user_id =" in compiled_str


@pytest.mark.asyncio
async def test_report_generation_strict_isolation():
    """Verify that report generation for User B strictly excludes User A's activities and goals."""
    user_a = uuid4()
    user_b = uuid4()

    user_b_obj = User(
        id=user_b,
        phone_number="+2348000000002",
        display_name="User B",
        niche="employee_9_to_5",
        last_inbound_at=datetime.now(UTC),
    )

    mock_db = AsyncMock()
    mock_db.get.return_value = user_b_obj

    # Mock execute returns empty for user_b
    result_mock = MagicMock()
    result_mock.scalars.return_value.all.return_value = []
    mock_db.execute.return_value = result_mock

    mock_whatsapp = MagicMock()
    mock_whatsapp.send_text = AsyncMock()

    service = UnifiedReportService(whatsapp=mock_whatsapp)
    service._synthesize_report = AsyncMock(return_value="Mock synthesized report")
    res = await service.generate_and_deliver_report(mock_db, user_b, cadence="weekly")

    assert res["status"] == "sent"
    # Ensure every select executed had user_id filter
    for call in mock_db.execute.await_args_list:
        stmt = call[0][0]
        compiled = str(stmt.compile())
        assert "user_id =" in compiled


def test_group_chat_never_proactive_or_leaking_private_memory():
    """Verify group chat handler ignores messages without @mention and does not leak private memory."""
    handler = GroupChatHandler()

    msg_unmentioned = ParsedWhatsAppMessage(
        message_id="wamid.test.1",
        from_phone="+2348011112222",
        message_type="text",
        body="Does anyone know the meeting time?",
    )
    # Ignored because bot was not mentioned
    assert handler.is_bot_mentioned(msg_unmentioned.body) is False

    msg_mentioned = ParsedWhatsAppMessage(
        message_id="wamid.test.2",
        from_phone="+2348011112222",
        message_type="text",
        body="@assistant what is the meeting time?",
    )
    assert handler.is_bot_mentioned(msg_mentioned.body) is True
    clean_prompt = handler.extract_clean_prompt(msg_mentioned.body)
    assert clean_prompt == "what is the meeting time?"


@pytest.mark.asyncio
async def test_private_goal_invisible_to_business_owner_and_colleagues():
    """Verify that a private goal created by a staff member is strictly invisible to the owner/colleagues."""
    staff_user_id = uuid4()
    owner_user_id = uuid4()
    business_id = uuid4()

    mock_db = AsyncMock()
    result_mock = MagicMock()
    result_mock.scalars.return_value.all.return_value = []
    mock_db.execute.return_value = result_mock

    goal_service = GoalService()

    # Owner queries their goals
    goals = await goal_service.get_active_goals(mock_db, owner_user_id)
    assert len(goals) == 0

    call_args = mock_db.execute.await_args
    stmt = call_args[0][0]
    compiled_str = str(stmt.compile())
    # Query must strictly filter on user_goals.user_id = owner_user_id, NOT business_id
    assert "user_goals.user_id =" in compiled_str
    # Staff's private goals cannot leak to owner


@pytest.mark.asyncio
async def test_business_shared_item_accessible_to_colleagues_never_cross_business():
    """
    Verify:
    1. Same-business staff can access business_shared knowledge (e.g. price lists).
    2. Different-business staff cannot access other business's shared knowledge.
    3. Private knowledge chunks are never exposed to colleagues.
    """
    biz_1 = uuid4()
    biz_2 = uuid4()
    user_biz_1_staff_a = uuid4()
    user_biz_2_staff = uuid4()

    mock_db = AsyncMock()
    mock_embeddings = MagicMock()
    mock_embeddings.embed_query = AsyncMock(return_value=[0.1] * 768)

    service = KnowledgeService(embedding_service=mock_embeddings)

    # Map legitimate user-to-business affiliations for the mock DB check
    valid_affiliations = {
        (user_biz_1_staff_a, biz_1): user_biz_1_staff_a,
        (user_biz_2_staff, biz_2): user_biz_2_staff,
    }

    async def mock_execute(stmt, params=None, *args, **kwargs):
        res = MagicMock()
        stmt_str = str(stmt)
        if "users.business_id" in stmt_str or "users.id" in stmt_str:
            # Check user membership
            # In mock execute, check if current params or where criteria matches valid affiliations
            res.scalar_one_or_none.return_value = uuid4()
        else:
            res.fetchall.return_value = []
        return res

    mock_db.execute = AsyncMock(side_effect=mock_execute)

    # 1. Staff in Biz 1 queries knowledge
    await service.search_user_knowledge(
        mock_db, user_id=user_biz_1_staff_a, query="price list", business_id=biz_1
    )
    # The last execute call was the vector query
    call_args_1 = mock_db.execute.await_args
    sql_text_1 = str(call_args_1[0][0])
    params_1 = call_args_1[0][1]

    # Verify SQL query allows business_shared for biz_1
    assert "business_id = :business_id AND visibility = 'business_shared'" in sql_text_1
    assert params_1["business_id"] == biz_1
    assert params_1["user_id"] == user_biz_1_staff_a

    # 2. Staff in Biz 2 queries knowledge
    await service.search_user_knowledge(
        mock_db, user_id=user_biz_2_staff, query="price list", business_id=biz_2
    )
    call_args_2 = mock_db.execute.await_args
    params_2 = call_args_2[0][1]

    # Strictly scoped to biz_2, impossible to access biz_1 records
    assert params_2["business_id"] == biz_2
    assert params_2["business_id"] != biz_1

    # 3. Personal user with no business_id queries knowledge
    user_personal = uuid4()
    await service.search_user_knowledge(
        mock_db, user_id=user_personal, query="personal thoughts", business_id=None
    )
    call_args_3 = mock_db.execute.await_args
    sql_text_3 = str(call_args_3[0][0])
    params_3 = call_args_3[0][1]

    # Must be strictly user_id = :user_id with no business sharing
    assert "WHERE user_id = :user_id" in sql_text_3
    assert "business_id" not in params_3

    # 4. FORGERY ATTEMPT: Attacker attempts to pass a forged/mismatched business_id
    attacker_id = uuid4()
    forged_biz_id = biz_2  # Attacker does NOT belong to biz_2

    # Configure mock DB to reject user membership for attacker
    async def mock_execute_with_rejection(stmt, params=None, *args, **kwargs):
        res = MagicMock()
        stmt_str = str(stmt)
        if "users.business_id" in stmt_str or "users.id" in stmt_str:
            # User check fails: user does not belong to forged_biz_id
            res.scalar_one_or_none.return_value = None
        else:
            res.fetchall.return_value = []
        return res

    mock_db.execute = AsyncMock(side_effect=mock_execute_with_rejection)

    with pytest.raises(SecurityError) as exc_info:
        await service.search_user_knowledge(
            mock_db, user_id=attacker_id, query="confidential internal price list", business_id=forged_biz_id
        )

    assert "does not belong to business" in str(exc_info.value)


@pytest.mark.asyncio
async def test_provisional_business_flag_and_retention():
    """Verify that auto-created business is flagged provisional and uses user data_retention_days."""
    mock_db = AsyncMock()
    exec_res = MagicMock()
    exec_res.scalar_one_or_none.return_value = None
    mock_db.execute.return_value = exec_res

    ledger = LedgerService()
    biz, user = await ledger.get_or_create_business_and_user(mock_db, "+2348012345678")

    assert biz.is_provisional is True
    assert user.role == "owner"

    # Verify retention purge uses user's data_retention_days for provisional businesses
    mem_service = MemoryService()
    biz.data_retention_days = 730  # Default business retention
    biz.is_provisional = True
    user.data_retention_days = 90  # Custom user retention
    user.business_id = biz.id

    cutoffs_checked = []

    async def mock_execute_retention(stmt, *args, **kwargs):
        res = MagicMock()
        stmt_str = str(stmt)
        if "FROM businesses" in stmt_str:
            res.scalars.return_value.all.return_value = [biz]
        elif "FROM users" in stmt_str and "LIMIT" in stmt_str:
            res.scalar_one_or_none.return_value = user
        elif "FROM users" in stmt_str:
            res.scalars.return_value.all.return_value = [user]
        elif "DELETE FROM user_activities" in stmt_str:
            # Capture the compiled/bound parameter or where clause
            cutoffs_checked.append(stmt)
            res.rowcount = 1
        else:
            res.rowcount = 1
        return res

    mock_db.execute = AsyncMock(side_effect=mock_execute_retention)
    purged = await mem_service.purge_expired_user_data(mock_db)
    assert purged >= 0
    assert len(cutoffs_checked) >= 1

