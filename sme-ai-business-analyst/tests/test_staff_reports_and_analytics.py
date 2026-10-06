import pytest
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from app.models.activity import Activity
from app.models.business import Business
from app.models.goal import UserGoal
from app.models.member import Member
from app.models.transaction import Transaction
from app.models.user import User
from app.schemas.actor import ActorContext
from app.services.agent_service import AgentService
from app.services.analytics_service import AnalyticsService
from app.services.unified_report_service import UnifiedReportService


@pytest.mark.asyncio
async def test_owner_team_sales_breakdown_analytics():
    """Verify that AnalyticsService aggregates team sales breakdown by member for owner."""
    analytics = AnalyticsService()
    biz_id = uuid4()
    owner_id = uuid4()
    staff1_id = uuid4()
    staff2_id = uuid4()

    m_staff1 = Member(id=staff1_id, business_id=biz_id, wa_id="2348011111111", display_name="Emeka", role="staff", status="active")
    m_staff2 = Member(id=staff2_id, business_id=biz_id, wa_id="2348022222222", display_name="Blessing", role="staff", status="active")

    now = datetime.now(UTC)
    tx1 = Transaction(
        id=uuid4(),
        business_id=biz_id,
        created_by_member_id=staff1_id,
        transaction_type="sale",
        amount=Decimal("45000"),
        status="confirmed",
        occurred_at=now,
    )
    tx2 = Transaction(
        id=uuid4(),
        business_id=biz_id,
        created_by_member_id=staff2_id,
        transaction_type="sale",
        amount=Decimal("120000"),
        status="confirmed",
        occurred_at=now,
    )
    tx3 = Transaction(
        id=uuid4(),
        business_id=biz_id,
        created_by_member_id=None,  # Direct / Owner
        transaction_type="sale",
        amount=Decimal("30000"),
        status="confirmed",
        occurred_at=now,
    )

    db = MagicMock()
    async def mock_execute(stmt, *args, **kwargs):
        s_str = str(stmt).lower()
        mock_res = MagicMock()
        if "from members" in s_str:
            mock_res.scalars.return_value.all.return_value = [m_staff1, m_staff2]
            return mock_res
        if "from transactions" in s_str:
            mock_res.scalars.return_value.all.return_value = [tx1, tx2, tx3]
            return mock_res
        mock_res.scalars.return_value.all.return_value = []
        return mock_res

    db.execute.side_effect = mock_execute

    res = await analytics.get_team_sales_breakdown(db, business_id=biz_id, period="this_week")

    assert res["total_sales"] == Decimal("195000.00")
    assert res["total_count"] == 3
    assert res["top_contributor"] == "Blessing"
    assert "Emeka" in res["summary_text"]
    assert "Blessing" in res["summary_text"]
    assert "Direct / Owner" in res["summary_text"]


@pytest.mark.asyncio
async def test_staff_personal_summary_isolation():
    """Verify that staff personal summary is strictly scoped to staff member's entries."""
    analytics = AnalyticsService()
    biz_id = uuid4()
    staff_id = uuid4()

    now = datetime.now(UTC)
    tx1 = Transaction(
        id=uuid4(),
        business_id=biz_id,
        created_by_member_id=staff_id,
        transaction_type="sale",
        amount=Decimal("25000"),
        item_name="Rice 50kg",
        quantity=Decimal("1"),
        status="confirmed",
        occurred_at=now,
    )
    tx2 = Transaction(
        id=uuid4(),
        business_id=biz_id,
        created_by_member_id=staff_id,
        transaction_type="sale",
        amount=Decimal("30000"),
        item_name="Rice 50kg",
        quantity=Decimal("1"),
        status="confirmed",
        occurred_at=now,
    )

    db = MagicMock()
    async def mock_execute(stmt, *args, **kwargs):
        mock_res = MagicMock()
        s_str = str(stmt).lower()
        # prior period check (strict < without <=)
        if "occurred_at < :" in s_str or "occurred_at < %" in s_str or ("occurred_at <" in s_str and "occurred_at <=" not in s_str):
            mock_res.scalars.return_value.all.return_value = []
        else:
            mock_res.scalars.return_value.all.return_value = [tx1, tx2]
        return mock_res

    db.execute.side_effect = mock_execute

    summary = await analytics.get_staff_personal_summary(
        db,
        business_id=biz_id,
        member_id=staff_id,
        member_name="Blessing Staff",
        period="this_week",
    )

    assert summary["member_name"] == "Blessing Staff"
    assert summary["total_sales"] == Decimal("55000.00")
    assert summary["total_count"] == 2
    assert summary["top_item"] == "Rice 50kg"
    assert "Personal Staff Performance Report - Blessing Staff" in summary["summary_text"]
    assert "Tips to Keep Improving" in summary["summary_text"]
    # Ensure zero leak of gross margins or store expenses
    assert "Margin" not in summary["summary_text"]
    assert "Expense" not in summary["summary_text"]


