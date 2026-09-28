from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4
import pytest

from app.models.business import Business
from app.models.task import Task
from app.schemas.whatsapp import WhatsAppSendResult
from app.services.report_delivery import ReportDeliveryService
from app.services.task_service import TaskService


@pytest.mark.asyncio
async def test_mark_reminder_sent_atomic_claim():
    """Verify that mark_reminder_sent executes atomic conditional update."""
    db = AsyncMock()
    # Simulate first claim succeeding (rowcount = 1)
    result_mock = MagicMock()
    result_mock.rowcount = 1
    db.execute.return_value = result_mock

    service = TaskService()
    task_id = uuid4()
    claimed = await service.mark_reminder_sent(db, task_id)
    assert claimed is True
    db.commit.assert_awaited_once()

    # Simulate subsequent concurrent claim failing (rowcount = 0)
    result_mock.rowcount = 0
    claimed_again = await service.mark_reminder_sent(db, task_id)
    assert claimed_again is False


@pytest.mark.asyncio
async def test_send_task_reminders_no_double_send():
    """Verify that reminders are not double-sent if already claimed."""
    task_id = uuid4()
    business_id = uuid4()
    now = datetime.now(UTC)
    
    mock_task = Task(
        id=task_id,
        business_id=business_id,
        title="Check inventory shipment",
        description="Verify 10 cartons received",
        due_at=now - timedelta(minutes=5),
        reminder_sent_at=None,
        completed_at=None,
    )
    mock_business = Business(
        id=business_id,
        name="Lagos Fabrics",
        phone_number="+2348012345678",
    )

    mock_tasks_service = MagicMock()
    # First time: return task. mark_reminder_sent returns True (claimed)
    mock_tasks_service.get_reminders_to_send = AsyncMock(return_value=[mock_task])
    mock_tasks_service.mark_reminder_sent = AsyncMock(return_value=True)

    mock_whatsapp = MagicMock()
    mock_whatsapp.send_text = AsyncMock(return_value=WhatsAppSendResult(message_id="msg_123"))

    mock_ledger = MagicMock()
    mock_ledger.record_outbound_message = AsyncMock()

    mock_session = AsyncMock()
    mock_session.get.return_value = mock_business

    delivery = ReportDeliveryService(
        whatsapp=mock_whatsapp,
        ledger=mock_ledger,
        tasks=mock_tasks_service,
    )

    with patch("app.services.report_delivery.async_session_factory") as mock_factory:
        mock_factory.return_value.__aenter__.return_value = mock_session
        sent_count = await delivery.send_task_reminders()

    assert sent_count == 1
    mock_whatsapp.send_text.assert_awaited_once()
    sent_msg = mock_whatsapp.send_text.await_args[0][1]
    assert "⏰ REMINDER: Check inventory shipment" in sent_msg
    assert "Verify 10 cartons received" in sent_msg

    # Second invocation: task is already claimed (mark_reminder_sent returns False)
    mock_tasks_service.mark_reminder_sent = AsyncMock(return_value=False)
    mock_whatsapp.send_text.reset_mock()

    with patch("app.services.report_delivery.async_session_factory") as mock_factory:
        mock_factory.return_value.__aenter__.return_value = mock_session
        second_sent_count = await delivery.send_task_reminders()

    assert second_sent_count == 0
    mock_whatsapp.send_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_send_task_reminders_flags_late_delivery():
    """Verify that tasks more than 15 minutes overdue are flagged as LATE REMINDER."""
    task_id = uuid4()
    business_id = uuid4()
    # Due 30 minutes ago (system was down)
    due_at = datetime.now(UTC) - timedelta(minutes=30)
    
    mock_task = Task(
        id=task_id,
        business_id=business_id,
        title="Call distributor",
        description=None,
        due_at=due_at,
        reminder_sent_at=None,
        completed_at=None,
    )
    mock_business = Business(
        id=business_id,
        name="Kano Traders",
        phone_number="+2348098765432",
    )

    mock_tasks_service = MagicMock()
    mock_tasks_service.get_reminders_to_send = AsyncMock(return_value=[mock_task])
    mock_tasks_service.mark_reminder_sent = AsyncMock(return_value=True)

    mock_whatsapp = MagicMock()
    mock_whatsapp.send_text = AsyncMock(return_value=WhatsAppSendResult(message_id="msg_456"))

    mock_ledger = MagicMock()
    mock_ledger.record_outbound_message = AsyncMock()

    mock_session = AsyncMock()
    mock_session.get.return_value = mock_business

    delivery = ReportDeliveryService(
        whatsapp=mock_whatsapp,
        ledger=mock_ledger,
        tasks=mock_tasks_service,
    )

    with patch("app.services.report_delivery.async_session_factory") as mock_factory:
        mock_factory.return_value.__aenter__.return_value = mock_session
        sent_count = await delivery.send_task_reminders()

    assert sent_count == 1
    mock_whatsapp.send_text.assert_awaited_once()
    sent_msg = mock_whatsapp.send_text.await_args[0][1]
    assert "⏰ LATE REMINDER (was due" in sent_msg
    assert "Call distributor" in sent_msg


@pytest.mark.asyncio
async def test_send_task_reminders_routes_to_individual_user_phone():
    """Verify that reminders with a user_id route to the sender's own phone number, not business.phone_number."""
    task_id = uuid4()
    business_id = uuid4()
    user_id = uuid4()
    now = datetime.now(UTC)

    mock_task = Task(
        id=task_id,
        business_id=business_id,
        user_id=user_id,
        title="Review staff shift schedule",
        description=None,
        due_at=now - timedelta(minutes=2),
        reminder_sent_at=None,
        completed_at=None,
    )
    mock_business = Business(
        id=business_id,
        name="Lagos Fabrics",
        phone_number="+2348000000000",
    )
    from app.models.user import User
    mock_user = User(
        id=user_id,
        business_id=business_id,
        phone_number="+2348099112233",
        display_name="Ifeoma",
    )

    mock_tasks_service = MagicMock()
    mock_tasks_service.get_reminders_to_send = AsyncMock(return_value=[mock_task])
    mock_tasks_service.mark_reminder_sent = AsyncMock(return_value=True)

    mock_whatsapp = MagicMock()
    mock_whatsapp.send_text = AsyncMock(return_value=WhatsAppSendResult(message_id="msg_789"))

    mock_ledger = MagicMock()
    mock_ledger.record_outbound_message = AsyncMock()

    mock_session = AsyncMock()
    def mock_get(model_cls, pk):
        if model_cls == Business:
            return mock_business
        elif model_cls == User:
            return mock_user
        return None
    mock_session.get.side_effect = mock_get

    delivery = ReportDeliveryService(
        whatsapp=mock_whatsapp,
        ledger=mock_ledger,
        tasks=mock_tasks_service,
    )

    with patch("app.services.report_delivery.async_session_factory") as mock_factory:
        mock_factory.return_value.__aenter__.return_value = mock_session
        sent_count = await delivery.send_task_reminders()

    assert sent_count == 1
    mock_whatsapp.send_text.assert_awaited_once()
    recipient_arg = mock_whatsapp.send_text.await_args[0][0]
    # CRITICAL: Sent to individual sender phone (+2348099112233), NOT business phone (+2348000000000)
    assert recipient_arg == "+2348099112233"
    assert recipient_arg != mock_business.phone_number

