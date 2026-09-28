from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch
from uuid import uuid4
import asyncio
import pytest

from sqlalchemy import delete, select

from app.core.config import settings
from app.core.database import async_session_factory
from app.models.business import Business
from app.models.invite_code import InviteCode, Waitlist
from app.models.user import User
from app.schemas.whatsapp import ParsedWhatsAppMessage, WhatsAppSendResult
from app.services.invite_service import InviteCodeService
from app.services.webhook_processor import WhatsAppWebhookProcessor
from app.utils.phone import normalize_phone


@pytest.fixture(autouse=True)
async def cleanup_test_records():
    from app.core.database import engine
    await engine.dispose()
    orig_req = settings.require_invite_code
    orig_mode = settings.access_mode
    settings.require_invite_code = True
    settings.access_mode = "invite_code"
    try:
        yield
    finally:
        settings.require_invite_code = orig_req
        settings.access_mode = orig_mode
    # Clean up test numbers and invite codes created during tests
    async with async_session_factory() as session:
        test_phones = (
            [normalize_phone(f"+23480000000{i}") for i in range(1, 15)]
            + [f"+23480000000{i}" for i in range(1, 15)]
        )
        from app.models.message import WhatsAppMessage
        await session.execute(delete(WhatsAppMessage).where(WhatsAppMessage.from_phone.in_(test_phones)))
        await session.execute(delete(WhatsAppMessage).where(WhatsAppMessage.to_phone.in_(test_phones)))
        await session.execute(delete(User).where(User.phone_number.in_(test_phones)))
        await session.execute(delete(Business).where(Business.phone_number.in_(test_phones)))
        await session.execute(delete(Waitlist).where(Waitlist.phone_number.in_(test_phones)))
        await session.execute(delete(InviteCode).where(InviteCode.code.like("TEST-%")))
        await session.execute(delete(InviteCode).where(InviteCode.code.like("RACE-%")))
        await session.commit()
    await engine.dispose()


@pytest.mark.asyncio
async def test_valid_invite_code_creates_account_and_increments_use_count():
    """Verify that a valid invite code atomically increments use_count and provisions business & user."""
    code_str = f"TEST-{uuid4().hex[:6].upper()}"
    phone = "+234800000001"
    norm_phone = normalize_phone(phone)

    async with async_session_factory() as session:
        invite_service = InviteCodeService()
        await invite_service.create_code(session, code=code_str, max_uses=2)

    mock_whatsapp = AsyncMock()
    mock_whatsapp.send_text.return_value = WhatsAppSendResult(message_id=f"msg_welcome_{uuid4().hex[:8]}")

    processor = WhatsAppWebhookProcessor(whatsapp=mock_whatsapp)
    parsed = ParsedWhatsAppMessage(
        message_id=f"wamid_test_1_{uuid4().hex[:8]}",
        from_phone=phone,
        message_type="text",
        body=f"Hello, my invite code is {code_str}",
        timestamp=datetime.now(UTC),
    )

    async with async_session_factory() as session:
        await processor.process_message(session, parsed, event=None)

    # Verify directly against database
    async with async_session_factory() as session:
        # 1. Invite code use_count incremented
        inv_res = await session.execute(select(InviteCode).where(InviteCode.code == code_str))
        inv = inv_res.scalar_one()
        assert inv.use_count == 1

        # 2. User exists
        user_res = await session.execute(select(User).where(User.phone_number == norm_phone))
        user = user_res.scalar_one_or_none()
        assert user is not None

        # 3. Business exists
        biz_res = await session.execute(select(Business).where(Business.phone_number == norm_phone))
        biz = biz_res.scalar_one_or_none()
        assert biz is not None

    # Verify welcome message sent to correct recipient
    mock_whatsapp.send_text.assert_awaited_once()
    assert mock_whatsapp.send_text.await_args[0][0] == phone
    assert "Welcome to Waasz!" in mock_whatsapp.send_text.await_args[0][1]


