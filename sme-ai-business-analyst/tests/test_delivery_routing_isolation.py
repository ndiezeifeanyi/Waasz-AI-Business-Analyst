from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4
import asyncio
import pytest

from sqlalchemy import delete, select

from app.core.config import settings
from app.core.database import async_session_factory
from app.models.activity import Activity
from app.models.business import Business
from app.models.task import Task
from app.models.user import User
from app.schemas.whatsapp import ParsedWhatsAppMessage, WhatsAppSendResult
from app.services.report_delivery import ReportDeliveryService
from app.services.unified_report_service import UnifiedReportService
from app.services.webhook_processor import WhatsAppWebhookProcessor
from app.services.group_handler import GroupChatHandler
from app.utils.phone import normalize_phone


@pytest.fixture(autouse=True)
async def cleanup_isolation_records():
    from app.core.database import engine
    await engine.dispose()
    yield
    async with async_session_factory() as session:
        test_phones = (
            [normalize_phone(f"+234809900000{i}") for i in range(1, 10)]
            + [f"+234809900000{i}" for i in range(1, 10)]
        )
        from app.models.message import WhatsAppMessage
        await session.execute(delete(WhatsAppMessage).where(WhatsAppMessage.from_phone.in_(test_phones)))
        await session.execute(delete(WhatsAppMessage).where(WhatsAppMessage.to_phone.in_(test_phones)))
        await session.execute(delete(Activity).where(Activity.title.like("IsoAct-%")))
        await session.execute(delete(Task).where(Task.title.like("IsoTask-%")))
        await session.execute(delete(User).where(User.phone_number.in_(test_phones)))
        await session.execute(delete(Business).where(Business.phone_number.in_(test_phones)))
        await session.commit()
    await engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_inbound_routing_isolation():
    """
    Real concurrency test: Inbound messages from 3 different phone numbers processed
    genuinely concurrently with asyncio.gather.
    Assert each outbound WhatsApp call's 'to' argument exactly matches its own sender
    with zero cross-wiring.
    """
    phones = ["+2348099000001", "+2348099000002", "+2348099000003"]
    norm_phones = [normalize_phone(p) for p in phones]

    # Pre-create users so they bypass the invite gate and enter live message processing
    async with async_session_factory() as session:
        for p, np in zip(phones, norm_phones):
            biz = Business(name=f"Biz-{np[-4:]}", phone_number=np, is_provisional=False)
            session.add(biz)
            await session.flush()
            user = User(business_id=biz.id, phone_number=np, display_name=f"User-{np[-4:]}", role="owner")
            session.add(user)
        await session.commit()

    captured_outbound: list[tuple[str, str]] = []

    mock_whatsapp = MagicMock()
    async def fake_send_text(to_phone: str, body: str):
        # Introduce a micro-sleep to interleave async executions genuinely
        await asyncio.sleep(0.02)
        captured_outbound.append((to_phone, body))
        return WhatsAppSendResult(message_id=f"msg_{to_phone[-4:]}_{uuid4().hex[:6]}")

    mock_whatsapp.send_text = AsyncMock(side_effect=fake_send_text)

    # Mock agent to return a distinct greeting reflecting the sender
    mock_agent = MagicMock()
    async def fake_agent_reply(**kwargs):
        u = kwargs.get("user")
        return f"Reply exclusively for {u.phone_number}"

    mock_agent.process_user_message = AsyncMock(side_effect=fake_agent_reply)
    mock_agent.handle_message = AsyncMock(side_effect=fake_agent_reply)

    processor = WhatsAppWebhookProcessor(whatsapp=mock_whatsapp, agent=mock_agent)

    async def send_inbound(sender_phone: str, index: int):
        parsed = ParsedWhatsAppMessage(
            message_id=f"wamid_conc_{index}_{uuid4().hex[:6]}",
            from_phone=sender_phone,
            message_type="text",
            body=f"Message from {sender_phone}",
            timestamp=datetime.now(UTC),
        )
        async with async_session_factory() as session:
            await processor.process_message(session, parsed, event=None)

    # Execute all 3 inbound messages concurrently
    await asyncio.gather(
        send_inbound(phones[0], 1),
        send_inbound(phones[1], 2),
        send_inbound(phones[2], 3),
    )

    # Assert exactly 3 outbound messages were dispatched
    assert len(captured_outbound) == 3

    # Assert zero cross-wiring: each recipient received only their own message
    for to_phone, body in captured_outbound:
        norm_to = normalize_phone(to_phone)
        assert norm_to in norm_phones
        assert f"Reply exclusively for {norm_to}" in body


