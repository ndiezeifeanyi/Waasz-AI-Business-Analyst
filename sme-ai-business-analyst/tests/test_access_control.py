from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch
from uuid import uuid4
import pytest
from sqlalchemy import delete, select, func

from app.core.config import settings
from app.core.database import async_session_factory
from app.models.approved_tester import ApprovedTester
from app.models.business import Business
from app.models.message import WhatsAppMessage
from app.models.user import User
from app.schemas.whatsapp import ParsedWhatsAppMessage, WhatsAppSendResult
from app.services.access_control_service import AccessControlService
from app.services.invite_service import InviteCodeService
from app.services.webhook_processor import WhatsAppWebhookProcessor
from app.utils.phone import normalize_phone


@pytest.fixture(autouse=True)
async def cleanup_access_control_test_records():
    from app.core.database import engine
    await engine.dispose()
    orig_mode = settings.access_mode
    orig_cap = settings.max_active_users
    orig_req = settings.require_invite_code
    ac = AccessControlService()
    orig_db_mode = None
    orig_db_cap = None
    try:
        async with async_session_factory() as session:
            orig_db_mode = await ac.get_access_mode(session)
            orig_db_cap = await ac.get_max_active_users(session)
    except Exception:
        pass

    yield

    settings.access_mode = orig_mode
    settings.max_active_users = orig_cap
    settings.require_invite_code = orig_req

    async with async_session_factory() as session:
        try:
            if orig_db_mode is not None:
                await ac.set_access_mode(session, orig_db_mode)
            if orig_db_cap is not None:
                await ac.set_max_active_users(session, orig_db_cap)
        except Exception:
            pass
        test_phones = (
            [normalize_phone(f"+23480000002{i}") for i in range(0, 10)]
            + [f"+23480000002{i}" for i in range(0, 10)]
            + [normalize_phone(f"+23480000009{i}") for i in range(0, 10)]
            + [f"+23480000009{i}" for i in range(0, 10)]
        )
        await session.execute(delete(WhatsAppMessage).where(WhatsAppMessage.from_phone.in_(test_phones)))
        await session.execute(delete(WhatsAppMessage).where(WhatsAppMessage.to_phone.in_(test_phones)))
        await session.execute(delete(User).where(User.phone_number.in_(test_phones)))
        await session.execute(delete(Business).where(Business.phone_number.in_(test_phones)))
        await session.execute(delete(ApprovedTester).where(ApprovedTester.phone_number.in_(test_phones)))
        await session.commit()
    await engine.dispose()


@pytest.mark.asyncio
async def test_allowlist_mode_unapproved_number_rejected_creates_zero_db_records():
    """
    Mode: allowlist
    A number NOT on the allowlist is rejected and creates zero database rows.
    Verified directly against the database, not just the reply text.
    """
    settings.access_mode = "allowlist"
    settings.max_active_users = 20
    async with async_session_factory() as session:
        await AccessControlService().set_access_mode(session, "allowlist")
        await AccessControlService().set_max_active_users(session, 20)

    phone = "+234800000021"
    norm_phone = normalize_phone(phone)

    captured_outbound: list[str] = []
    mock_whatsapp = AsyncMock()

    async def fake_send_text(to_phone: str, body: str):
        captured_outbound.append(body)
        return WhatsAppSendResult(message_id="msg_gate_deny")

    mock_whatsapp.send_text = AsyncMock(side_effect=fake_send_text)

    mock_agent = AsyncMock()
    processor = WhatsAppWebhookProcessor(whatsapp=mock_whatsapp, agent=mock_agent)

    parsed = ParsedWhatsAppMessage(
        message_id=f"wamid_gate_{uuid4().hex[:8]}",
        from_phone=phone,
        message_type="text",
        body="Hello, I want to use the bot",
        timestamp=datetime.now(UTC),
    )

    async with async_session_factory() as session:
        await processor.process_message(session, parsed, event=None)

    # 1. Assert rejection message sent
    assert len(captured_outbound) == 1
    assert "limited private test" in captured_outbound[0]

    # 2. Assert zero LLM / Agent calls
    mock_agent.handle_message.assert_not_called()

    # 3. Assert ZERO database rows created
    async with async_session_factory() as session:
        user_res = await session.execute(select(User).where(User.phone_number == norm_phone))
        assert user_res.scalar_one_or_none() is None

        biz_res = await session.execute(select(Business).where(Business.phone_number == norm_phone))
        assert biz_res.scalar_one_or_none() is None

        msg_res = await session.execute(select(WhatsAppMessage).where(WhatsAppMessage.from_phone == norm_phone))
        assert len(msg_res.scalars().all()) == 0


