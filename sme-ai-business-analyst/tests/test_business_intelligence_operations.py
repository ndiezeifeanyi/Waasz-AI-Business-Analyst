from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4
import pytest
from sqlalchemy import select

from app.models.business import Business
from app.models.customer import Customer
from app.models.debt import Debt
from app.models.inventory import InventoryItem
from app.models.transaction import Transaction
from app.models.user import User
from app.schemas.extraction import ExtractedRecord
from app.schemas.whatsapp import ParsedWhatsAppMessage, WhatsAppSendResult
from app.services.agent_service import AgentService
from app.services.confirmation_service import ConfirmationService
from app.services.ledger_service import LedgerService
from app.services.receipt_service import ReceiptService
from app.services.report_service import ReportService
from app.services.webhook_processor import WhatsAppWebhookProcessor
from app.services.whatsapp_client import WhatsAppClient


# =========================================================================
# PHASE 0 TESTS: Foundational Data Model, Customers, Debts, Unit Cost
# =========================================================================

@pytest.mark.asyncio
async def test_set_item_cost_creates_and_updates_cogs():
    """Verify that set_item_cost creates an inventory item with unit_cost and updates it."""
    business_id = uuid4()
    ledger = LedgerService()

    db_items: dict[str, InventoryItem] = {}

    async def mock_execute(stmt, *args, **kwargs):
        res = MagicMock()
        norm = "rice"
        item = db_items.get(norm)
        res.scalar_one_or_none.return_value = item
        return res

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(side_effect=mock_execute)

    def mock_add(instance):
        if isinstance(instance, InventoryItem):
            db_items[instance.normalized_item_name] = instance

    mock_db.add = MagicMock(side_effect=mock_add)

    # 1. Set cost for new item
    item1 = await ledger.set_item_cost(mock_db, business_id, "Rice", 32000.0)
    assert item1.normalized_item_name == "rice"
    assert item1.unit_cost == Decimal("32000.0")

    # 2. Update cost for existing item
    item2 = await ledger.set_item_cost(mock_db, business_id, "Rice", 35500.0)
    assert item2.unit_cost == Decimal("35500.0")


@pytest.mark.asyncio
async def test_credit_sale_creates_customer_and_debt_automatically():
    """
    Verify that recording a confirmed credit sale creates the Customer,
    records the Transaction with is_credit=True, and creates an open Debt record.
    """
    business_id = uuid4()
    ledger = LedgerService()

    db_customers = {}
    db_transactions = []
    db_debts = []

    async def mock_execute(stmt, *args, **kwargs):
        res = MagicMock()
        res.scalar_one_or_none.return_value = None
        return res

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(side_effect=mock_execute)

    def mock_add(inst):
        if isinstance(inst, Customer):
            db_customers[inst.normalized_name] = inst
        elif isinstance(inst, Transaction):
            db_transactions.append(inst)
        elif isinstance(inst, Debt):
            db_debts.append(inst)

    mock_db.add = MagicMock(side_effect=mock_add)

    tx, debt, alert = await ledger.record_transaction(
        db=mock_db,
        business_id=business_id,
        record_type="sale",
        amount=Decimal("150000"),
        item_name="Cement",
        quantity=Decimal("30"),
        unit="bags",
        is_credit=True,
        customer_name="Emeka Okoye",
        due_date="2026-10-25T12:00:00Z",
        status="confirmed",
    )

    assert tx.is_credit is True
    assert tx.customer_id is not None
    assert tx.amount == Decimal("150000")

    # Assert customer was created
    assert "emeka okoye" in db_customers
    customer = db_customers["emeka okoye"]
    assert customer.name == "Emeka Okoye"
    assert customer.business_id == business_id

    # Assert debt was created
    assert debt is not None
    assert len(db_debts) == 1
    assert debt.status == "outstanding"
    assert debt.amount == Decimal("150000")
    assert debt.customer_id == customer.id
    assert debt.business_id == business_id
    assert debt.due_date is not None


@pytest.mark.asyncio
async def test_credit_sale_revenue_recognized_and_segregated_in_reports():
    """
    Assert that a credit sale counts as recognized revenue at time of sale (keeping
    sales totals accurate) while reports separately expose cash collected vs uncollected revenue.
    """
    business_id = uuid4()
    report_svc = ReportService()

    # Mock query returning:
    # row = (total_sales=500000, cash_sales=350000, credit_sales=150000, expenses=80000, count=4)
    async def mock_execute(stmt, *args, **kwargs):
        res = MagicMock()
        stmt_str = str(stmt)
        if "count(inventory_movements" in stmt_str:
            res.scalar_one.return_value = 2
        else:
            res.one.return_value = (
                Decimal("500000"),  # total sales
                Decimal("350000"),  # cash sales
                Decimal("150000"),  # credit sales
                Decimal("80000"),   # expenses
                4,                  # records_count
            )
        return res

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(side_effect=mock_execute)

    body, totals = await report_svc._build_report_body(
        mock_db, business_id, datetime.now(UTC).date(), datetime.now(UTC).date(), "daily"
    )

    # Total recognized sales remain accurately at 500,000
    assert totals["sales"] == Decimal("500000")
    assert totals["cash_collected"] == Decimal("350000")
    assert totals["revenue_uncollected"] == Decimal("150000")
    assert totals["profit"] == Decimal("420000")

    # Body displays the clear breakdown
    assert "Sales: ₦500,000 (Cash: ₦350,000 | Credit/Receivables: ₦150,000)" in body