@pytest.mark.asyncio
async def test_batch_reminder_dispatch_routing_isolation():
    """
    Batch-dispatch isolation test:
    Create due reminders for 3 different users in the same scheduler tick.
    Run the dispatch loop once, and assert each of the 3 outbound calls went to the
    correct, distinct phone number — no repeats, no swaps.
    """
    phones = ["+2348099000004", "+2348099000005", "+2348099000006"]
    norm_phones = [normalize_phone(p) for p in phones]

    task_ids = []
    async with async_session_factory() as session:
        # Ensure only test tasks and fresh message state are evaluated in this run
        from app.models.message import WhatsAppMessage
        await session.execute(delete(WhatsAppMessage).where(WhatsAppMessage.from_phone.in_(norm_phones)))
        await session.execute(delete(WhatsAppMessage).where(WhatsAppMessage.to_phone.in_(norm_phones)))
        await session.execute(delete(Task).where(Task.completed_at.is_(None)))
        for i, (p, np) in enumerate(zip(phones, norm_phones)):
            biz = Business(name=f"Biz-{np[-4:]}", phone_number=np, is_provisional=False)
            session.add(biz)
            await session.flush()
            user = User(business_id=biz.id, phone_number=np, display_name=f"User-{i}", role="owner")
            session.add(user)
            await session.flush()

            task = Task(
                business_id=biz.id,
                user_id=user.id,
                title=f"IsoTask-{np[-4:]}-Action",
                description=f"Important action item for {np}",
                due_at=datetime.now(UTC) - timedelta(minutes=5),
                completed_at=None,
                reminder_sent_at=None,
            )
            session.add(task)
            await session.flush()
            task_ids.append(task.id)
        await session.commit()

    captured_reminders: list[tuple[str, str]] = []

    mock_whatsapp = MagicMock()
    async def fake_send_text(to_phone: str, body: str):
        captured_reminders.append((to_phone, body))
        return WhatsAppSendResult(message_id=f"rem_{to_phone[-4:]}_{uuid4().hex[:6]}")

    async def fake_send_template(to_phone: str, template_name: str, language_code: str, components: list):
        body_summary = " ".join([str(p.get("text", "")) for c in components if c.get("type") == "body" for p in c.get("parameters", [])])
        captured_reminders.append((to_phone, f"Template: {body_summary}"))
        return WhatsAppSendResult(message_id=f"rem_tmpl_{to_phone[-4:]}_{uuid4().hex[:6]}")

    mock_whatsapp.send_text = AsyncMock(side_effect=fake_send_text)
    mock_whatsapp.send_template = AsyncMock(side_effect=fake_send_template)

    delivery = ReportDeliveryService(whatsapp=mock_whatsapp)

    # Run dispatch loop in a single scheduler tick
    sent_count = await delivery.send_task_reminders()

    assert sent_count == 3
    assert len(captured_reminders) == 3

    # Confirm distinct recipients with no duplicates
    recipients = [to_phone for to_phone, _ in captured_reminders]
    assert len(set(recipients)) == 3

    # Confirm each recipient got their OWN distinct task
    for to_phone, body in captured_reminders:
        norm_to = normalize_phone(to_phone)
        assert norm_to in norm_phones
        assert f"IsoTask-{norm_to[-4:]}-Action" in body
        assert f"Important action item for {norm_to}" in body


