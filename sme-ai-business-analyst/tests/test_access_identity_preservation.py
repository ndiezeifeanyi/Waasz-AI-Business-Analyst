from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, patch
from uuid import uuid4
import pytest
from sqlalchemy import delete, select

from app.core.config import settings
from app.core.database import async_session_factory
from app.models.approved_tester import ApprovedTester
from app.models.business import Business
from app.models.message import WhatsAppMessage
from app.models.transaction import Transaction
from app.models.user import User
from app.schemas.whatsapp import ParsedWhatsAppMessage, WhatsAppSendResult
from app.services.access_control_service import AccessControlService
from app.services.ledger_service import LedgerService
from app.services.webhook_processor import WhatsAppWebhookProcessor
from app.utils.phone import normalize_phone


@pytest.fixture(autouse=True)
async def cleanup_test_records():
    from app.core.database import engine
    await engine.dispose()
    orig_mode = settings.access_mode
    orig_cap = settings.max_active_users

    yield

    settings.access_mode = orig_mode
    settings.max_active_users = orig_cap

    test_phone = normalize_phone("+2348000000888")
    async with async_session_factory() as session:
        await session.execute(delete(ApprovedTester).where(ApprovedTester.phone_number == test_phone))
        # Find business/user
        res = await session.execute(select(Business).where(Business.phone_number == test_phone))
        b = res.scalar_one_or_none()
        if b:
            await session.execute(delete(Transaction).where(Transaction.business_id == b.id))
            await session.execute(delete(WhatsAppMessage).where(WhatsAppMessage.business_id == b.id))
        await session.execute(delete(User).where(User.phone_number == test_phone))
        if b:
            await session.execute(delete(Business).where(Business.id == b.id))
        await session.commit()
    await engine.dispose()


@pytest.mark.asyncio
async def test_remove_access_readmit_preserves_identity_and_historical_ledger():
    """
    Regression test for duplicate identity / orphaned ledger bug:
    When an active user with historical transactions is removed from approved_testers,
    inbound messages are rejected. When re-admitted, the system MUST reuse the exact
    same business_id and user_id, preserving all historical transactions and data.
    """
    settings.access_mode = "allowlist"
    settings.max_active_users = 20

    raw_phone = "+2348000000888"
    norm_phone = normalize_phone(raw_phone)

    ledger = LedgerService()
    processor = WhatsAppWebhookProcessor()

    # Step 1: Create initial user and business with historical transactions
    async with async_session_factory() as session:
        business, user = await ledger.get_or_create_business_and_user(session, raw_phone)
        original_business_id = business.id
        original_user_id = user.id

        # Seed two historical transactions
        t1 = Transaction(
            business_id=original_business_id,
            transaction_type="sale",
            amount=Decimal("15000.00"),
            currency="NGN",
            description="Historical sale #1",
            status="confirmed",
            occurred_at=datetime.now(UTC),
        )
        t2 = Transaction(
            business_id=original_business_id,
            transaction_type="expense",
            amount=Decimal("5000.00"),
            currency="NGN",
            description="Historical supplies #2",
            status="confirmed",
            occurred_at=datetime.now(UTC),
        )
        session.add_all([t1, t2])

        # Step 2: Add to approved_testers
        tester = ApprovedTester(
            phone_number=norm_phone,
            label="Test Tester",
            added_by="admin",
        )
        session.add(tester)
        await session.commit()

    # Verify initial state: transactions exist
    async with async_session_factory() as session:
        tx_count = (await session.execute(
            select(Transaction).where(Transaction.business_id == original_business_id)
        )).scalars().all()
        assert len(tx_count) == 2

    # Step 3: Remove user from approved testers (simulating removal of access)
    async with async_session_factory() as session:
        await session.execute(delete(ApprovedTester).where(ApprovedTester.phone_number == norm_phone))
        await session.commit()

    # Step 4: Send inbound message while removed -> Gate rejects message, zero account tampering
    mock_whatsapp = AsyncMock()
    mock_whatsapp.send_text.return_value = WhatsAppSendResult(message_id="wamid.mock_blocked")

    processor_blocked = WhatsAppWebhookProcessor(whatsapp=mock_whatsapp)
    parsed = ParsedWhatsAppMessage(
        message_id=f"wamid.blocked.{uuid4().hex[:8]}",
        from_phone=raw_phone,
        message_type="text",
        body="Hello I want to record 1000",
        timestamp=datetime.now(UTC),
    )
    async with async_session_factory() as session:
        await processor_blocked.process_message(session, parsed, event=None)

    # Verify access denied message was sent
    mock_whatsapp.send_text.assert_called()

    # Verify database still has the exact same business and user, and still 2 transactions
    async with async_session_factory() as session:
        b_check, u_check = await ledger.get_or_create_business_and_user(session, raw_phone)
        assert b_check.id == original_business_id
        assert u_check.id == original_user_id
        txs = (await session.execute(
            select(Transaction).where(Transaction.business_id == original_business_id)
        )).scalars().all()
        assert len(txs) == 2

    # Step 5: Re-admit user to approved_testers
    async with async_session_factory() as session:
        tester = ApprovedTester(
            phone_number=norm_phone,
            label="Re-admitted tester",
            added_by="admin",
        )
        session.add(tester)
        await session.commit()

    # Step 6: User sends a new message after re-admittance
    mock_whatsapp_admitted = AsyncMock()
    mock_whatsapp_admitted.send_text.return_value = WhatsAppSendResult(message_id="wamid.mock_allowed")

    mock_agent = AsyncMock()
    mock_agent.process_user_message.return_value = "Welcome back! Your records are safe."
    processor_admitted = WhatsAppWebhookProcessor(whatsapp=mock_whatsapp_admitted, agent=mock_agent)

    parsed_admitted = ParsedWhatsAppMessage(
        message_id=f"wamid.allowed.{uuid4().hex[:8]}",
        from_phone=raw_phone,
        message_type="text",
        body="Show my weekly report",
        timestamp=datetime.now(UTC),
    )
    async with async_session_factory() as session:
        await processor_admitted.process_message(session, parsed_admitted, event=None)
        await session.commit()

    # Step 7: Assert business_id and user_id are UNCHANGED and historical transactions remain intact
    async with async_session_factory() as session:
        final_b, final_u = await ledger.get_or_create_business_and_user(session, raw_phone)
        assert final_b.id == original_business_id
        assert final_u.id == original_user_id

        # Assert all historical transactions are STILL attached to this business
        final_txs = (await session.execute(
            select(Transaction).where(Transaction.business_id == final_b.id)
        )).scalars().all()
        assert len(final_txs) == 2
        descriptions = {t.description for t in final_txs}
        assert "Historical sale #1" in descriptions
        assert "Historical supplies #2" in descriptions
