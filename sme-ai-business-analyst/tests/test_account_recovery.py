from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select

from app.core.auth import TokenData, create_access_token
from app.core.config import settings
from app.core.database import async_session_factory
from app.main import app
from app.models.activity import Activity
from app.models.approved_tester import ApprovedTester
from app.models.business import Business
from app.models.transaction import Transaction
from app.models.user import User
from app.services.account_recovery_service import AccountRecoveryService
from app.services.ledger_service import LedgerService
from app.utils.phone import normalize_phone


@pytest.fixture(autouse=True)
async def cleanup_recovery_test_records():
    from app.core.database import engine
    await engine.dispose()

    old_phone = normalize_phone("+2348000000771")
    new_phone = normalize_phone("+2348000000772")
    phones = [old_phone, new_phone]

    yield

    async with async_session_factory() as session:
        await session.execute(delete(ApprovedTester).where(ApprovedTester.phone_number.in_(phones)))
        b_res = await session.execute(select(Business).where(Business.phone_number.in_(phones)))
        for b in b_res.scalars().all():
            await session.execute(delete(Activity).where(Activity.business_id == b.id))
            await session.execute(delete(Transaction).where(Transaction.business_id == b.id))
            await session.execute(delete(User).where(User.business_id == b.id))
            await session.execute(delete(Business).where(Business.id == b.id))
        await session.execute(delete(User).where(User.phone_number.in_(phones)))
        await session.commit()
    await engine.dispose()


@pytest.mark.asyncio
async def test_account_recovery_service_relinks_phone_and_preserves_ledger():
    """
    Test AccountRecoveryService:
    1. Reclaims account under new phone number
    2. Preserves all historical transactions
    3. Guarantees subsequent lookups by new phone number retrieve original business and transactions
    """
    old_phone = "+2348000000771"
    new_phone = "+2348000000772"
    norm_new = normalize_phone(new_phone)

    ledger = LedgerService()
    recovery = AccountRecoveryService()

    # Step 1: Create initial account and seed historical transactions
    async with async_session_factory() as session:
        business, user = await ledger.get_or_create_business_and_user(session, old_phone)
        original_business_id = business.id
        original_user_id = user.id

        t1 = Transaction(
            business_id=original_business_id,
            transaction_type="sale",
            amount=Decimal("45000.00"),
            currency="NGN",
            description="Sold 3 cartons biscuits",
            status="confirmed",
            occurred_at=datetime.now(UTC),
        )
        t2 = Transaction(
            business_id=original_business_id,
            transaction_type="expense",
            amount=Decimal("12000.00"),
            currency="NGN",
            description="Generator fuel",
            status="confirmed",
            occurred_at=datetime.now(UTC),
        )
        session.add_all([t1, t2])
        await session.commit()

    # Step 2: Execute account recovery
    async with async_session_factory() as session:
        res = await recovery.relink_phone_number(
            db=session,
            business_id=original_business_id,
            new_phone_number=new_phone,
            verification_method="CAC Registration Document",
            notes="Owner presented CAC cert and verified recent transactions",
            operator="superadmin",
        )
        assert res["status"] == "success"
        assert res["new_phone"] == norm_new
        assert res["transactions_preserved"] == 2

    # Step 3: Verify with LedgerService that inbound message from NEW phone number finds the SAME business
    async with async_session_factory() as session:
        found_b, found_u = await ledger.get_or_create_business_and_user(session, new_phone)
        assert found_b.id == original_business_id
        assert found_u.id == original_user_id
        assert found_b.phone_number == norm_new
        assert found_u.phone_number == norm_new

        # Verify all transactions are still present under this business
        txs = (await session.execute(
            select(Transaction).where(Transaction.business_id == found_b.id)
        )).scalars().all()
        assert len(txs) == 2
        descriptions = {t.description for t in txs}
        assert "Sold 3 cartons biscuits" in descriptions
        assert "Generator fuel" in descriptions

        # Verify audit activity log was created
        act = (await session.execute(
            select(Activity).where(
                Activity.business_id == found_b.id,
                Activity.activity_type == "account_recovery"
            )
        )).scalar_one_or_none()
        assert act is not None
        assert act.details.get("verification_method") == "CAC Registration Document"


@pytest.mark.asyncio
async def test_account_recovery_admin_rest_endpoint():
    """
    Test POST /api/v1/admin/recover-account endpoint:
    Guarantees admin can relink phone number via REST API.
    """
    old_phone = "+2348000000771"
    new_phone = "+2348000000772"
    ledger = LedgerService()

    async with async_session_factory() as session:
        business, user = await ledger.get_or_create_business_and_user(session, old_phone)
        b_id = business.id
        t = Transaction(
            business_id=b_id,
            transaction_type="sale",
            amount=Decimal("25000.00"),
            currency="NGN",
            description="REST endpoint test transaction",
            status="confirmed",
        )
        session.add(t)
        await session.commit()

    token = create_access_token(TokenData(sub="admin_tester", role="admin"))
    headers = {"Authorization": f"Bearer {token}"}

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/admin/recover-account",
            json={
                "business_id": str(b_id),
                "new_phone_number": new_phone,
                "verification_method": "Admin Verified (Call)",
                "notes": "Verified via WhatsApp video call",
            },
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["status"] == "success"
        assert data["new_phone"] == normalize_phone(new_phone)
        assert data["transactions_preserved"] >= 1
