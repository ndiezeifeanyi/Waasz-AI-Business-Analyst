import pytest
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from app.models.business import Business
from app.models.customer import Customer
from app.models.debt import Debt
from app.services.debt_service import DebtService


@pytest.mark.asyncio
async def test_draft_payment_reminder_with_phone_number():
    debt_svc = DebtService()
    biz_id = uuid4()
    cust_id = uuid4()

    mock_db = AsyncMock()
    mock_biz = Business(id=biz_id, name="Ace Enterprises")
    mock_customer = Customer(
        id=cust_id,
        business_id=biz_id,
        name="Chidi Nwosu",
        normalized_name="chidi nwosu",
        phone_number="08031234567",
    )
    mock_debt = Debt(
        id=uuid4(),
        business_id=biz_id,
        customer_id=cust_id,
        amount=Decimal("45000"),
        status="outstanding",
        notes="2 bags of rice",
    )

    mock_db.get = AsyncMock(return_value=mock_biz)

    # First execute for customer lookup, second for debts
    cust_res = MagicMock()
    cust_res.scalar_one_or_none.return_value = mock_customer

    debt_res = MagicMock()
    debt_res.scalars.return_value.all.return_value = [mock_debt]

    mock_db.execute = AsyncMock(side_effect=[cust_res, debt_res])

    draft = await debt_svc.draft_payment_reminder(mock_db, biz_id, "Chidi")

    assert "Chidi Nwosu" in draft
    assert "45,000" in draft
    assert "Ace Enterprises" in draft
    # Check 1-tap WhatsApp deep link
    assert "https://wa.me/2348031234567?text=" in draft
    assert "📲 *1-Tap WhatsApp Forward Link:*" in draft


@pytest.mark.asyncio
async def test_draft_payment_reminder_without_phone_number():
    debt_svc = DebtService()
    biz_id = uuid4()
    cust_id = uuid4()

    mock_db = AsyncMock()
    mock_biz = Business(id=biz_id, name="Ace Enterprises")
    mock_customer = Customer(
        id=cust_id,
        business_id=biz_id,
        name="Amaka Obi",
        normalized_name="amaka obi",
        phone_number=None,
    )
    mock_debt = Debt(
        id=uuid4(),
        business_id=biz_id,
        customer_id=cust_id,
        amount=Decimal("15000"),
        status="outstanding",
        notes="Carton of milk",
    )

    mock_db.get = AsyncMock(return_value=mock_biz)

    cust_res = MagicMock()
    cust_res.scalar_one_or_none.return_value = mock_customer

    debt_res = MagicMock()
    debt_res.scalars.return_value.all.return_value = [mock_debt]

    mock_db.execute = AsyncMock(side_effect=[cust_res, debt_res])

    draft = await debt_svc.draft_payment_reminder(mock_db, biz_id, "Amaka")

    assert "Amaka Obi" in draft
    assert "15,000" in draft
    # Check fallback WhatsApp link allowing user to pick contact
    assert "https://wa.me/?text=" in draft
