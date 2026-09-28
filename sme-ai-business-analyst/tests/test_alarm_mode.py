import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.models.business import Business
from app.models.task import Task
from app.models.user import User
from app.schemas.whatsapp import ParsedWhatsAppMessage, WhatsAppSendResult
from app.services.agent_service import AgentService
from app.services.report_delivery import ReportDeliveryService
from app.services.task_service import TaskService
from app.services.webhook_processor import WhatsAppWebhookProcessor


@pytest.mark.asyncio
async def test_alarm_mode_first_dispatch_interactive_buttons():
    """Alarm mode on first dispatch attaches 'Turn Off' and 'Snooze 10 min' buttons."""
    biz_id = uuid4()
    task_id = uuid4()
    biz = Business(
        id=biz_id,
        name="Test Biz",
        owner_name="Test Owner",
        phone_number="2348011112222",
    )
    task = Task(
        id=task_id,
        business_id=biz_id,
        title="Check oven temperature",
        due_at=datetime.now(UTC) - timedelta(minutes=1),
        is_alarm_mode=True,
        repeat_interval_seconds=60,
        max_repeats=5,
        reminder_sent_at=None,
    )

    mock_db = AsyncMock()
    mock_db.get.return_value = biz
    # Mock last inbound check within 24h
    mock_inbound_res = MagicMock()
    mock_inbound_msg = MagicMock(created_at=datetime.now(UTC))
    mock_inbound_res.scalar_one_or_none.return_value = mock_inbound_msg
    mock_db.execute.return_value = mock_inbound_res

    mock_tasks = AsyncMock()
    mock_tasks.get_reminders_to_send.return_value = [task]
    mock_tasks.mark_reminder_sent.return_value = True
    mock_tasks.get_alarm_repeats_to_send.return_value = []

    mock_whatsapp = MagicMock()
    mock_whatsapp.send_alarm_reminder = AsyncMock(
        return_value=WhatsAppSendResult(message_id="msg_alarm_1")
    )
    mock_whatsapp.send_text = AsyncMock(return_value=WhatsAppSendResult(message_id="msg_text_1"))

    delivery = ReportDeliveryService(whatsapp=mock_whatsapp, tasks=mock_tasks)

    with patch("app.services.report_delivery.async_session_factory") as mock_factory:
        mock_factory.return_value.__aenter__.return_value = mock_db
        sent = await delivery.send_task_reminders()

    assert sent >= 1
    # Verify send_alarm_reminder was called with Turn Off & Snooze buttons
    mock_whatsapp.send_alarm_reminder.assert_called_once()
    call_args = mock_whatsapp.send_alarm_reminder.call_args
    assert call_args[0][0] == "2348011112222"
    assert "Check oven temperature" in call_args[0][1]
    assert call_args[1]["task_id"] == str(task.id)
    # Verify mark_reminder_sent was called with is_alarm_mode=True
    mock_tasks.mark_reminder_sent.assert_called_once_with(mock_db, task.id, is_alarm_mode=True)


@pytest.mark.asyncio
async def test_alarm_mode_atomic_claim_prevents_double_send():
    """Atomic claim pattern prevents duplicate repeat sends under concurrent scheduler ticks."""
    tasks_service = TaskService()
    task_id = uuid4()
    expected_last_sent = datetime.now(UTC) - timedelta(minutes=1)

    # First call: execute returns rowcount=1 (successful claim)
    mock_db_1 = AsyncMock()
    mock_res_1 = MagicMock(rowcount=1)
    mock_db_1.execute.return_value = mock_res_1

    claim_1 = await tasks_service.claim_alarm_repeat(
        mock_db_1,
        task_id,
        current_repeat_count=1,
        expected_last_sent=expected_last_sent,
    )

    # Second concurrent call: execute returns rowcount=0 (already claimed by another tick)
    mock_db_2 = AsyncMock()
    mock_res_2 = MagicMock(rowcount=0)
    mock_db_2.execute.return_value = mock_res_2

    claim_2 = await tasks_service.claim_alarm_repeat(
        mock_db_2,
        task_id,
        current_repeat_count=1,
        expected_last_sent=expected_last_sent,
    )

    # First claim succeeds, second claim MUST fail
    assert claim_1 is True
    assert claim_2 is False
    assert mock_db_1.commit.called
    assert mock_db_2.commit.called