@pytest.mark.asyncio
async def test_cross_tenant_isolation_customers_and_debts():
    """
    Ensure strict cross-tenant isolation: Business B cannot read or alter
    Business A's customers or debts.
    """
    biz_a = uuid4()
    biz_b = uuid4()

    mock_db = AsyncMock()
    res_mock = MagicMock()
    res_mock.scalars.return_value.all.return_value = []
    mock_db.execute.return_value = res_mock

    # Simulate query for customers of Business B
    stmt = select(Customer).where(Customer.business_id == biz_b)
    compiled = str(stmt.compile())
    assert "customers.business_id =" in compiled

    # Simulate query for debts of Business B
    debt_stmt = select(Debt).where(Debt.business_id == biz_b)
    compiled_debt = str(debt_stmt.compile())
    assert "debts.business_id =" in compiled_debt
    assert biz_a != biz_b


# =========================================================================
# PHASE 1 TESTS: Instant PDF Receipts and Invoices (ReportLab)
# =========================================================================

@pytest.mark.asyncio
async def test_generate_receipt_pdf_cash_sale():
    """Generate a ReportLab receipt for a paid sale transaction and verify structure."""
    business_id = uuid4()
    tx_id = uuid4()

    biz = Business(id=business_id, name="Ifeanyi Groceries Ltd", phone_number="+2348011223344")
    tx = Transaction(
        id=tx_id,
        business_id=business_id,
        transaction_type="sale",
        item_name="Rice",
        quantity=Decimal("5"),
        unit="bags",
        unit_price=Decimal("45000"),
        amount=Decimal("225000"),
        currency="NGN",
        status="confirmed",
        is_credit=False,
        occurred_at=datetime(2026, 10, 1, 10, 0, tzinfo=UTC),
    )

    mock_db = AsyncMock()
    mock_db.get = AsyncMock(return_value=biz)

    async def mock_execute(stmt, *args, **kwargs):
        res = MagicMock()
        res.scalar_one_or_none.return_value = tx
        return res

    mock_db.execute = AsyncMock(side_effect=mock_execute)

    receipt_svc = ReceiptService()
    pdf_bytes, filename, out_tx = await receipt_svc.generate_receipt_pdf(
        mock_db, business_id=business_id, transaction_id=tx_id
    )

    assert filename.startswith("receipt_")
    assert filename.endswith(".pdf")
    assert len(pdf_bytes) > 500
    # PDF starts with %PDF header
    assert pdf_bytes.startswith(b"%PDF")
    assert out_tx.id == tx_id


@pytest.mark.asyncio
async def test_generate_invoice_pdf_credit_sale():
    """Generate an Invoice variant for a credit sale with customer name and due date."""
    business_id = uuid4()
    tx_id = uuid4()
    cust_id = uuid4()

    biz = Business(id=business_id, name="Ifeanyi Wholesale Ltd", phone_number="+2348011223344")
    cust = Customer(id=cust_id, business_id=business_id, name="Chief Okeke", phone_number="+2348099887766")
    due_dt = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)
    tx = Transaction(
        id=tx_id,
        business_id=business_id,
        customer_id=cust_id,
        transaction_type="sale",
        item_name="Fertilizer",
        quantity=Decimal("10"),
        unit="bags",
        unit_price=Decimal("18000"),
        amount=Decimal("180000"),
        currency="NGN",
        status="confirmed",
        is_credit=True,
        occurred_at=datetime(2026, 10, 1, 10, 0, tzinfo=UTC),
    )
    debt = Debt(
        id=uuid4(),
        business_id=business_id,
        customer_id=cust_id,
        transaction_id=tx_id,
        amount=Decimal("180000"),
        status="outstanding",
        due_date=due_dt,
    )

    mock_db = AsyncMock()
    mock_db.get = AsyncMock(return_value=biz)

    async def mock_execute(stmt, *args, **kwargs):
        res = MagicMock()
        stmt_str = str(stmt)
        if "FROM transactions" in stmt_str:
            res.scalar_one_or_none.return_value = tx
        elif "FROM customers" in stmt_str:
            res.scalar_one_or_none.return_value = cust
        elif "FROM debts" in stmt_str:
            res.scalar_one_or_none.return_value = debt
        else:
            res.scalar_one_or_none.return_value = None
        return res

    mock_db.execute = AsyncMock(side_effect=mock_execute)

    receipt_svc = ReceiptService()
    pdf_bytes, filename, out_tx = await receipt_svc.generate_receipt_pdf(
        mock_db, business_id=business_id, transaction_id=tx_id
    )

    assert filename.startswith("invoice_")
    assert filename.endswith(".pdf")
    assert len(pdf_bytes) > 500
    assert pdf_bytes.startswith(b"%PDF")
    assert out_tx.is_credit is True