@pytest.mark.asyncio
async def test_allowlist_mode_approved_number_admitted_normally():
    """
    Mode: allowlist
    A number ON the allowlist is admitted normally.
    """
    settings.access_mode = "allowlist"
    settings.max_active_users = 20
    async with async_session_factory() as session:
        await AccessControlService().set_access_mode(session, "allowlist")
        await AccessControlService().set_max_active_users(session, 20)

    phone = "+234800000022"
    norm_phone = normalize_phone(phone)

    # Add to approved testers
    async with async_session_factory() as session:
        service = AccessControlService()
        await service.add_approved_tester(session, phone=phone, label="Test Approved Tester")

    mock_whatsapp = AsyncMock()
    mock_whatsapp.send_text.return_value = WhatsAppSendResult(message_id="msg_admitted")

    mock_agent = AsyncMock()
    mock_agent.process_user_message.return_value = "Hello from agent!"

    processor = WhatsAppWebhookProcessor(whatsapp=mock_whatsapp, agent=mock_agent)
    parsed = ParsedWhatsAppMessage(
        message_id=f"wamid_ok_{uuid4().hex[:8]}",
        from_phone=phone,
        message_type="text",
        body="Hello Waasz, good morning!",
        timestamp=datetime.now(UTC),
    )

    async with async_session_factory() as session:
        await processor.process_message(session, parsed, event=None)
        await session.commit()

    # Verify user and business were created in the database
    async with async_session_factory() as session:
        user_res = await session.execute(select(User).where(User.phone_number == norm_phone))
        user = user_res.scalar_one_or_none()
        assert user is not None
        assert user.phone_number == norm_phone

        biz_res = await session.execute(select(Business).where(Business.id == user.business_id))
        biz = biz_res.scalar_one_or_none()
        assert biz is not None


@pytest.mark.asyncio
async def test_allowlist_mode_max_active_users_cap_rejects_even_approved_number():
    """
    Mode: allowlist
    Once MAX_ACTIVE_USERS is reached, even an allowlisted number that hasn't
    registered yet is rejected (hard cap independent of list size).
    """
    phone = "+234800000023"
    norm_phone = normalize_phone(phone)

    settings.access_mode = "allowlist"

    # Add tester to allowlist
    async with async_session_factory() as session:
        service = AccessControlService()
        await service.add_approved_tester(session, phone=phone, label="Approved But Capped")

        # Query current count of users and set cap to exactly that count
        user_count_res = await session.execute(select(func.count(User.id)))
        current_users = user_count_res.scalar() or 0

    # Set hard cap equal to current users, so system is at capacity
    settings.max_active_users = current_users
    async with async_session_factory() as session:
        await AccessControlService().set_access_mode(session, "allowlist")
        await AccessControlService().set_max_active_users(session, current_users)

    captured_outbound: list[str] = []
    mock_whatsapp = AsyncMock()

    async def fake_send_text(to_phone: str, body: str):
        captured_outbound.append(body)
        return WhatsAppSendResult(message_id="msg_cap_deny")

    mock_whatsapp.send_text = AsyncMock(side_effect=fake_send_text)

    mock_agent = AsyncMock()
    processor = WhatsAppWebhookProcessor(whatsapp=mock_whatsapp, agent=mock_agent)

    parsed = ParsedWhatsAppMessage(
        message_id=f"wamid_capped_{uuid4().hex[:8]}",
        from_phone=phone,
        message_type="text",
        body="Hey I'm approved, let me in",
        timestamp=datetime.now(UTC),
    )

    async with async_session_factory() as session:
        await processor.process_message(session, parsed, event=None)

    # 1. Assert capacity limit message sent
    assert len(captured_outbound) == 1
    assert "maximum user capacity" in captured_outbound[0]

    # 2. Assert no user record created
    async with async_session_factory() as session:
        user_res = await session.execute(select(User).where(User.phone_number == norm_phone))
        assert user_res.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_open_mode_admits_any_number_immediately_with_no_gate_check():
    """
    Mode: open
    No gate check at all — every new number gets an account immediately.
    """
    settings.access_mode = "open"
    async with async_session_factory() as session:
        await AccessControlService().set_access_mode(session, "open")

    phone = "+234800000024"
    norm_phone = normalize_phone(phone)

    mock_whatsapp = AsyncMock()
    mock_whatsapp.send_text.return_value = WhatsAppSendResult(message_id="msg_open")

    mock_agent = AsyncMock()
    mock_agent.process_user_message.return_value = "Hello from open mode agent"

    processor = WhatsAppWebhookProcessor(whatsapp=mock_whatsapp, agent=mock_agent)
    parsed = ParsedWhatsAppMessage(
        message_id=f"wamid_open_{uuid4().hex[:8]}",
        from_phone=phone,
        message_type="text",
        body="Hi, open world!",
        timestamp=datetime.now(UTC),
    )

    async with async_session_factory() as session:
        await processor.process_message(session, parsed, event=None)
        await session.commit()

    # User and business were created directly
    async with async_session_factory() as session:
        user_res = await session.execute(select(User).where(User.phone_number == norm_phone))
        user = user_res.scalar_one_or_none()
        assert user is not None
        assert user.phone_number == norm_phone