@pytest.mark.asyncio
async def test_alarm_mode_max_repeats_graceful_stop():
    """When repeat_count reaches max_repeats, final graceful message is sent and alarm is dismissed."""
    biz_id = uuid4()
    task_id = uuid4()
    biz = Business(id=biz_id, name="Biz Max", phone_number="2348000000002")
    task = Task(
        id=task_id,
        business_id=biz_id,
        title="Check gate lock",
        due_at=datetime.now(UTC) - timedelta(minutes=10),
        is_alarm_mode=True,
        repeat_interval_seconds=60,
        max_repeats=3,
        reminder_sent_at=datetime.now(UTC) - timedelta(minutes=5),
        last_repeat_sent_at=datetime.now(UTC) - timedelta(minutes=2),
        repeat_count=2,  # Next tick will be repeat #3 (reaches max_repeats 3)
    )

    mock_db = AsyncMock()
    mock_db.get.return_value = biz
    mock_inbound_res = MagicMock()
    mock_inbound_msg = MagicMock(created_at=datetime.now(UTC))
    mock_inbound_res.scalar_one_or_none.return_value = mock_inbound_msg
    mock_db.execute.return_value = mock_inbound_res

    mock_tasks = AsyncMock()
    mock_tasks.get_reminders_to_send.return_value = []
    mock_tasks.get_alarm_repeats_to_send.return_value = [task]
    mock_tasks.claim_alarm_repeat.return_value = True

    mock_whatsapp = MagicMock()
    mock_whatsapp.send_text = AsyncMock(return_value=WhatsAppSendResult(message_id="msg_final_stop"))
    mock_whatsapp.send_alarm_reminder = AsyncMock()

    delivery = ReportDeliveryService(whatsapp=mock_whatsapp, tasks=mock_tasks)

    with patch("app.services.report_delivery.async_session_factory") as mock_factory:
        mock_factory.return_value.__aenter__.return_value = mock_db
        sent = await delivery.send_task_reminders()

    assert sent >= 1
    # Verify claim was called with is_final=True
    mock_tasks.claim_alarm_repeat.assert_called_once_with(
        mock_db,
        task.id,
        current_repeat_count=2,
        expected_last_sent=task.last_repeat_sent_at,
        is_final=True,
    )
    # Verify final stop message sent
    mock_whatsapp.send_text.assert_called_once()
    stop_msg = mock_whatsapp.send_text.call_args[0][1]
    assert "stop reminding you" in stop_msg.lower()
    assert "Check gate lock" in stop_msg


@pytest.mark.asyncio
async def test_alarm_mode_turn_off_deterministic_and_duplicate_click():
    """'Turn Off' button is handled deterministically before LLM routing, and duplicate click is idempotent."""
    biz_id = uuid4()
    task_id = uuid4()
    biz = Business(id=biz_id, name="Biz TurnOff", phone_number="2348000000003")
    user = User(id=uuid4(), business_id=biz_id, phone_number="2348000000003")
    task = Task(
        id=task_id,
        business_id=biz_id,
        user_id=user.id,
        title="Shut down generator",
        is_alarm_mode=True,
        dismissed_at=None,
    )

    mock_db = AsyncMock()
    mock_tasks = AsyncMock()
    mock_tasks.dismiss_alarm.return_value = True

    mock_whatsapp = MagicMock()
    mock_whatsapp.send_text = AsyncMock(return_value=WhatsAppSendResult(message_id="msg_ack_off"))
    processor = WhatsAppWebhookProcessor(whatsapp=mock_whatsapp, tasks=mock_tasks)

    # 1. First button tap: "🔕 Turn Off"
    msg_1 = ParsedWhatsAppMessage(
        message_id="wamid_btn_1",
        from_phone="2348000000003",
        message_type="interactive",
        interactive_reply_id=f"alarm_off_{task.id}",
        body="🔕 Turn Off",
        timestamp="1790330000",
    )
    mock_db.get.return_value = task
    res_1 = await processor._try_handle_alarm_button(mock_db, msg_1, biz, user)
    assert res_1 is not None
    assert "Alarm turned off" in res_1
    assert "Shut down generator" in res_1
    mock_tasks.dismiss_alarm.assert_called_once_with(mock_db, task.id)

    # 2. Duplicate button tap: task.dismissed_at is now already set
    task.dismissed_at = datetime.now(UTC)
    mock_tasks.dismiss_alarm.reset_mock()

    msg_2 = ParsedWhatsAppMessage(
        message_id="wamid_btn_2_duplicate",
        from_phone="2348000000003",
        message_type="interactive",
        interactive_reply_id=f"alarm_off_{task.id}",
        body="🔕 Turn Off",
        timestamp="1790330002",
    )
    res_2 = await processor._try_handle_alarm_button(mock_db, msg_2, biz, user)
    # Handled cleanly without re-calling dismiss_alarm or raising error
    assert res_2 is not None
    assert "Alarm turned off" in res_2
    mock_tasks.dismiss_alarm.assert_not_called()