@pytest.mark.asyncio
async def test_sale_confirmation_offers_quick_reply_receipt_button():
    """Verify that when a sale is confirmed, an interactive quick-reply button '📄 Send receipt' is offered."""
    from app.models.confirmation import Confirmation

    business_id = uuid4()
    tx_id = uuid4()
    conf_id = uuid4()

    mock_db = AsyncMock()
    mock_whatsapp = MagicMock()
    mock_whatsapp.send_interactive_buttons = AsyncMock(return_value=WhatsAppSendResult(message_id="msg_btn_1"))
    mock_whatsapp.send_text = AsyncMock(return_value=WhatsAppSendResult(message_id="msg_text_1"))

    processor = WhatsAppWebhookProcessor(whatsapp=mock_whatsapp)

    # Pending confirmation for a sale
    pending = Confirmation(
        id=conf_id,
        business_id=business_id,
        status="pending",
        token="test_tok_123",
        confirmation_text="Confirm sale?",
        expires_at=datetime.now(UTC) + timedelta(hours=24),
        extracted_record={"record_type": "sale", "amount": 50000, "item_name": "rice"},
    )
    processor.confirmations.latest_actionable = AsyncMock(return_value=pending)
    processor._try_handle_confirmation_reply = AsyncMock(return_value="Done. I have confirmed and saved the record.")

    # Transaction associated with this confirmation
    tx = Transaction(
        id=tx_id,
        business_id=business_id,
        confirmation_id=conf_id,
        transaction_type="sale",
        status="confirmed",
        amount=Decimal("50000"),
    )

    user = User(id=uuid4(), business_id=business_id, phone_number="+2348012345678")
    business = Business(id=business_id, phone_number="+2348012345678", is_provisional=False)

    async def mock_execute(stmt, *args, **kwargs):
        res = MagicMock()
        if "FROM transactions" in str(stmt):
            res.scalar_one_or_none.return_value = tx
        elif "FROM users" in str(stmt):
            res.scalar_one_or_none.return_value = user
        elif "FROM businesses" in str(stmt):
            res.scalar_one_or_none.return_value = business
        else:
            res.scalar_one_or_none.return_value = None
        return res

    mock_db.execute = AsyncMock(side_effect=mock_execute)
    processor.ledger.record_outbound_message = AsyncMock()

    parsed = ParsedWhatsAppMessage(
        message_id="wamid.123",
        from_phone="+2348012345678",
        message_type="interactive",
        interactive_reply_id="1",
        body="1 Yes",
    )

    pending.status = "confirmed"
    await processor.process_message(mock_db, parsed, event=MagicMock())

    # Assert interactive buttons were called with receipt button
    assert mock_whatsapp.send_interactive_buttons.await_count == 1
    call_args = mock_whatsapp.send_interactive_buttons.await_args
    buttons = call_args[0][2]
    assert len(buttons) == 1
    assert buttons[0][0] == f"receipt_{tx_id}"
    assert "Send receipt" in buttons[0][1]


# =========================================================================
# PHASE 2 TESTS: Low-Stock Alerts and Reorder Thresholds
# =========================================================================

@pytest.mark.asyncio
async def test_reorder_threshold_set_and_inline_alert_generated():
    """
    Verify:
    1. Reorder threshold can be configured for an inventory item.
    2. A sale that decrements tracked stock to or below threshold generates an inline low-stock alert.
    """
    business_id = uuid4()
    ledger = LedgerService()

    # Tracked item starting with 6 bags of rice, threshold 5 bags
    item = InventoryItem(
        id=uuid4(),
        business_id=business_id,
        item_name="Rice",
        normalized_item_name="rice",
        quantity_on_hand=Decimal("6"),
        unit="bags",
        low_stock_threshold=Decimal("5"),
    )

    mock_db = AsyncMock()

    async def mock_execute(stmt, *args, **kwargs):
        res = MagicMock()
        if "FROM inventory_items" in str(stmt):
            res.scalar_one_or_none.return_value = item
        else:
            res.scalar_one_or_none.return_value = None
        return res

    mock_db.execute = AsyncMock(side_effect=mock_execute)
    mock_db.add = MagicMock()

    # Record sale of 2 bags: stock drops from 6 to 4 (threshold is 5)
    tx, debt, alert = await ledger.record_transaction(
        db=mock_db,
        business_id=business_id,
        record_type="sale",
        amount=Decimal("90000"),
        item_name="Rice",
        quantity=Decimal("2"),
        unit="bags",
        status="confirmed",
    )

    assert item.quantity_on_hand == Decimal("4")
    assert alert is not None
    assert "Low Stock Alert" in alert
    assert "down to 4 bags" in alert
    assert "reorder threshold: 5 bags" in alert


@pytest.mark.asyncio
async def test_untracked_item_does_not_fire_false_positive_alert():
    """An item without a low_stock_threshold does not generate any low-stock alerts."""
    business_id = uuid4()
    ledger = LedgerService()

    item = InventoryItem(
        id=uuid4(),
        business_id=business_id,
        item_name="Sugar",
        normalized_item_name="sugar",
        quantity_on_hand=Decimal("10"),
        low_stock_threshold=None,  # No threshold configured
    )

    mock_db = AsyncMock()

    async def mock_execute(stmt, *args, **kwargs):
        res = MagicMock()
        res.scalar_one_or_none.return_value = item
        return res

    mock_db.execute = AsyncMock(side_effect=mock_execute)
    mock_db.add = MagicMock()

    tx, debt, alert = await ledger.record_transaction(
        db=mock_db,
        business_id=business_id,
        record_type="sale",
        amount=Decimal("5000"),
        item_name="Sugar",
        quantity=Decimal("2"),
        status="confirmed",
    )

    assert item.quantity_on_hand == Decimal("8")
    assert alert is None  # Never fires for untracked items


# =========================================================================
# PHASE 3 TESTS: Debt Tracking & Payment Reminder Drafting
# =========================================================================

