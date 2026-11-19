from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from app.core.database import async_session_factory
from app.models.business import Business
from app.models.risk_assessment import TransactionRiskAssessment
from app.models.transaction import Transaction
from app.services.credit_score_service import CreditScoreService


@pytest.mark.asyncio
async def test_credit_score_v2_quarantine_integrity():
    service = CreditScoreService()

    async with async_session_factory() as db_session:
        biz_id = uuid4()
        biz = Business(
            id=biz_id,
            name="Score Integrity Test Store",
            phone_number="2348111111222",
            business_type="retail",
        )
        db_session.add(biz)
        await db_session.flush()

        # 1. Clean sales: 5 transactions of ₦50,000 each = ₦250,000
        for i in range(5):
            clean_tx = Transaction(
                id=uuid4(),
                business_id=biz_id,
                transaction_type="sale",
                item_name="Detergent Carton",
                quantity=Decimal("5"),
                unit_price=Decimal("10000"),
                amount=Decimal("50000"),
                status="confirmed",
                occurred_at=datetime.now(UTC) - timedelta(days=i + 1),
            )
            db_session.add(clean_tx)

        # 2. Operating restock expense: ₦150,000
        exp_tx = Transaction(
            id=uuid4(),
            business_id=biz_id,
            transaction_type="expense",
            item_name="Stock purchase",
            amount=Decimal("150000"),
            status="confirmed",
            occurred_at=datetime.now(UTC) - timedelta(days=6),
        )
        db_session.add(exp_tx)
        await db_session.flush()

        score_clean = await service.calculate_score(db_session, biz_id)
        assert score_clean["sales_30d"] == 250000.0
        assert score_clean["verified_sales_30d"] == 250000.0
        assert score_clean["auditability_ratio"] == 100.0
        assert score_clean["quarantined_transactions_count"] == 0

        # 3. Fabricated / Orphan Sale: ₦10,000,000 cash sale with risk assessment is_credit_eligible = False
        fake_tx = Transaction(
            id=uuid4(),
            business_id=biz_id,
            transaction_type="sale",
            item_name="Phantom Gold Bars",
            amount=Decimal("10000000"),
            status="confirmed",
            occurred_at=datetime.now(UTC) - timedelta(days=1),
        )
        db_session.add(fake_tx)
        await db_session.flush()

        risk = TransactionRiskAssessment(
            transaction_id=fake_tx.id,
            business_id=biz_id,
            risk_score=0.95,
            risk_tier="tier_2_shadow_quarantined",
            anomaly_flags=["ORPHAN_CASH_SALE_NO_RESTOCK", "STATISTICAL_VELOCITY_SPIKE"],
            is_credit_eligible=False,
            quarantine_reason="Unanchored synthetic transaction",
        )
        db_session.add(risk)
        await db_session.flush()

        # Score recalculation
        score_after_fraud = await service.calculate_score(db_session, biz_id)
        # Raw sales reflects ₦10.25M
        assert score_after_fraud["sales_30d"] == 10250000.0
        # But verified sales stays strictly at ₦250,000!
        assert score_after_fraud["verified_sales_30d"] == 250000.0
        assert score_after_fraud["quarantined_transactions_count"] == 1
        # Auditability ratio drops to (5 clean / 6 total) ~ 83.3%
        assert score_after_fraud["auditability_ratio"] < 100.0

        await db_session.rollback()