@pytest.mark.asyncio
async def test_unified_reports_staff_receives_personal_summary_only():
    """Verify that when staff requests a report, they receive only their personal report without trend charts."""
    whatsapp = MagicMock()
    whatsapp.send_text = AsyncMock(return_value={"messages": [{"id": "wamid.123"}]})
    ledger = MagicMock()
    ledger.record_outbound_message = AsyncMock()

    service = UnifiedReportService(whatsapp=whatsapp, ledger=ledger)

    biz_id = uuid4()
    staff_user_id = uuid4()
    staff_member_id = uuid4()

    user = User(
        id=staff_user_id,
        phone_number="2348022222222",
        display_name="Blessing Staff",
        business_id=biz_id,
        niche="sme_owner",
        last_inbound_at=datetime.now(UTC),
    )
    actor = ActorContext(
        business_id=biz_id,
        member_id=staff_member_id,
        role="staff",
        wa_id="2348022222222",
        display_name="Blessing Staff",
    )

    db = MagicMock()
    db.commit = AsyncMock()
    db.get = AsyncMock(return_value=user)

    now = datetime.now(UTC)
    tx = Transaction(
        id=uuid4(),
        business_id=biz_id,
        created_by_member_id=staff_member_id,
        transaction_type="sale",
        amount=Decimal("50000"),
        item_name="Generator",
        quantity=Decimal("1"),
        status="confirmed",
        occurred_at=now,
    )

    async def mock_execute(stmt, *args, **kwargs):
        mock_res = MagicMock()
        mock_res.scalars.return_value.all.return_value = [tx]
        return mock_res

    db.execute.side_effect = mock_execute

    result = await service.generate_and_deliver_report(
        db, user_id=staff_user_id, cadence="monthly", actor=actor
    )

    assert result["status"] == "sent"
    assert "Personal Staff Performance Report" in result["report"]
    assert result["chart"] is None  # Staff does NOT receive visual trend chart
    whatsapp.send_text.assert_called_once()


@pytest.mark.asyncio
async def test_staff_blocked_from_financial_chart_delivery():
    """Verify that staff members cannot trigger business financial chart deliveries."""
    service = UnifiedReportService()
    biz_id = uuid4()
    staff_user_id = uuid4()
    staff_member_id = uuid4()

    user = User(
        id=staff_user_id,
        phone_number="2348022222222",
        display_name="Blessing Staff",
        business_id=biz_id,
    )
    actor = ActorContext(
        business_id=biz_id,
        member_id=staff_member_id,
        role="staff",
        wa_id="2348022222222",
        display_name="Blessing Staff",
    )

    db = MagicMock()
    db.get = AsyncMock(return_value=user)

    res = await service.deliver_financial_chart(
        db, user_id=staff_user_id, period="monthly", actor=actor
    )

    assert res["status"] == "error"
    assert "restricted to the business owner" in res["message"]