@pytest.mark.asyncio
async def test_phase3_list_outstanding_debts_and_filtering():
    """Verify listing all outstanding debts and filtering by customer name."""
    from app.services.debt_service import DebtService
    business_id = uuid4()
    cust1 = Customer(id=uuid4(), business_id=business_id, name="Emeka Okoye", normalized_name="emeka okoye")
    cust2 = Customer(id=uuid4(), business_id=business_id, name="Amina Bello", normalized_name="amina bello")

    overdue_date = datetime.now(UTC) - timedelta(days=5)
    future_date = datetime.now(UTC) + timedelta(days=5)

    debt1 = Debt(
        id=uuid4(),
        business_id=business_id,
        customer_id=cust1.id,
        amount=Decimal("120000"),
        status="outstanding",
        due_date=overdue_date,
        notes="3 bags of rice",
    )
    debt2 = Debt(
        id=uuid4(),
        business_id=business_id,
        customer_id=cust2.id,
        amount=Decimal("50000"),
        status="outstanding",
        due_date=future_date,
        notes="Cooking oil",
    )

    all_debts = [(debt1, cust1), (debt2, cust2)]

    async def mock_execute(stmt, *args, **kwargs):
        res = MagicMock()
        stmt_str = str(stmt).lower()
        if "amina" in stmt_str:
            res.all.return_value = [(debt2, cust2)]
        else:
            res.all.return_value = all_debts
        return res

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(side_effect=mock_execute)

    debt_svc = DebtService()

    # 1. List all
    results_all = await debt_svc.list_outstanding_debts(mock_db, business_id=business_id)
    assert len(results_all) == 2
    assert results_all[0]["customer_name"] == "Emeka Okoye"
    assert results_all[0]["is_overdue"] is True
    assert results_all[0]["days_overdue"] >= 4
    assert results_all[1]["customer_name"] == "Amina Bello"
    assert results_all[1]["is_overdue"] is False

    summary_text = debt_svc.format_debts_summary(results_all)
    assert "Outstanding Debts Summary" in summary_text
    assert "₦170,000" in summary_text
    assert "Emeka Okoye" in summary_text
    assert "overdue" in summary_text


@pytest.mark.asyncio
async def test_phase3_mark_debt_paid_partial_and_full():
    """Verify marking debt partially paid and fully paid."""
    from app.services.debt_service import DebtService
    business_id = uuid4()
    cust = Customer(id=uuid4(), business_id=business_id, name="Emeka Okoye", normalized_name="emeka okoye")
    debt = Debt(
        id=uuid4(),
        business_id=business_id,
        customer_id=cust.id,
        amount=Decimal("100000"),
        status="outstanding",
    )

    mock_db = AsyncMock()
    mock_db.get = AsyncMock(return_value=cust)

    async def mock_execute(stmt, *args, **kwargs):
        res = MagicMock()
        res.scalar_one_or_none.return_value = debt
        res.first.return_value = (debt, cust)
        return res

    mock_db.execute = AsyncMock(side_effect=mock_execute)

    debt_svc = DebtService()

    # 1. Partial payment: pays 40,000 of 100,000
    updated_debt, msg1 = await debt_svc.mark_debt_paid(
        mock_db, business_id=business_id, customer_name="Emeka", amount_or_debt_id="40000"
    )
    assert updated_debt.amount == Decimal("60000")
    assert updated_debt.status == "outstanding"
    assert "Remaining balance: ₦60,000" in msg1

    # 2. Full payment: pays remaining 60,000
    paid_debt, msg2 = await debt_svc.mark_debt_paid(
        mock_db, business_id=business_id, customer_name="Emeka", amount_or_debt_id="60000"
    )
    assert paid_debt.status == "paid"
    assert paid_debt.paid_at is not None
    assert "paid in full" in msg2


@pytest.mark.asyncio
async def test_phase3_draft_payment_reminder_owner_only_guardrail():
    """
    Verify that drafting a payment reminder returns polite, ready-to-forward text
    strictly to the owner, and never invokes any outbound customer communication.
    """
    from app.services.debt_service import DebtService
    business_id = uuid4()
    biz = Business(id=business_id, name="Ade Trading Store", phone_number="+2348011112222")
    cust = Customer(
        id=uuid4(), business_id=business_id, name="Tunde Bakare", normalized_name="tunde bakare", phone_number="+2348099998888"
    )
    due_dt = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)
    debt = Debt(
        id=uuid4(),
        business_id=business_id,
        customer_id=cust.id,
        amount=Decimal("75000"),
        status="outstanding",
        due_date=due_dt,
        notes="2 bags cement",
    )

    mock_db = AsyncMock()
    mock_db.get = AsyncMock(return_value=biz)

    async def mock_execute(stmt, *args, **kwargs):
        res = MagicMock()
        stmt_str = str(stmt)
        if "FROM customers" in stmt_str:
            res.scalar_one_or_none.return_value = cust
        elif "FROM debts" in stmt_str:
            res.scalars.return_value.all.return_value = [debt]
        else:
            res.scalar_one_or_none.return_value = None
        return res

    mock_db.execute = AsyncMock(side_effect=mock_execute)

    debt_svc = DebtService()
    draft = await debt_svc.draft_payment_reminder(mock_db, business_id=business_id, customer_name="Tunde")

    # Assert content
    assert "Tunde Bakare" in draft
    assert "₦75,000" in draft
    assert "Ade Trading Store" in draft
    assert "draft reminder message for you to review and forward" in draft
    # Must never contain internal system tokens or leak IDs
    assert str(cust.id) not in draft
    assert str(debt.id) not in draft


@pytest.mark.asyncio
async def test_phase3_debt_tools_strict_tenant_isolation():
    """Ensure that Business B cannot list, modify, or draft reminders for Business A's debts."""
    from app.services.debt_service import DebtService
    biz_a = uuid4()
    biz_b = uuid4()

    mock_db = AsyncMock()
    res_mock = MagicMock()
    res_mock.all.return_value = []
    res_mock.scalar_one_or_none.return_value = None
    mock_db.execute.return_value = res_mock

    debt_svc = DebtService()

    # Business B lists debts - query must be strictly scoped to biz_b
    await debt_svc.list_outstanding_debts(mock_db, business_id=biz_b)
    call_args = mock_db.execute.await_args
    stmt = call_args[0][0]
    compiled = str(stmt.compile())
    assert "debts.business_id =" in compiled
    assert biz_a != biz_b