@pytest.mark.asyncio
async def test_exhausted_invite_code_is_rejected_and_creates_no_user():
    """Verify that an exhausted invite code (use_count >= max_uses) is rejected and creates NO database records."""
    code_str = f"TEST-{uuid4().hex[:6].upper()}"
    phone = "+234800000002"
    norm_phone = normalize_phone(phone)

    async with async_session_factory() as session:
        invite = InviteCode(code=code_str, max_uses=1, use_count=1, is_active=True)
        session.add(invite)
        await session.commit()

    mock_whatsapp = AsyncMock()
    mock_whatsapp.send_text.return_value = WhatsAppSendResult(message_id="msg_gate")

    processor = WhatsAppWebhookProcessor(whatsapp=mock_whatsapp)
    parsed = ParsedWhatsAppMessage(
        message_id="wamid_test_2",
        from_phone=phone,
        message_type="text",
        body=code_str,
        timestamp=datetime.now(UTC),
    )

    async with async_session_factory() as session:
        await processor.process_message(session, parsed, event=None)

    # Verify database directly: NO user or business created
    async with async_session_factory() as session:
        user_res = await session.execute(select(User).where(User.phone_number == norm_phone))
        assert user_res.scalar_one_or_none() is None

        biz_res = await session.execute(select(Business).where(Business.phone_number == norm_phone))
        assert biz_res.scalar_one_or_none() is None

        # Invite code was not further incremented
        inv_res = await session.execute(select(InviteCode).where(InviteCode.code == code_str))
        inv = inv_res.scalar_one()
        assert inv.use_count == 1

    # Gate rejection message sent
    mock_whatsapp.send_text.assert_awaited_once()
    assert "private access by invitation only" in mock_whatsapp.send_text.await_args[0][1]


@pytest.mark.asyncio
async def test_expired_invite_code_is_rejected_and_creates_no_user():
    """Verify that an expired invite code is rejected and creates NO database records."""
    code_str = f"TEST-{uuid4().hex[:6].upper()}"
    phone = "+234800000003"
    norm_phone = normalize_phone(phone)

    async with async_session_factory() as session:
        invite = InviteCode(
            code=code_str,
            max_uses=10,
            use_count=0,
            is_active=True,
            expires_at=datetime.now(UTC) - timedelta(days=2),
        )
        session.add(invite)
        await session.commit()

    mock_whatsapp = AsyncMock()
    mock_whatsapp.send_text.return_value = WhatsAppSendResult(message_id="msg_gate")

    processor = WhatsAppWebhookProcessor(whatsapp=mock_whatsapp)
    parsed = ParsedWhatsAppMessage(
        message_id="wamid_test_3",
        from_phone=phone,
        message_type="text",
        body=code_str,
        timestamp=datetime.now(UTC),
    )

    async with async_session_factory() as session:
        await processor.process_message(session, parsed, event=None)

    # Verify database directly: NO user or business created
    async with async_session_factory() as session:
        user_res = await session.execute(select(User).where(User.phone_number == norm_phone))
        assert user_res.scalar_one_or_none() is None

        biz_res = await session.execute(select(Business).where(Business.phone_number == norm_phone))
        assert biz_res.scalar_one_or_none() is None

        inv_res = await session.execute(select(InviteCode).where(InviteCode.code == code_str))
        inv = inv_res.scalar_one()
        assert inv.use_count == 0