@pytest.mark.asyncio
async def test_batch_unified_reports_dispatch_routing_isolation():
    """
    Batch-dispatch report isolation test:
    Verify that scheduled unified reports across multiple distinct users
    deliver strictly to each user's own phone number.
    """
    phones = ["+2348099000007", "+2348099000008", "+2348099000009"]
    norm_phones = [normalize_phone(p) for p in phones]

    user_ids = []
    async with async_session_factory() as session:
        for i, (p, np) in enumerate(zip(phones, norm_phones)):
            biz = Business(name=f"RepBiz-{np[-4:]}", phone_number=np, is_provisional=False)
            session.add(biz)
            await session.flush()
            user = User(
                business_id=biz.id,
                phone_number=np,
                display_name=f"ReportUser-{np[-4:]}",
                role="owner",
                last_inbound_at=datetime.now(UTC),  # within 24h window
            )
            session.add(user)
            await session.flush()
            user_ids.append(user.id)

            act = Activity(
                user_id=user.id,
                business_id=biz.id,
                title=f"IsoAct-{np[-4:]}-Event",
                activity_type="task",
                occurred_at=datetime.now(UTC) - timedelta(hours=2),
            )
            session.add(act)
        await session.commit()

    captured_reports: list[tuple[str, str]] = []

    mock_whatsapp = MagicMock()
    async def fake_send_text(to_phone: str, body: str):
        captured_reports.append((to_phone, body))
        return WhatsAppSendResult(message_id=f"rep_{to_phone[-4:]}_{uuid4().hex[:6]}")

    mock_whatsapp.send_text = AsyncMock(side_effect=fake_send_text)

    unified_svc = UnifiedReportService(whatsapp=mock_whatsapp)

    # Dispatch scheduled reports batch
    async with async_session_factory() as session:
        sent_count = await unified_svc.dispatch_scheduled_reports(session, cadence="daily")

    assert sent_count >= 3

    # Check only our test users' outbound reports
    our_reports = [(p, b) for p, b in captured_reports if p in norm_phones or p in phones]
    assert len(our_reports) == 3

    delivered_phones = [normalize_phone(p) for p, _ in our_reports]
    assert set(delivered_phones) == set(norm_phones)


@pytest.mark.asyncio
async def test_group_vs_direct_delivery_routing_isolation():
    """
    Group handling isolation:
    Confirm that a group reply is strictly sent to the group thread (group_id),
    never to a group member's private phone number.
    And a private 1-on-1 message is never delivered to a group thread.
    """
    user_phone = "+2348099000007"
    norm_phone = normalize_phone(user_phone)
    group_id = "120363029999999999@g.us"

    captured_destinations: list[str] = []

    mock_whatsapp = MagicMock()
    async def fake_send_text(to_phone: str, body: str):
        captured_destinations.append(to_phone)
        return WhatsAppSendResult(message_id=f"msg_out_{uuid4().hex[:6]}")

    mock_whatsapp.send_text = AsyncMock(side_effect=fake_send_text)

    # Mock groups handler with active status
    mock_groups = MagicMock(spec=GroupChatHandler)
    mock_groups.handle_group_message = AsyncMock(return_value="Group response from bot")

    processor = WhatsAppWebhookProcessor(whatsapp=mock_whatsapp, groups=mock_groups)

    # 1. Inbound group message
    group_msg = ParsedWhatsAppMessage(
        message_id=f"wamid_group_{uuid4().hex[:6]}",
        from_phone=user_phone,
        message_type="text",
        body="@assistant what is the meeting agenda?",
        timestamp=datetime.now(UTC),
        raw_payload={"group_id": group_id},
    )

    async with async_session_factory() as session:
        await processor.process_message(session, group_msg, event=None)

    # Assert outbound reply went to group_id, NOT user_phone
    assert len(captured_destinations) == 1
    assert captured_destinations[0] == group_id
    assert captured_destinations[0] != user_phone

    # 2. Inbound direct 1-on-1 message
    dm_msg = ParsedWhatsAppMessage(
        message_id=f"wamid_dm_{uuid4().hex[:6]}",
        from_phone=user_phone,
        message_type="text",
        body="What is my confidential revenue?",
        timestamp=datetime.now(UTC),
        raw_payload={},
    )

    # Mock agent for DM
    mock_agent = MagicMock()
    async def fake_dm_reply(**kwargs):
        return "Your private revenue is confidential."

    mock_agent.process_user_message = AsyncMock(side_effect=fake_dm_reply)
    mock_agent.handle_message = AsyncMock(side_effect=fake_dm_reply)
    processor.agent = mock_agent

    # Pre-create user so DM proceeds
    async with async_session_factory() as session:
        biz = Business(name="PrivBiz", phone_number=norm_phone, is_provisional=False)
        session.add(biz)
        await session.flush()
        user = User(business_id=biz.id, phone_number=norm_phone, display_name="PrivUser", role="owner")
        session.add(user)
        await session.commit()

    async with async_session_factory() as session:
        await processor.process_message(session, dm_msg, event=None)

    # Assert outbound reply went to user_phone, NEVER group_id
    assert len(captured_destinations) == 2
    assert captured_destinations[1] == user_phone
    assert captured_destinations[1] != group_id