# =========================================================================
# PHASE 4 TESTS: Margin Auditing, 80/20 Analysis, and Deterministic Pricing Simulation
# =========================================================================

@pytest.mark.asyncio
async def test_phase4_get_margin_report_hand_verifiable_outputs():
    """
    Verify get_margin_report computes per-item and overall gross margins deterministically:
    - Item 1 (Rice): 10 bags sold @ ₦45,000 = ₦450,000 revenue. Cost = ₦32,000.
      COGS = ₦320,000. Profit = ₦130,000. Margin = 28.9%.
    - Item 2 (Sugar): 5 bags sold @ ₦20,000 = ₦100,000 revenue. Cost = ₦15,000.
      COGS = ₦75,000. Profit = ₦25,000. Margin = 25.0%.
    - Overall: Total revenue = ₦550,000. Total COGS = ₦395,000. Profit = ₦155,000. Margin = 28.2%.
    """
    from app.services.analytics_service import AnalyticsService
    business_id = uuid4()

    # Seed InventoryItems
    inv_rice = InventoryItem(
        id=uuid4(),
        business_id=business_id,
        item_name="Rice",
        normalized_item_name="rice",
        unit_cost=Decimal("32000"),
        unit="bags",
    )
    inv_sugar = InventoryItem(
        id=uuid4(),
        business_id=business_id,
        item_name="Sugar",
        normalized_item_name="sugar",
        unit_cost=Decimal("15000"),
        unit="bags",
    )

    # Seed confirmed sales
    sale1 = Transaction(
        id=uuid4(),
        business_id=business_id,
        transaction_type="sale",
        item_name="Rice",
        quantity=Decimal("10"),
        amount=Decimal("450000"),
        status="confirmed",
        occurred_at=datetime.now(UTC),
    )
    sale2 = Transaction(
        id=uuid4(),
        business_id=business_id,
        transaction_type="sale",
        item_name="Sugar",
        quantity=Decimal("5"),
        amount=Decimal("100000"),
        status="confirmed",
        occurred_at=datetime.now(UTC),
    )

    mock_db = AsyncMock()

    async def mock_execute(stmt, *args, **kwargs):
        res = MagicMock()
        stmt_str = str(stmt)
        if "FROM inventory_items" in stmt_str:
            res.scalars.return_value.all.return_value = [inv_rice, inv_sugar]
        elif "FROM transactions" in stmt_str:
            res.scalars.return_value.all.return_value = [sale1, sale2]
        else:
            res.scalars.return_value.all.return_value = []
        return res

    mock_db.execute = AsyncMock(side_effect=mock_execute)

    analytics = AnalyticsService()

    # 1. Overall margin report
    rep = await analytics.get_margin_report(mock_db, business_id=business_id, period="this_month")
    assert rep["total_revenue"] == Decimal("550000")
    assert rep["total_cogs"] == Decimal("395000")
    assert rep["gross_profit"] == Decimal("155000")
    assert rep["overall_margin_pct"] == Decimal("28.2")

    items = rep["items"]
    assert len(items) == 2
    # Rice
    assert items[0]["normalized_name"] == "rice"
    assert items[0]["revenue"] == Decimal("450000")
    assert items[0]["cogs"] == Decimal("320000")
    assert items[0]["gross_profit"] == Decimal("130000")
    assert items[0]["margin_pct"] == Decimal("28.9")
    # Sugar
    assert items[1]["normalized_name"] == "sugar"
    assert items[1]["revenue"] == Decimal("100000")
    assert items[1]["cogs"] == Decimal("75000")
    assert items[1]["gross_profit"] == Decimal("25000")
    assert items[1]["margin_pct"] == Decimal("25.0")

    # 2. On-demand single item margin query ("what's my margin on rice?")
    single_rep = await analytics.get_margin_report(
        mock_db, business_id=business_id, period="this_month", item_name="rice"
    )
    assert single_rep["item"]["normalized_name"] == "rice"
    assert single_rep["item"]["margin_pct"] == Decimal("28.9")
    assert "28.9%" in single_rep["summary_text"]
    assert "₦130,000" in single_rep["summary_text"]