@pytest.mark.asyncio
async def test_concurrent_redemption_of_single_use_code_only_succeeds_once():
    """
    Race test: Two different phones concurrently attempt to redeem the exact same
    single-use (max_uses=1) invite code.
    Only one must succeed; the other must be rejected, with use_count capped at 1.
    """
    code_str = f"RACE-{uuid4().hex[:6].upper()}"
    phone_a = "+234800000004"
    phone_b = "+234800000005"
    norm_a = normalize_phone(phone_a)
    norm_b = normalize_phone(phone_b)

    async with async_session_factory() as session:
        invite = InviteCode(code=code_str, max_uses=1, use_count=0, is_active=True)
        session.add(invite)
        await session.commit()

    mock_whatsapp = AsyncMock()
    mock_whatsapp.send_text.return_value = WhatsAppSendResult(message_id="msg_race")

    processor = WhatsAppWebhookProcessor(whatsapp=mock_whatsapp)

    parsed_a = ParsedWhatsAppMessage(
        message_id="wamid_race_a",
        from_phone=phone_a,
        message_type="text",
        body=code_str,
        timestamp=datetime.now(UTC),
    )
    parsed_b = ParsedWhatsAppMessage(
        message_id="wamid_race_b",
        from_phone=phone_b,
        message_type="text",
        body=code_str,
        timestamp=datetime.now(UTC),
    )

    async def redeem(parsed_msg):
        async with async_session_factory() as session:
            await processor.process_message(session, parsed_msg, event=None)

    # Execute both redemptions concurrently
    await asyncio.gather(redeem(parsed_a), redeem(parsed_b))

    # Assertions against the real database
    async with async_session_factory() as session:
        # Code was used exactly once
        inv_res = await session.execute(select(InviteCode).where(InviteCode.code == code_str))
        inv = inv_res.scalar_one()
        assert inv.use_count == 1

        # Check users: exactly ONE user exists across phone_a and phone_b
        res_a = await session.execute(select(User).where(User.phone_number == norm_a))
        res_b = await session.execute(select(User).where(User.phone_number == norm_b))
        user_a = res_a.scalar_one_or_none()
        user_b = res_b.scalar_one_or_none()

        assert (user_a is not None and user_b is None) or (user_a is None and user_b is not None), (
            f"Expected exactly one user created, got user_a={user_a}, user_b={user_b}"
        )


@pytest.mark.asyncio
async def test_unapproved_number_request_waitlist_logs_to_waitlist_without_creating_user():
    """Verify that sending 'REQUEST' when referrals are enabled logs to waitlist without creating user."""
    phone = "+234800000006"
    norm_phone = normalize_phone(phone)

    mock_whatsapp = AsyncMock()
    mock_whatsapp.send_text.return_value = WhatsAppSendResult(message_id="msg_waitlist")

    processor = WhatsAppWebhookProcessor(whatsapp=mock_whatsapp)
    parsed = ParsedWhatsAppMessage(
        message_id="wamid_test_req",
        from_phone=phone,
        message_type="text",
        body="REQUEST",
        timestamp=datetime.now(UTC),
    )

    with patch.object(settings, "enable_user_referrals", True):
        async with async_session_factory() as session:
            await processor.process_message(session, parsed, event=None)

    # Check database directly
    async with async_session_factory() as session:
        # Waitlist row exists
        wl_res = await session.execute(select(Waitlist).where(Waitlist.phone_number == norm_phone))
        wl = wl_res.scalar_one_or_none()
        assert wl is not None
        assert wl.phone_number == norm_phone

        # No User created
        user_res = await session.execute(select(User).where(User.phone_number == norm_phone))
        assert user_res.scalar_one_or_none() is None

        # No Business created
        biz_res = await session.execute(select(Business).where(Business.phone_number == norm_phone))
        assert biz_res.scalar_one_or_none() is None

    # Waitlist message sent
    mock_whatsapp.send_text.assert_awaited_once()
    assert "added to our private access waitlist" in mock_whatsapp.send_text.await_args[0][1]


@pytest.mark.asyncio
async def test_unapproved_number_random_traffic_creates_zero_db_records_and_no_llm():
    """Curious/wrong-number traffic gets gate notice, creates ZERO rows, and invokes ZERO LLM calls."""
    phone = "+234800000007"
    norm_phone = normalize_phone(phone)

    mock_whatsapp = AsyncMock()
    mock_whatsapp.send_text.return_value = WhatsAppSendResult(message_id="msg_gate_random")

    mock_agent = AsyncMock()

    processor = WhatsAppWebhookProcessor(whatsapp=mock_whatsapp, agent=mock_agent)
    parsed = ParsedWhatsAppMessage(
        message_id="wamid_random",
        from_phone=phone,
        message_type="text",
        body="Hello what is your pricing?",
        timestamp=datetime.now(UTC),
    )

    async with async_session_factory() as session:
        await processor.process_message(session, parsed, event=None)

    # 1. Agent LLM was NEVER called
    mock_agent.handle_message.assert_not_called()

    # 2. No database records created
    async with async_session_factory() as session:
        user_res = await session.execute(select(User).where(User.phone_number == norm_phone))
        assert user_res.scalar_one_or_none() is None

        biz_res = await session.execute(select(Business).where(Business.phone_number == norm_phone))
        assert biz_res.scalar_one_or_none() is None