@pytest.mark.asyncio
async def test_switching_access_mode_to_invite_code_requires_code():
    """
    Mode: invite_code
    Confirm switching ACCESS_MODE to 'invite_code' enforces invite codes.
    """
    settings.access_mode = "invite_code"
    async with async_session_factory() as session:
        await AccessControlService().set_access_mode(session, "invite_code")

    phone = "+234800000025"
    norm_phone = normalize_phone(phone)

    # 1. Non-code text is rejected
    captured_replies = []
    mock_whatsapp = AsyncMock()
    async def fake_send_text(to_phone: str, body: str):
        captured_replies.append(body)
        return WhatsAppSendResult(message_id="msg_inv_rej")
    mock_whatsapp.send_text = AsyncMock(side_effect=fake_send_text)

    processor = WhatsAppWebhookProcessor(whatsapp=mock_whatsapp)
    parsed = ParsedWhatsAppMessage(
        message_id=f"wamid_no_code_{uuid4().hex[:8]}",
        from_phone=phone,
        message_type="text",
        body="Just saying hello without a code",
        timestamp=datetime.now(UTC),
    )

    async with async_session_factory() as session:
        await processor.process_message(session, parsed, event=None)

    assert len(captured_replies) == 1
    assert "by invitation only" in captured_replies[0]

    # Verify no user created
    async with async_session_factory() as session:
        user_res = await session.execute(select(User).where(User.phone_number == norm_phone))
        assert user_res.scalar_one_or_none() is None

    # 2. Sending valid code creates account
    code_str = f"SW-{uuid4().hex[:6].upper()}"
    async with async_session_factory() as session:
        inv_service = InviteCodeService()
        await inv_service.create_code(session, code=code_str, max_uses=1)

    parsed_with_code = ParsedWhatsAppMessage(
        message_id=f"wamid_with_code_{uuid4().hex[:8]}",
        from_phone=phone,
        message_type="text",
        body=f"Here is my code: {code_str}",
        timestamp=datetime.now(UTC),
    )

    async with async_session_factory() as session:
        await processor.process_message(session, parsed_with_code, event=None)

    # User created and welcome sent
    async with async_session_factory() as session:
        user_res = await session.execute(select(User).where(User.phone_number == norm_phone))
        user = user_res.scalar_one_or_none()
        assert user is not None
        assert user.phone_number == norm_phone


@pytest.mark.asyncio
async def test_access_control_service_admin_methods():
    """
    Verify add_approved_tester, list_approved_testers, get_status, and remove_approved_tester.
    """
    phone = "+234800000026"
    norm_phone = normalize_phone(phone)

    service = AccessControlService()
    async with async_session_factory() as session:
        tester = await service.add_approved_tester(session, phone=phone, label="Admin QA", added_by="admin_user")
        assert tester.phone_number == norm_phone
        assert tester.label == "Admin QA"

        testers = await service.list_approved_testers(session)
        tester_phones = [t.phone_number for t in testers]
        assert norm_phone in tester_phones

        status_info = await service.get_status(session)
        assert "access_mode" in status_info
        assert "max_active_users" in status_info
        assert "active_users_count" in status_info
        assert "approved_testers_count" in status_info
        assert status_info["approved_testers_count"] >= 1

        deleted = await service.remove_approved_tester(session, phone=phone)
        assert deleted is True

        testers_after = await service.list_approved_testers(session)
        after_phones = [t.phone_number for t in testers_after]
        assert norm_phone not in after_phones
