import pytest
from decimal import Decimal
from uuid import uuid4

from app.core.database import async_session_factory
from app.models.business import Business
from app.models.transaction import Transaction
from app.models.cash_reconciliation import CashRestockReconciliation
from app.models.risk_assessment import TransactionRiskAssessment
from app.services.risk.cash_reconciliation_service import CashReconciliationEngine

@pytest.mark.asyncio
async def test_cash_reconciliation_flow():
    engine = CashReconciliationEngine()

    async with async_session_factory() as db_session:
        # 1. Setup a test business
        biz_id = uuid4()
        biz = Business(
            id=biz_id,
            name="Test Provisions Store",
            phone_number="2348000000999",
            business_type="retail",
        )
        db_session.add(biz)
        await db_session.flush()

        # 2. Record a physical restock expense: 50 bags of rice @ ₦70,000 each = ₦3,500,000
        restock_tx = Transaction(
            id=uuid4(),
            business_id=biz_id,
            transaction_type="expense",
            item_name="Rice 50kg",
            quantity=Decimal("50"),
            unit_price=Decimal("70000"),
            amount=Decimal("3500000"),
            status="confirmed",
        )
        db_session.add(restock_tx)
        await db_session.flush()

        batch = await engine.record_restock_expense(db_session, biz_id, restock_tx)
        assert batch is not None
        assert batch.quantity_acquired == Decimal("50")
        assert batch.quantity_sold_cash == Decimal("0")
        assert batch.unit_cost == Decimal("70000")

        # 3. Legitimate sale: 3 bags of rice @ ₦78,000 each = ₦234,000 cash
        sale_tx = Transaction(
            id=uuid4(),
            business_id=biz_id,
            transaction_type="sale",
            item_name="Rice 50kg",
            quantity=Decimal("3"),
            unit_price=Decimal("78000"),
            amount=Decimal("234000"),
            status="confirmed",
            is_credit=False,
        )
        db_session.add(sale_tx)
        await db_session.flush()

        assessment = await engine.evaluate_cash_sale(db_session, biz_id, sale_tx)
        assert assessment.risk_tier == "tier_0_clean"
        assert assessment.is_credit_eligible is True
        assert len(assessment.anomaly_flags) == 0

        # Verify that the restock batch deducted 3 bags
        await db_session.refresh(batch)
        assert batch.quantity_sold_cash == Decimal("3")
        assert batch.total_cash_revenue_realized == Decimal("234000")

        # 4. Anomalous / Orphan sale: ₦5,000,000 cash sale of an item with NO restock history
        phantom_tx = Transaction(
            id=uuid4(),
            business_id=biz_id,
            transaction_type="sale",
            item_name="Imported Sugar Bags",
            quantity=Decimal("100"),
            unit_price=Decimal("50000"),
            amount=Decimal("5000000"),
            status="confirmed",
            is_credit=False,
        )
        db_session.add(phantom_tx)
        await db_session.flush()

        phantom_assessment = await engine.evaluate_cash_sale(db_session, biz_id, phantom_tx)
        assert phantom_assessment.risk_tier == "tier_1_step_up"
        assert phantom_assessment.is_credit_eligible is False
        assert "ORPHAN_CASH_SALE_NO_RESTOCK" in phantom_assessment.anomaly_flags
        assert "STATISTICAL_VELOCITY_SPIKE" in phantom_assessment.anomaly_flags
        assert phantom_assessment.step_up_triggered is True

        # Rollback test data to keep database clean
        await db_session.rollback()