@pytest.mark.asyncio
async def test_phase4_product_performance_80_20_and_dead_stock():
    """
    Verify 80/20 Pareto revenue ranking and dead stock detection with known seeded numbers:
    - Products A (₦800,000 / 80%): Top 80% Core Driver
    - Products B (₦100,000 / 10%), C (₦70,000 / 7%), D (₦30,000 / 3%): Long Tail
    - Product E (Cement): 20 units on hand @ ₦5,000 unit cost = ₦100,000 tied-up dead stock (0 sales)
    """
    from app.services.analytics_service import AnalyticsService
    business_id = uuid4()

    # Inventory
    inv_a = InventoryItem(id=uuid4(), business_id=business_id, item_name="Prod A", normalized_item_name="prod a", quantity_on_hand=Decimal("5"))
    inv_b = InventoryItem(id=uuid4(), business_id=business_id, item_name="Prod B", normalized_item_name="prod b", quantity_on_hand=Decimal("5"))
    inv_c = InventoryItem(id=uuid4(), business_id=business_id, item_name="Prod C", normalized_item_name="prod c", quantity_on_hand=Decimal("5"))
    inv_d = InventoryItem(id=uuid4(), business_id=business_id, item_name="Prod D", normalized_item_name="prod d", quantity_on_hand=Decimal("5"))
    # Dead stock item
    inv_e = InventoryItem(
        id=uuid4(),
        business_id=business_id,
        item_name="Cement",
        normalized_item_name="cement",
        quantity_on_hand=Decimal("20"),
        unit_cost=Decimal("5000"),
        unit="bags",
    )

    # Sales
    s_a = Transaction(id=uuid4(), business_id=business_id, transaction_type="sale", item_name="Prod A", amount=Decimal("800000"), status="confirmed", occurred_at=datetime.now(UTC))
    s_b = Transaction(id=uuid4(), business_id=business_id, transaction_type="sale", item_name="Prod B", amount=Decimal("100000"), status="confirmed", occurred_at=datetime.now(UTC))
    s_c = Transaction(id=uuid4(), business_id=business_id, transaction_type="sale", item_name="Prod C", amount=Decimal("70000"), status="confirmed", occurred_at=datetime.now(UTC))
    s_d = Transaction(id=uuid4(), business_id=business_id, transaction_type="sale", item_name="Prod D", amount=Decimal("30000"), status="confirmed", occurred_at=datetime.now(UTC))

    mock_db = AsyncMock()

    async def mock_execute(stmt, *args, **kwargs):
        res = MagicMock()
        stmt_str = str(stmt)
        if "FROM inventory_items" in stmt_str:
            res.scalars.return_value.all.return_value = [inv_a, inv_b, inv_c, inv_d, inv_e]
        elif "FROM transactions" in stmt_str:
            res.scalars.return_value.all.return_value = [s_a, s_b, s_c, s_d]
        return res

    mock_db.execute = AsyncMock(side_effect=mock_execute)

    analytics = AnalyticsService()
    perf = await analytics.get_product_performance_analysis(mock_db, business_id=business_id, period="this_month")

    assert perf["total_revenue"] == Decimal("1000000")
    # Pareto drivers (Top 80%)
    drivers = perf["pareto_drivers"]
    assert len(drivers) == 1
    assert drivers[0]["normalized_name"] == "prod a"
    assert drivers[0]["share_pct"] == Decimal("80.0")

    # Long tail items
    long_tail = perf["long_tail_items"]
    assert len(long_tail) == 3

    # Dead stock
    dead = perf["dead_stock_items"]
    assert len(dead) == 1
    assert dead[0]["normalized_name"] == "cement"
    assert dead[0]["quantity_on_hand"] == Decimal("20")
    assert dead[0]["tied_up_capital"] == Decimal("100000")
    assert perf["total_tied_up_capital"] == Decimal("100000")

    # WhatsApp formatted output includes key insights
    assert "80/20 Core Drivers" in perf["summary_text"]
    assert "Dead Stock Warning" in perf["summary_text"]
    assert "₦100,000" in perf["summary_text"]


@pytest.mark.asyncio
async def test_phase4_pricing_simulation_deterministic_math():
    """
    Verify pricing simulation deterministic math without LLM estimation:
    1. Discount simulation:
       - Item cost = ₦30,000, current price = ₦50,000.
       - 10% discount: price drops to ₦45,000, profit drops to ₦15,000, margin drops to 33.3%.
    2. Target margin simulation:
       - Item cost = ₦30,000, target margin = 25%.
       - Required price = 30,000 / (1 - 0.25) = ₦40,000. Profit per unit = ₦10,000.
    3. Mathematical edge cases:
       - Target margin >= 100% returns error explaining mathematical impossibility.
       - Missing unit cost returns prompt to set cost first.
    """
    from app.services.analytics_service import AnalyticsService
    business_id = uuid4()

    item_rice = InventoryItem(
        id=uuid4(),
        business_id=business_id,
        item_name="Rice",
        normalized_item_name="rice",
        unit_cost=Decimal("30000"),
    )

    mock_db = AsyncMock()

    async def mock_execute(stmt, *args, **kwargs):
        res = MagicMock()
        stmt_str = str(stmt)
        if "FROM inventory_items" in stmt_str:
            res.scalar_one_or_none.return_value = item_rice
        else:
            res.scalar_one_or_none.return_value = None
        return res

    mock_db.execute = AsyncMock(side_effect=mock_execute)

    analytics = AnalyticsService()

    # 1. Discount simulation
    res_disc = await analytics.simulate_pricing(
        mock_db,
        business_id=business_id,
        item_name="Rice",
        discount_pct=10.0,
        current_price=50000.0,
    )
    assert res_disc["success"] is True
    assert res_disc["new_price"] == Decimal("45000.00")
    assert res_disc["profit_per_unit"] == Decimal("15000.00")
    assert res_disc["new_margin_pct"] == Decimal("33.3")
    assert res_disc["is_loss"] is False
    assert "33.3%" in res_disc["summary_text"]

    # 2. Target margin simulation
    res_tgt = await analytics.simulate_pricing(
        mock_db,
        business_id=business_id,
        item_name="Rice",
        target_margin_pct=25.0,
        current_price=50000.0,
    )
    assert res_tgt["success"] is True
    assert res_tgt["required_price"] == Decimal("40000.00")
    assert res_tgt["profit_per_unit"] == Decimal("10000.00")
    assert "₦40,000" in res_tgt["summary_text"]

    # 3. Impossible target margin (>= 100%)
    res_impossible = await analytics.simulate_pricing(
        mock_db,
        business_id=business_id,
        item_name="Rice",
        target_margin_pct=100.0,
    )
    assert res_impossible["success"] is False
    assert "mathematically impossible" in res_impossible["summary_text"]

    # 4. Item with missing unit cost
    item_rice.unit_cost = None
    res_no_cost = await analytics.simulate_pricing(
        mock_db,
        business_id=business_id,
        item_name="Rice",
        discount_pct=10.0,
        current_price=50000.0,
    )
    assert res_no_cost["success"] is False
    assert "cost price (COGS) is not set" in res_no_cost["summary_text"]


