from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4
import pytest
from sqlalchemy import delete, select

from app.core.database import async_session_factory
from app.models.activity import Activity
from app.models.business import Business
from app.models.transaction import Transaction
from app.models.user import User
from app.services.agent_service import AgentService
from app.services.ledger_service import LedgerService
from app.utils.phone import normalize_phone


@pytest.fixture(autouse=True)
async def cleanup_historical_test_records():
    from app.core.database import engine
    await engine.dispose()
    test_phone = normalize_phone("+2348000000661")

    yield

    async with async_session_factory() as session:
        b_res = await session.execute(select(Business).where(Business.phone_number == test_phone))
        for b in b_res.scalars().all():
            await session.execute(delete(Activity).where(Activity.business_id == b.id))
            await session.execute(delete(Transaction).where(Transaction.business_id == b.id))
            await session.execute(delete(User).where(User.business_id == b.id))
            await session.execute(delete(Business).where(Business.id == b.id))
        await session.execute(delete(User).where(User.phone_number == test_phone))
        await session.commit()
    await engine.dispose()


@pytest.mark.asyncio
async def test_get_historical_summary_across_multi_month_dataset():
    """
    Test arbitrary date range recall tool (get_historical_summary) against
    a seeded multi-month dataset spanning January, February, and March.
    """
    test_phone = "+2348000000661"
    ledger = LedgerService()
    agent = AgentService()

    async with async_session_factory() as session:
        business, user = await ledger.get_or_create_business_and_user(session, test_phone)
        b_id = business.id
        u_id = user.id

        # Month 1: January 15, 2026
        t_jan_sale = Transaction(
            business_id=b_id,
            transaction_type="sale",
            amount=Decimal("50000.00"),
            currency="NGN",
            item_name="January Bags",
            description="Sold bags in Jan",
            status="confirmed",
            occurred_at=datetime(2026, 1, 15, 10, 0, tzinfo=UTC),
        )
        t_jan_exp = Transaction(
            business_id=b_id,
            transaction_type="expense",
            amount=Decimal("10000.00"),
            currency="NGN",
            item_name="January Fuel",
            description="Fuel in Jan",
            status="confirmed",
            occurred_at=datetime(2026, 1, 15, 14, 0, tzinfo=UTC),
        )

        # Month 2: February 10, 2026
        t_feb_sale = Transaction(
            business_id=b_id,
            transaction_type="sale",
            amount=Decimal("80000.00"),
            currency="NGN",
            item_name="February Rice",
            description="Sold rice in Feb",
            status="confirmed",
            occurred_at=datetime(2026, 2, 10, 11, 30, tzinfo=UTC),
        )
        t_feb_exp = Transaction(
            business_id=b_id,
            transaction_type="expense",
            amount=Decimal("20000.00"),
            currency="NGN",
            item_name="February Delivery",
            description="Delivery in Feb",
            status="confirmed",
            occurred_at=datetime(2026, 2, 10, 16, 0, tzinfo=UTC),
        )

        # Month 3: March 3, 2026
        t_mar_sale = Transaction(
            business_id=b_id,
            transaction_type="sale",
            amount=Decimal("120000.00"),
            currency="NGN",
            item_name="March Cement",
            description="Sold cement on March 3rd",
            status="confirmed",
            occurred_at=datetime(2026, 3, 3, 9, 15, tzinfo=UTC),
        )
        t_mar_exp = Transaction(
            business_id=b_id,
            transaction_type="expense",
            amount=Decimal("30000.00"),
            currency="NGN",
            item_name="March Offloading",
            description="Offloading on March 3rd",
            status="confirmed",
            occurred_at=datetime(2026, 3, 3, 15, 45, tzinfo=UTC),
        )

        session.add_all([t_jan_sale, t_jan_exp, t_feb_sale, t_feb_exp, t_mar_sale, t_mar_exp])

        # Seed an activity on March 3rd
        act_mar = Activity(
            business_id=b_id,
            user_id=u_id,
            activity_type="milestone",
            title="Recorded Largest Single Day Cement Sale",
            details={"notes": "Special supplier delivery"},
            visibility="business_shared",
            occurred_at=datetime(2026, 3, 3, 18, 0, tzinfo=UTC),
        )
        session.add(act_mar)
        await session.commit()

    # Query 1: Single day recall — "what happened on March 3rd"
    async with async_session_factory() as session:
        b_curr, u_curr = await ledger.get_or_create_business_and_user(session, test_phone)
        result_str, was_interactive = await agent._execute_tool(
            db=session,
            business=b_curr,
            user=u_curr,
            tool_name="get_historical_summary",
            tool_args={"start_date": "2026-03-03"},
            raw_user_text="what happened on March 3rd?",
        )
        assert was_interactive is False
        assert "Historical Summary for period [2026-03-03]" in result_str
        assert "Total Confirmed Records: 2" in result_str
        assert "Total Sales: ₦120,000.00" in result_str
        assert "Total Expenses: ₦30,000.00" in result_str
        assert "Net Profit / Balance: ₦90,000.00" in result_str
        assert "March Cement" in result_str
        assert "Recorded Largest Single Day Cement Sale" in result_str
        # Verify January and February are NOT in the March 3rd results
        assert "January Bags" not in result_str
        assert "February Rice" not in result_str

    # Query 2: Entire month recall — February 2026
    async with async_session_factory() as session:
        b_curr, u_curr = await ledger.get_or_create_business_and_user(session, test_phone)
        result_str, _ = await agent._execute_tool(
            db=session,
            business=b_curr,
            user=u_curr,
            tool_name="get_historical_summary",
            tool_args={"start_date": "2026-02-01", "end_date": "2026-02-28"},
            raw_user_text="show me last month (February)",
        )
        assert "Total Confirmed Records: 2" in result_str
        assert "Total Sales: ₦80,000.00" in result_str
        assert "Total Expenses: ₦20,000.00" in result_str
        assert "Net Profit / Balance: ₦60,000.00" in result_str
        assert "February Rice" in result_str
        assert "January Bags" not in result_str
        assert "March Cement" not in result_str

    # Query 3: Multi-month range recall — January 1 to March 31
    async with async_session_factory() as session:
        b_curr, u_curr = await ledger.get_or_create_business_and_user(session, test_phone)
        result_str, _ = await agent._execute_tool(
            db=session,
            business=b_curr,
            user=u_curr,
            tool_name="get_historical_summary",
            tool_args={"start_date": "2026-01-01", "end_date": "2026-03-31"},
            raw_user_text="summary for entire Q1",
        )
        assert "Total Confirmed Records: 6" in result_str
        assert "Total Sales: ₦250,000.00" in result_str
        assert "Total Expenses: ₦60,000.00" in result_str
        assert "Net Profit / Balance: ₦190,000.00" in result_str
        assert "January Bags" in result_str
        assert "February Rice" in result_str
        assert "March Cement" in result_str

    # Query 4: Type-filtered recall — sales only in Q1
    async with async_session_factory() as session:
        b_curr, u_curr = await ledger.get_or_create_business_and_user(session, test_phone)
        result_str, _ = await agent._execute_tool(
            db=session,
            business=b_curr,
            user=u_curr,
            tool_name="get_historical_summary",
            tool_args={"start_date": "2026-01-01", "end_date": "2026-03-31", "record_type": "sale"},
            raw_user_text="show only my sales for Q1",
        )
        assert "Total Confirmed Records: 3" in result_str
        assert "Total Sales: ₦250,000.00 (3 sales)" in result_str
        assert "Total Expenses: ₦0.00 (0 expenses)" in result_str
        assert "SALE" in result_str
        assert "EXPENSE" not in result_str
