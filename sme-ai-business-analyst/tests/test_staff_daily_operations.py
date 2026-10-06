import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4
from datetime import datetime, timedelta, timezone, UTC
from decimal import Decimal

from app.models.business import Business
from app.models.member import Member
from app.models.user import User
from app.models.transaction import Transaction
from app.models.inventory import InventoryItem
from app.models.debt import Debt
from app.models.confirmation import Confirmation
from app.schemas.actor import ActorContext
from app.schemas.whatsapp import ParsedWhatsAppMessage
from app.services.ledger_service import LedgerService
from app.services.receipt_service import ReceiptService
from app.services.webhook_processor import WhatsAppWebhookProcessor


@pytest.mark.asyncio
async def test_record_transaction_attribution():
    ledger = LedgerService()
    db = AsyncMock()

    biz_id = uuid4()
    member_id = uuid4()

    mock_res = MagicMock()
    mock_res.scalar_one_or_none.return_value = None
    db.execute.return_value = mock_res

    tx, debt, alert = await ledger.record_transaction(
        db=db,
        business_id=biz_id,
        record_type="sale",
        amount=15000.0,
        item_name="Nike Air Max",
        quantity=1.0,
        customer_name="Emeka",
        is_credit=True,
        created_by_member_id=member_id,
        status="confirmed",
    )

    assert tx.created_by_member_id == member_id
    assert debt is not None
    assert debt.created_by_member_id == member_id


@pytest.mark.asyncio
async def test_receipt_scoping_to_staff_member():
    receipt_svc = ReceiptService()
    db = AsyncMock()

    biz_id = uuid4()
    staff_a_id = uuid4()
    staff_b_id = uuid4()

    tx_a = Transaction(
        id=uuid4(),
        business_id=biz_id,
        created_by_member_id=staff_a_id,
        transaction_type="sale",
        status="confirmed",
        amount=Decimal("5000"),
        item_name="Wireless Mouse",
        quantity=Decimal("1"),
        occurred_at=datetime.now(UTC) - timedelta(minutes=10),
    )

    tx_b = Transaction(
        id=uuid4(),
        business_id=biz_id,
        created_by_member_id=staff_b_id,
        transaction_type="sale",
        status="confirmed",
        amount=Decimal("12000"),
        item_name="Mechanical Keyboard",
        quantity=Decimal("1"),
        occurred_at=datetime.now(UTC) - timedelta(minutes=2),
    )

    biz = Business(id=biz_id, name="Tech Hub", phone_number="2348011111111")
    staff_a = Member(id=staff_a_id, business_id=biz_id, display_name="Tunde Staff", role="staff")

    async def mock_execute(stmt, *args, **kwargs):
        s_str = str(stmt).lower()
        mock_res = MagicMock()
        if "from transactions" in s_str:
            if "created_by_member_id" in s_str:
                mock_res.scalar_one_or_none.return_value = tx_a
            else:
                mock_res.scalar_one_or_none.return_value = tx_b
            return mock_res
        if "from business_members" in s_str or "from members" in s_str:
            mock_res.scalar_one_or_none.return_value = staff_a
            return mock_res
        mock_res.scalar_one_or_none.return_value = None
        mock_res.scalars.return_value.all.return_value = []
        return mock_res

    db.execute.side_effect = mock_execute
    db.get.return_value = biz

    # When staff_a_id is passed, receipt scopes to staff A's sale
    pdf_bytes, filename, tx = await receipt_svc.generate_receipt_pdf(
        db, business_id=biz_id, member_id=staff_a_id
    )
    assert tx.id == tx_a.id
    assert tx.item_name == "Wireless Mouse"
    assert len(pdf_bytes) > 0


@pytest.mark.asyncio
async def test_void_transaction_staff_within_15_minutes():
    ledger = LedgerService()
    db = AsyncMock()

    biz_id = uuid4()
    staff_id = uuid4()
    tx_id = uuid4()

    tx = Transaction(
        id=tx_id,
        business_id=biz_id,
        created_by_member_id=staff_id,
        transaction_type="sale",
        status="confirmed",
        amount=Decimal("50000"),
        item_name="Smart Watch",
        quantity=Decimal("2"),
        occurred_at=datetime.now(UTC) - timedelta(minutes=8),  # Within 15-min window
    )

    actor = ActorContext(
        business_id=biz_id,
        member_id=staff_id,
        role="staff",
        wa_id="2348022222222",
        display_name="Blessing Staff",
    )

    inv_item = InventoryItem(
        id=uuid4(),
        business_id=biz_id,
        item_name="Smart Watch",
        normalized_item_name="smart watch",
        quantity_on_hand=Decimal("10"),
    )

    async def mock_execute(stmt, *args, **kwargs):
        s_str = str(stmt).lower()
        mock_res = MagicMock()
        if "from transactions" in s_str:
            mock_res.scalar_one_or_none.return_value = tx
            return mock_res
        if "from inventory_items" in s_str:
            mock_res.scalar_one_or_none.return_value = inv_item
            return mock_res
        if "from debts" in s_str:
            mock_res.scalar_one_or_none.return_value = None
            return mock_res
        mock_res.scalar_one_or_none.return_value = None
        return mock_res

    db.execute.side_effect = mock_execute

    res = await ledger.void_transaction(
        db=db,
        business_id=biz_id,
        actor=actor,
        transaction_id=tx_id,
        reason="Customer returned to swap items",
    )

    assert res["success"] is True
    assert tx.status == "void"
    assert inv_item.quantity_on_hand == Decimal("12")  # Restored 2 units
    assert res["notify_owner"] is True
    assert "Blessing Staff" in res["owner_alert_message"]