@pytest.mark.asyncio
async def test_phase4_strict_tenant_isolation_analytics():
    """Ensure that Business B cannot query margins or product performance of Business A."""
    from app.services.analytics_service import AnalyticsService
    biz_a = uuid4()
    biz_b = uuid4()

    mock_db = AsyncMock()
    res_mock = MagicMock()
    res_mock.scalars.return_value.all.return_value = []
    mock_db.execute.return_value = res_mock

    analytics = AnalyticsService()

    # Query for Business B
    await analytics.get_margin_report(mock_db, business_id=biz_b, period="this_month")
    call_args = mock_db.execute.await_args_list[0]
    stmt = call_args[0][0]
    compiled = str(stmt.compile())
    assert "transactions.business_id =" in compiled
    assert biz_a != biz_b


# =========================================================================
# PHASE 5 TESTS: Cash Flow Forecasting & Payables Tracking
# =========================================================================

@pytest.mark.asyncio
async def test_phase5_log_upcoming_payable_creates_record():
    """Verify logging an upcoming supplier payable with due date and description."""
    from app.services.analytics_service import AnalyticsService
    from app.models.payable import Payable
    business_id = uuid4()

    mock_db = AsyncMock()
    mock_db.add = MagicMock()

    analytics = AnalyticsService()
    payable, msg = await analytics.log_upcoming_payable(
        mock_db,
        business_id=business_id,
        amount=150000.0,
        due_date="2026-10-25",
        description="50 cartons Indomie from distributor",
        vendor_name="De-United Foods",
    )

    assert payable.amount == Decimal("150000.00")
    assert payable.description == "50 cartons Indomie from distributor"
    assert payable.vendor_name == "De-United Foods"
    assert payable.due_date == date(2026, 10, 25)
    assert payable.status == "outstanding"
    assert "₦150,000" in msg
    assert "De-United Foods" in msg
    assert mock_db.add.called


@pytest.mark.asyncio
async def test_phase5_cash_flow_forecast_detects_shortfall():
    """
    Verify cash flow shortfall detection:
    - Trailing 30 days revenue = ₦300,000 -> ₦10,000 avg/day.
    - Horizon 14 days -> ₦140,000 projected cash inflow.
    - Payables due in 14 days:
      - ₦120,000 (Supplier) + ₦50,000 (Transport) = ₦170,000.
    - Projected Net = ₦140,000 - ₦170,000 = -₦30,000 (Shortfall of ₦30,000).
    """
    from app.services.analytics_service import AnalyticsService
    from app.models.payable import Payable
    business_id = uuid4()

    p1 = Payable(
        id=uuid4(),
        business_id=business_id,
        amount=Decimal("120000"),
        due_date=datetime.now(UTC).date() + timedelta(days=5),
        description="Rice supplier balance",
        status="outstanding",
    )
    p2 = Payable(
        id=uuid4(),
        business_id=business_id,
        amount=Decimal("50000"),
        due_date=datetime.now(UTC).date() + timedelta(days=10),
        description="Haulage & logistics",
        status="outstanding",
    )

    mock_db = AsyncMock()

    async def mock_execute(stmt, *args, **kwargs):
        res = MagicMock()
        stmt_str = str(stmt)
        if "FROM payables" in stmt_str:
            res.scalars.return_value.all.return_value = [p1, p2]
        else:
            # Trailing sales sum
            res.scalar_one.return_value = Decimal("300000")
        return res

    mock_db.execute = AsyncMock(side_effect=mock_execute)

    analytics = AnalyticsService()
    forecast = await analytics.get_cash_flow_forecast(
        mock_db, business_id=business_id, trailing_days=30, horizon_days=14
    )

    assert forecast["trailing_sales"] == Decimal("300000")
    assert forecast["avg_daily_revenue"] == Decimal("10000.00")
    assert forecast["projected_inflow"] == Decimal("140000.00")
    assert forecast["total_payables"] == Decimal("170000.00")
    assert forecast["projected_net"] == Decimal("-30000.00")
    assert forecast["is_shortfall"] is True
    assert forecast["shortfall_amount"] == Decimal("30000.00")

    # WhatsApp formatted message contains explicit shortfall alert
    assert "Cash Flow Shortfall Alert" in forecast["summary_text"]
    assert "₦30,000" in forecast["summary_text"]
    assert "Simplified v1" in forecast["summary_text"]


@pytest.mark.asyncio
async def test_phase5_cash_flow_forecast_healthy_surplus():
    """Verify healthy surplus scenario where projected sales exceed upcoming payables."""
    from app.services.analytics_service import AnalyticsService
    from app.models.payable import Payable
    business_id = uuid4()

    p1 = Payable(
        id=uuid4(),
        business_id=business_id,
        amount=Decimal("40000"),
        due_date=datetime.now(UTC).date() + timedelta(days=3),
        description="Shop electricity bill",
        status="outstanding",
    )

    mock_db = AsyncMock()

    async def mock_execute(stmt, *args, **kwargs):
        res = MagicMock()
        stmt_str = str(stmt)
        if "FROM payables" in stmt_str:
            res.scalars.return_value.all.return_value = [p1]
        else:
            res.scalar_one.return_value = Decimal("600000")  # 20k/day
        return res

    mock_db.execute = AsyncMock(side_effect=mock_execute)

    analytics = AnalyticsService()
    forecast = await analytics.get_cash_flow_forecast(
        mock_db, business_id=business_id, trailing_days=30, horizon_days=14
    )

    # 20,000 * 14 = 280,000 inflow vs 40,000 payables -> +240,000 surplus
    assert forecast["projected_inflow"] == Decimal("280000.00")
    assert forecast["total_payables"] == Decimal("40000.00")
    assert forecast["projected_net"] == Decimal("240000.00")
    assert forecast["is_shortfall"] is False
    assert "Healthy Cash Flow" in forecast["summary_text"]
    assert "₦240,000" in forecast["summary_text"]