@pytest.mark.asyncio
async def test_alarm_mode_snooze_cycle_reset():
    """Snooze button sets due_at to now + 10m and cleanly resets repeat state without leaking."""
    biz_id = uuid4()
    task_id = uuid4()
    biz = Business(id=biz_id, name="Biz Snooze", phone_number="2348000000004")
    user = User(id=uuid4(), business_id=biz_id, phone_number="2348000000004")
    task = Task(
        id=task_id,
        business_id=biz_id,
        user_id=user.id,
        title="Check refrigerator",
        due_at=datetime.now(UTC) - timedelta(minutes=5),
        is_alarm_mode=True,
        repeat_interval_seconds=60,
        max_repeats=10,
        reminder_sent_at=datetime.now(UTC) - timedelta(minutes=4),
        last_repeat_sent_at=datetime.now(UTC) - timedelta(minutes=1),
        repeat_count=3,
        dismissed_at=None,
    )

    mock_db = AsyncMock()
    mock_db.get.return_value = task
    mock_tasks = AsyncMock()
    mock_tasks.snooze_alarm.return_value = task

    processor = WhatsAppWebhookProcessor(tasks=mock_tasks)

    snooze_msg = ParsedWhatsAppMessage(
        message_id="wamid_snooze_1",
        from_phone="2348000000004",
        message_type="interactive",
        interactive_reply_id=f"alarm_snooze_{task.id}",
        body="⏰ Snooze 10 min",
        timestamp="1790330050",
    )
    res = await processor._try_handle_alarm_button(mock_db, snooze_msg, biz, user)
    assert res is not None
    assert "Alarm snoozed for 10 minutes" in res
    assert "Check refrigerator" in res
    mock_tasks.snooze_alarm.assert_called_once_with(mock_db, task.id, snooze_minutes=10)

    # Also verify TaskService.snooze_alarm state reset logic
    ts = TaskService()
    now_before = datetime.now(UTC)
    snoozed_task = await ts.snooze_alarm(mock_db, task.id, snooze_minutes=10)
    assert snoozed_task.due_at >= now_before + timedelta(minutes=9)
    assert snoozed_task.reminder_sent_at is None
    assert snoozed_task.last_repeat_sent_at is None
    assert snoozed_task.repeat_count == 0
    assert snoozed_task.dismissed_at is None


@pytest.mark.asyncio
async def test_create_reminder_tool_alarm_mode_trigger():
    """create_reminder sets is_alarm_mode=True on alarm phrasing or explicit flag, and False on normal phrasing."""
    biz = Business(id=uuid4(), name="Biz Tool", phone_number="2348000000005")
    user = User(id=uuid4(), business_id=biz.id, phone_number="2348000000005")

    mock_tasks = AsyncMock()
    mock_task_alarm = Task(
        id=uuid4(),
        business_id=biz.id,
        title="Turn off water pump",
        is_alarm_mode=True,
        repeat_interval_seconds=60,
    )
    mock_task_regular = Task(
        id=uuid4(),
        business_id=biz.id,
        title="Call vendor",
        is_alarm_mode=False,
    )

    mock_db = AsyncMock()
    agent = AgentService(tasks=mock_tasks)

    # 1. Alarm phrasing: "Keep reminding me until I stop it"
    mock_tasks.create_task.return_value = mock_task_alarm
    res1, _ = await agent._execute_tool(
        mock_db,
        biz,
        user,
        "create_reminder",
        {
            "title": "Turn off water pump",
            "due_at_iso": (datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
        },
        raw_user_text="Keep reminding me until I turn it off to check the water pump in 5 mins",
        inbound_message_id=uuid4(),
    )
    assert "Alarm 'Turn off water pump' created" in res1
    assert mock_tasks.create_task.call_args.kwargs["is_alarm_mode"] is True

    # 2. Standard reminder phrasing: single-shot default preserved
    mock_tasks.create_task.return_value = mock_task_regular
    res2, _ = await agent._execute_tool(
        mock_db,
        biz,
        user,
        "create_reminder",
        {
            "title": "Call vendor",
            "due_at_iso": (datetime.now(UTC) + timedelta(minutes=10)).isoformat(),
        },
        raw_user_text="Remind me to call vendor in 10 mins",
        inbound_message_id=uuid4(),
    )
    assert "Reminder 'Call vendor' created" in res2
    assert mock_tasks.create_task.call_args.kwargs["is_alarm_mode"] is False