@pytest.mark.asyncio
async def test_void_transaction_staff_rejected_after_15_minutes():
    ledger = LedgerService()
    db = AsyncMock()

    biz_id = uuid4()
    staff_id = uuid4()
    tx_id = uuid4()

    tx = Transaction(
        id=tx_id,
        business_id=biz_id,
        created_by_member_id=staff_id,
        transaction_type="sale",
        status="confirmed",
        amount=Decimal("50000"),
        item_name="Smart Watch",
        quantity=Decimal("2"),
        occurred_at=datetime.now(UTC) - timedelta(minutes=18),  # Beyond 15-min window
    )

    actor = ActorContext(
        business_id=biz_id,
        member_id=staff_id,
        role="staff",
        wa_id="2348022222222",
        display_name="Blessing Staff",
    )

    async def mock_execute(stmt, *args, **kwargs):
        s_str = str(stmt).lower()
        mock_res = MagicMock()
        if "from transactions" in s_str:
            mock_res.scalar_one_or_none.return_value = tx
            return mock_res
        mock_res.scalar_one_or_none.return_value = None
        return mock_res

    db.execute.side_effect = mock_execute

    res = await ledger.void_transaction(
        db=db,
        business_id=biz_id,
        actor=actor,
        transaction_id=tx_id,
        reason="Customer cancelled",
    )

    assert res["success"] is False
    assert "15 minutes" in res["error"]
    assert "business owner" in res["error"]
    assert tx.status == "confirmed"  # Not altered


@pytest.mark.asyncio
async def test_void_transaction_staff_cannot_void_other_staff_record():
    ledger = LedgerService()
    db = AsyncMock()

    biz_id = uuid4()
    staff_a_id = uuid4()
    staff_b_id = uuid4()
    tx_id = uuid4()

    tx = Transaction(
        id=tx_id,
        business_id=biz_id,
        created_by_member_id=staff_b_id,  # Created by Staff B
        transaction_type="sale",
        status="confirmed",
        amount=Decimal("20000"),
        item_name="Shoes",
        occurred_at=datetime.now(UTC) - timedelta(minutes=5),
    )

    actor = ActorContext(
        business_id=biz_id,
        member_id=staff_a_id,  # Staff A attempting to void
        role="staff",
        wa_id="2348022222222",
        display_name="Staff A",
    )

    async def mock_execute(stmt, *args, **kwargs):
        mock_res = MagicMock()
        if "from transactions" in str(stmt).lower():
            mock_res.scalar_one_or_none.return_value = tx
            return mock_res
        mock_res.scalar_one_or_none.return_value = None
        return mock_res

    db.execute.side_effect = mock_execute

    res = await ledger.void_transaction(
        db=db,
        business_id=biz_id,
        actor=actor,
        transaction_id=tx_id,
        reason="Mistake",
    )

    assert res["success"] is False
    assert "they recorded" in res["error"]


@pytest.mark.asyncio
async def test_void_transaction_owner_can_void_anytime():
    ledger = LedgerService()
    db = AsyncMock()

    biz_id = uuid4()
    owner_id = uuid4()
    staff_id = uuid4()
    tx_id = uuid4()

    tx = Transaction(
        id=tx_id,
        business_id=biz_id,
        created_by_member_id=staff_id,
        transaction_type="sale",
        status="confirmed",
        amount=Decimal("20000"),
        item_name="Headphones",
        quantity=Decimal("1"),
        occurred_at=datetime.now(UTC) - timedelta(hours=5),  # 5 hours ago
    )

    actor = ActorContext(
        business_id=biz_id,
        member_id=owner_id,
        role="owner",
        wa_id="2348011111111",
        display_name="Boss",
    )

    inv_item = InventoryItem(
        id=uuid4(),
        business_id=biz_id,
        item_name="Headphones",
        normalized_item_name="headphones",
        quantity_on_hand=Decimal("4"),
    )

    async def mock_execute(stmt, *args, **kwargs):
        s_str = str(stmt).lower()
        mock_res = MagicMock()
        if "from transactions" in s_str:
            mock_res.scalar_one_or_none.return_value = tx
            return mock_res
        if "from inventory_items" in s_str:
            mock_res.scalar_one_or_none.return_value = inv_item
            return mock_res
        if "from debts" in s_str:
            mock_res.scalar_one_or_none.return_value = None
            return mock_res
        mock_res.scalar_one_or_none.return_value = None
        return mock_res

    db.execute.side_effect = mock_execute

    res = await ledger.void_transaction(
        db=db,
        business_id=biz_id,
        actor=actor,
        transaction_id=tx_id,
        reason="Owner administrative audit",
    )

    assert res["success"] is True
    assert tx.status == "void"
    assert inv_item.quantity_on_hand == Decimal("5")  # Restored 1 unit