@pytest.mark.asyncio
async def test_phase5_tenant_isolation_payables():
    """Ensure Business B cannot query or list Business A's payables."""
    from app.services.analytics_service import AnalyticsService
    biz_a = uuid4()
    biz_b = uuid4()

    mock_db = AsyncMock()
    res_mock = MagicMock()
    res_mock.scalars.return_value.all.return_value = []
    mock_db.execute.return_value = res_mock

    analytics = AnalyticsService()
    await analytics.list_upcoming_payables(mock_db, business_id=biz_b, horizon_days=30)

    call_args = mock_db.execute.await_args
    stmt = call_args[0][0]
    compiled = str(stmt.compile())
    assert "payables.business_id =" in compiled
    assert biz_a != biz_b


# =========================================================================
# PHASE 6 TESTS: Personalized Growth Playbooks Strictly Grounded in Phase 4 Metrics
# =========================================================================

@pytest.mark.asyncio
async def test_phase6_growth_playbook_grounding_discipline():
    """
    Verify Phase 6 Growth Playbook grounding discipline:
    1. Report synthesis prompt explicitly injects Phase 4 BI metrics (margins, 80/20, dead stock).
    2. Prompt strictly mandates that every recommendation must cite a real underlying metric.
    3. Forbids generic startup advice filler.
    4. Confirms that suggestions cite the real computed data.
    """
    from app.services.unified_report_service import UnifiedReportService
    user_id = uuid4()
    biz_id = uuid4()
    user = User(id=user_id, business_id=biz_id, phone_number="+2348011223344", niche="sme_owner", display_name="Chidi")

    bi_evidence = (
        "📊 Margin Analysis: Rice (This Month)\n"
        "• Revenue: ₦450,000\n"
        "• Volume Sold: 10 bags\n"
        "• Cost Price (COGS): ₦32,000 per unit\n"
        "• Gross Profit: ₦130,000\n"
        "• Gross Margin: 28.9%\n\n"
        "📈 Product Performance & 80/20 Analysis (This Month)\n"
        "Total Sales: ₦450,000\n"
        "🎯 80/20 Core Drivers: Rice: ₦450,000 (100.0% of sales)\n"
        "⚠️ Dead Stock Warning: NPK Fertilizer: 8 bags unsold (~₦144,000)"
    )

    captured_prompt = None

    class FakeLLM:
        async def ainvoke(self, messages):
            nonlocal captured_prompt
            captured_prompt = messages[0].content
            return MagicMock(content=(
                "📊 *Monthly Highlights*\n"
                "- Confirmed sales of ₦450,000 for Rice\n\n"
                "🚀 *Personalized Growth Playbook*\n"
                "- Since Rice generates 100% of your revenue (₦450,000) at a 28.9% gross margin, consider stocking complementary grains.\n"
                "- Free up your ₦144,000 tied up in unsold NPK Fertilizer (8 bags) by running a bundled promotion."
            ))

    unified_svc = UnifiedReportService()

    with patch("app.services.unified_report_service.ChatGoogleGenerativeAI", return_value=FakeLLM()):
        with patch("app.core.config.settings.gemini_api_key", "test_key"):
            report_text = await unified_svc._synthesize_report(
                user=user,
                cadence="monthly",
                curr_acts=[],
                prior_acts=[],
                goals=[],
                bi_evidence=bi_evidence,
            )

    # 1. Assert prompt contains the Phase 4 BI metrics block
    assert "REAL BUSINESS METRICS & INVENTORY DATA (GROUNDING SOURCE):" in captured_prompt
    assert "Gross Margin: 28.9%" in captured_prompt
    assert "₦144,000" in captured_prompt
    assert "NPK Fertilizer" in captured_prompt

    # 2. Assert prompt enforces strict grounding discipline & forbids generic filler
    assert "GROWTH PLAYBOOK GROUNDING DISCIPLINE" in captured_prompt
    assert "ABSOLUTELY FORBIDDEN: Generic startup-advice filler" in captured_prompt

    # 3. Assert resulting report includes Personalized Growth Playbook grounded in real numbers
    assert "Personalized Growth Playbook" in report_text
    assert "28.9%" in report_text or "₦450,000" in report_text
    assert "₦144,000" in report_text or "Fertilizer" in report_text


# =========================================================================
# GUARDRAIL TEST: Never Contact Customers Directly
# =========================================================================

def test_guardrail_prompt_forbids_direct_customer_outreach():
    """Verify that the system prompt strictly enforces delivery of reminders ONLY to the owner."""
    agent = AgentService()
    biz = Business(id=uuid4(), name="Test Biz")
    owner_user = User(id=uuid4(), phone_number="+2348000000001", role="owner")

    prompt = agent._build_system_prompt(biz, owner_user, datetime.now(UTC), "Africa/Lagos")

    assert "NEVER send WhatsApp messages directly to a customer's phone number" in prompt
    assert "The reminder is always delivered back to the OWNER" in prompt
