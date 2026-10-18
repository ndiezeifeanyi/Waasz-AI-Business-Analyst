"""
Tests for conversational agent's Read & Act tools:
- get_recent_transactions
- update_transaction
- get_batch_items
- get_inventory_status
"""
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4
import pytest

from app.models.business import Business
from app.models.inventory import InventoryItem
from app.models.transaction import Transaction
from app.models.user import User
from app.schemas.actor import ActorContext
from app.services.agent_service import AgentService


@pytest.mark.asyncio
async def test_agent_get_recent_transactions_tool():
    """Verify get_recent_transactions retrieves and formats recent records cleanly."""
    agent = AgentService()
    db = AsyncMock()
    biz_id = uuid4()
    user_id = uuid4()
    mock_biz = Business(id=biz_id, name="Test Enterprise")
    mock_user = User(id=user_id, business_id=biz_id, phone_number="2348000000000")
    actor = ActorContext(business_id=biz_id, member_id=user_id, role="owner", wa_id="2348000000000", display_name="Owner")

    tx1 = Transaction(
        id=uuid4(),
        business_id=biz_id,
        transaction_type="sale",
        item_name="Brake Pad",
        quantity=Decimal("2"),
        unit="sets",
        amount=Decimal("24000.00"),
        status="confirmed",
        occurred_at=datetime.now(UTC),
        is_credit=False,
    )
    tx2 = Transaction(
        id=uuid4(),
        business_id=biz_id,
        transaction_type="sale",
        item_name="Engine Oil",
        quantity=Decimal("1"),
        unit="can",
        amount=Decimal("15000.00"),
        status="confirmed",
        occurred_at=datetime.now(UTC),
        is_credit=True,
    )

    q_res = MagicMock()
    q_res.scalars.return_value.all.return_value = [tx1, tx2]
    db.execute.return_value = q_res

    res_text, _ = await agent._execute_tool(
        db=db,
        business=mock_biz,
        user=mock_user,
        tool_name="get_recent_transactions",
        tool_args={"limit": 5, "days": 7},
        raw_user_text="Show recent sales",
        actor=actor,
    )

    assert "Recent Confirmed Transactions" in res_text
    assert "Brake Pad" in res_text
    assert "₦24,000.00" in res_text
    assert "Engine Oil" in res_text
    assert "[Credit]" in res_text


@pytest.mark.asyncio
async def test_agent_update_transaction_tool():
    """Verify update_transaction modifies confirmed entries without manual database surgery."""
    agent = AgentService()
    agent.ledger = MagicMock()
    agent.ledger.record_audit_log = AsyncMock()
    db = AsyncMock()
    biz_id = uuid4()
    user_id = uuid4()
    tx_id = uuid4()
    mock_biz = Business(id=biz_id, name="Test Enterprise")
    mock_user = User(id=user_id, business_id=biz_id, phone_number="2348000000000")
    actor = ActorContext(business_id=biz_id, member_id=user_id, role="owner", wa_id="2348000000000", display_name="Owner")

    target_tx = Transaction(
        id=tx_id,
        business_id=biz_id,
        transaction_type="sale",
        item_name="Ball Joint",
        quantity=Decimal("1"),
        amount=Decimal("12000.00"),
        status="confirmed",
        occurred_at=datetime.now(UTC),
    )

    q_res = MagicMock()
    q_res.scalar_one_or_none.return_value = target_tx
    db.execute.return_value = q_res

    res_text, _ = await agent._execute_tool(
        db=db,
        business=mock_biz,
        user=mock_user,
        tool_name="update_transaction",
        tool_args={"identifier": "Ball Joint", "new_amount": 15000.0, "new_quantity": 2},
        raw_user_text="Ball joint was 15k not 12k",
        actor=actor,
    )

    assert "Success: Transaction updated" in res_text
    assert "15,000.00" in res_text
    assert target_tx.amount == Decimal("15000")
    assert target_tx.quantity == Decimal("2")
    assert agent.ledger.record_audit_log.called


@pytest.mark.asyncio
async def test_agent_get_batch_items_tool():
    """Verify get_batch_items itemizes multi-item sales."""
    agent = AgentService()
    db = AsyncMock()
    biz_id = uuid4()
    user_id = uuid4()
    shared_extraction = uuid4()
    mock_biz = Business(id=biz_id, name="Auto Parts Hub")
    mock_user = User(id=user_id, business_id=biz_id, phone_number="2348000000000")
    actor = ActorContext(business_id=biz_id, member_id=user_id, role="owner", wa_id="2348000000000", display_name="Owner")

    items = [
        Transaction(id=uuid4(), business_id=biz_id, ai_extraction_id=shared_extraction, item_name="Spark Plug", quantity=Decimal("4"), amount=Decimal("8000.00"), status="confirmed"),
        Transaction(id=uuid4(), business_id=biz_id, ai_extraction_id=shared_extraction, item_name="Brake Fluid", quantity=Decimal("1"), amount=Decimal("3500.00"), status="confirmed"),
    ]

    primary_res = MagicMock()
    primary_res.scalar_one_or_none.return_value = items[0]
    sibling_res = MagicMock()
    sibling_res.scalars.return_value.all.return_value = items

    async def mock_execute(query, *args, **kwargs):
        q_str = str(query).lower()
        if "limit" in q_str:
            return primary_res
        return sibling_res

    db.execute = AsyncMock(side_effect=mock_execute)

    res_text, _ = await agent._execute_tool(
        db=db,
        business=mock_biz,
        user=mock_user,
        tool_name="get_batch_items",
        tool_args={},
        raw_user_text="What was in that batch?",
        actor=actor,
    )

    assert "Batch Items Breakdown (2 items, Total: ₦11,500.00)" in res_text
    assert "Spark Plug" in res_text
    assert "Brake Fluid" in res_text


@pytest.mark.asyncio
async def test_agent_get_inventory_status_tool():
    """Verify get_inventory_status reports quantities, thresholds, and low-stock alerts."""
    agent = AgentService()
    db = AsyncMock()
    biz_id = uuid4()
    user_id = uuid4()
    mock_biz = Business(id=biz_id, name="Rice World")
    mock_user = User(id=user_id, business_id=biz_id, phone_number="2348000000000")
    actor = ActorContext(business_id=biz_id, member_id=user_id, role="owner", wa_id="2348000000000", display_name="Owner")

    inv1 = InventoryItem(
        id=uuid4(),
        business_id=biz_id,
        item_name="Mama Gold Rice 50kg",
        normalized_item_name="mama gold rice 50kg",
        quantity_on_hand=Decimal("5"),
        unit="bags",
        low_stock_threshold=Decimal("10"),
        unit_cost=Decimal("75000.00"),
    )

    q_res = MagicMock()
    q_res.scalars.return_value.all.return_value = [inv1]
    db.execute.return_value = q_res

    res_text, _ = await agent._execute_tool(
        db=db,
        business=mock_biz,
        user=mock_user,
        tool_name="get_inventory_status",
        tool_args={"low_stock_only": True},
        raw_user_text="Check low stock items",
        actor=actor,
    )

    assert "Inventory Status" in res_text
    assert "Mama Gold Rice 50kg: 5 bags" in res_text
    assert "LOW STOCK" in res_text