@pytest.mark.asyncio
async def test_agent_tool_rbac_and_historical_summary_scoping():
    """Verify that AgentService enforces role-based tool restrictions on staff vs owner."""
    agent = AgentService()
    biz_id = uuid4()
    owner_id = uuid4()
    staff_id = uuid4()

    biz = Business(id=biz_id, name="Mega Store", phone_number="2348011111111", owner_name="Chief Obi")
    staff_user = User(id=staff_id, phone_number="2348022222222", display_name="Blessing", business_id=biz_id)
    owner_user = User(id=owner_id, phone_number="2348011111111", display_name="Chief Obi", business_id=biz_id)

    staff_actor = ActorContext(
        business_id=biz_id,
        member_id=staff_id,
        role="staff",
        wa_id="2348022222222",
        display_name="Blessing",
    )
    owner_actor = ActorContext(
        business_id=biz_id,
        member_id=owner_id,
        role="owner",
        wa_id="2348011111111",
        display_name="Chief Obi",
    )

    db = MagicMock()

    # 1. Staff blocked from get_team_performance
    res, _ = await agent._execute_tool(
        db, biz, staff_user, "get_team_performance", {"period": "this_week"}, raw_user_text="", actor=staff_actor
    )
    assert "restricted to the business owner" in res

    # 2. Staff blocked from get_margin_report
    res, _ = await agent._execute_tool(
        db, biz, staff_user, "get_margin_report", {"period": "this_month"}, raw_user_text="", actor=staff_actor
    )
    assert "restricted to the business owner" in res

    # 3. Staff blocked from get_product_performance_analysis
    res, _ = await agent._execute_tool(
        db, biz, staff_user, "get_product_performance_analysis", {"period": "this_month"}, raw_user_text="", actor=staff_actor
    )
    assert "restricted to the business owner" in res

    # 4. Staff blocked from simulate_pricing
    res, _ = await agent._execute_tool(
        db, biz, staff_user, "simulate_pricing", {"item_name": "Rice"}, raw_user_text="", actor=staff_actor
    )
    assert "restricted to the business owner" in res

    # 5. Staff blocked from log_upcoming_payable
    res, _ = await agent._execute_tool(
        db, biz, staff_user, "log_upcoming_payable", {"amount": 10000, "due_date": "2026-10-15"}, raw_user_text="", actor=staff_actor
    )
    assert "restricted to the business owner" in res

    # 6. Staff blocked from get_cash_flow_forecast
    res, _ = await agent._execute_tool(
        db, biz, staff_user, "get_cash_flow_forecast", {}, raw_user_text="", actor=staff_actor
    )
    assert "restricted to the business owner" in res

    # 7. Staff querying get_historical_summary sees only their personal records (no net profit or business expenses)
    now = datetime.now(UTC)
    staff_tx = Transaction(
        id=uuid4(),
        business_id=biz_id,
        created_by_member_id=staff_id,
        transaction_type="sale",
        amount=Decimal("15000"),
        item_name="Shoes",
        status="confirmed",
        occurred_at=now,
    )
    async def mock_execute(stmt, *args, **kwargs):
        mock_res = MagicMock()
        mock_res.scalars.return_value.all.return_value = [staff_tx]
        return mock_res
    db.execute.side_effect = mock_execute

    hist_res, _ = await agent._execute_tool(
        db, biz, staff_user, "get_historical_summary", {"start_date": "2026-10-01", "end_date": "2026-10-06"}, raw_user_text="", actor=staff_actor
    )
    assert "Personal Historical Summary" in hist_res
    assert "Total Sales Logged: ₦15,000.00" in hist_res
    assert "Net Profit" not in hist_res

    # 8. Owner executing get_team_performance returns breakdown
    with patch.object(agent.analytics, "get_team_sales_breakdown", AsyncMock(return_value={"summary_text": "👥 Team Sales Breakdown:\n• Blessing: ₦15,000.00"})):
        owner_res, _ = await agent._execute_tool(
            db, biz, owner_user, "get_team_performance", {"period": "this_week"}, raw_user_text="", actor=owner_actor
        )
        assert "Team Sales Breakdown" in owner_res