@pytest.mark.asyncio
async def test_expense_approval_threshold_permissions():
    ledger = LedgerService()
    db = AsyncMock()

    biz_id = uuid4()
    owner_id = uuid4()
    staff_id = uuid4()

    biz = Business(
        id=biz_id,
        name="Lekki Store",
        settings={},
    )
    db.get.return_value = biz

    owner_actor = ActorContext(
        business_id=biz_id,
        member_id=owner_id,
        role="owner",
        wa_id="2348011111111",
        display_name="Owner",
    )

    staff_actor = ActorContext(
        business_id=biz_id,
        member_id=staff_id,
        role="staff",
        wa_id="2348022222222",
        display_name="Staff",
    )

    # Owner sets threshold
    res_owner = await ledger.set_expense_approval_threshold(
        db=db,
        business_id=biz_id,
        actor=owner_actor,
        threshold_amount=Decimal("75000"),
    )
    assert res_owner["success"] is True
    assert biz.settings["expense_approval_threshold"] == 75000.0

    # Staff attempt rejected
    res_staff = await ledger.set_expense_approval_threshold(
        db=db,
        business_id=biz_id,
        actor=staff_actor,
        threshold_amount=Decimal("10000"),
    )
    assert res_staff["success"] is False
    assert "Only the business owner" in res_staff["error"]


@pytest.mark.asyncio
async def test_webhook_dual_low_stock_and_expense_alerts():
    db = AsyncMock()
    whatsapp = AsyncMock()
    ledger = LedgerService()
    processor = WhatsAppWebhookProcessor(
        extractor=MagicMock(),
        confirmations=MagicMock(),
        whatsapp=whatsapp,
        ledger=ledger,
        agent=MagicMock(),
    )

    biz_id = uuid4()
    staff_id = uuid4()
    biz = Business(
        id=biz_id,
        name="Lekki Mart",
        phone_number="2348011111111",  # Owner phone
        settings={"expense_approval_threshold": 30000.0},
    )
    staff_user = User(
        id=uuid4(),
        phone_number="2348022222222",  # Staff phone
        display_name="Emeka Staff",
        role="staff",
    )
    staff_member = Member(
        id=staff_id,
        business_id=biz_id,
        display_name="Emeka Staff",
        role="staff",
        status="active",
    )
    actor = ActorContext(
        business_id=biz_id,
        member_id=staff_id,
        role="staff",
        wa_id="2348022222222",
        display_name="Emeka Staff",
    )

    # 1. Low stock scenario
    conf_id = uuid4()
    pending_conf = Confirmation(
        id=conf_id,
        business_id=biz_id,
        member_id=staff_id,
        status="pending",
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )

    confirmed_sale = Transaction(
        id=uuid4(),
        business_id=biz_id,
        confirmation_id=conf_id,
        transaction_type="sale",
        status="confirmed",
        amount=Decimal("10000"),
        item_name="Basmati Rice",
        quantity=Decimal("5"),
    )

    processor.confirmations.latest_actionable = AsyncMock(return_value=pending_conf)
    processor.confirmations.confirm = AsyncMock(
        return_value="Done. I have confirmed and saved the record.\n\n⚠️ Low Stock Alert: Basmati Rice is down to 2 bags (reorder threshold: 5 bags)."
    )

    async def mock_execute(stmt, *args, **kwargs):
        s_str = str(stmt).lower()
        mock_res = MagicMock()
        if "from transactions" in s_str:
            mock_res.scalar_one_or_none.return_value = confirmed_sale
            return mock_res
        if "from users" in s_str:
            mock_res.scalar_one_or_none.return_value = staff_user
            return mock_res
        if "from business_members" in s_str or "from members" in s_str:
            mock_res.scalar_one_or_none.return_value = staff_member
            return mock_res
        if "from businesses" in s_str:
            mock_res.scalar_one_or_none.return_value = biz
            return mock_res
        mock_res.scalar_one_or_none.return_value = None
        return mock_res

    db.execute.side_effect = mock_execute

    parsed_msg = ParsedWhatsAppMessage(
        message_id="wamid.123",
        from_phone="2348022222222",
        message_type="interactive",
        body="confirm",
        interactive_reply_id="confirm_yes",
    )

    with patch.object(ledger, "get_or_create_business_and_user", AsyncMock(return_value=(biz, staff_user, staff_member, actor))):
        await processor.process_message(db, parsed_msg, event={})

    # Verify whatsapp.send_text was called with owner's phone for low stock alert
    owner_calls = [
        call for call in whatsapp.send_text.call_args_list
        if call[0][0] == "2348011111111" and "Low-Stock Alert" in call[0][1]
    ]
    assert len(owner_calls) > 0
    assert "Emeka Staff" in owner_calls[0][0][1]
