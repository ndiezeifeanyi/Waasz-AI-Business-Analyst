"""
Phase 4 Tests: Truthful Delivery Reporting, Meta Error Code Mapping & Merchant Visibility
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4
import pytest

from app.models.business import Business
from app.models.customer import Customer
from app.models.debt import Debt
from app.models.debt_recovery import DebtEvent, DebtorConsent, DebtorMessage, MessageSuppression, ReminderState
from app.models.user import User
from app.schemas.whatsapp import ParsedWhatsAppStatus
from app.services.debtor_send_service import DebtorSendService, explain_meta_error
from app.services.reminder_ladder_service import ReminderLadderService


@pytest.fixture
def mock_whatsapp():
    mock = AsyncMock()
    class Res:
        success = True
        message_id = "wamid_phase4_123"
        error_message = None
        skipped = False
    mock.send_template.return_value = Res()
    mock.send_text.return_value = Res()
    return mock


# -----------------------------------------------------------------------------
# 1. TRUTHFUL INITIAL SEND REPORTING (NEVER DELIVERED ON HTTP 200)
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_truthful_initial_send_reporting_accepted_never_delivered(mock_whatsapp):
    """Verify HTTP 200 marks message as 'accepted', never 'delivered'."""
    service = DebtorSendService(whatsapp=mock_whatsapp)
    mock_db = AsyncMock()

    biz_id = uuid4()
    debt_id = uuid4()
    cust_id = uuid4()

    biz = Business(id=biz_id, name="Victory Chemist")
    cust = Customer(id=cust_id, business_id=biz_id, name="Emeka", phone_number="+2348031234567")
    debt = Debt(id=debt_id, business_id=biz_id, customer_id=cust_id, amount=50000, due_date=datetime.now(UTC))
    consent = DebtorConsent(id=uuid4(), business_id=biz_id, debt_id=debt_id, debtor_phone="+2348031234567")

    async def mock_get(model, pk):
        if model is Debt: return debt
        if model is Business: return biz
        if model is Customer: return cust
        return None
    mock_db.get.side_effect = mock_get

    res_consent = MagicMock()
    res_consent.scalar_one_or_none.return_value = consent

    res_rem = MagicMock()
    res_rem.scalar_one_or_none.return_value = ReminderState(debt_id=debt_id, business_id=biz_id)

    with patch.object(service.recovery, "is_phone_suppressed", AsyncMock(return_value=False)), \
         patch.object(service, "is_within_sending_hours", return_value=True), \
         patch.object(service, "check_frequency_caps", AsyncMock(return_value=(True, "ok"))):
        mock_db.execute.side_effect = [res_consent, res_rem]
        success, status, msg_rec = await service.send_debtor_notice(
            db=mock_db,
            business_id=biz_id,
            debt_id=debt_id,
            stage="due_today",
            template_name="iou_due_today_v2",
        )

        assert success is True
        # Crucial requirement: status MUST be accepted, NOT delivered!
        assert status == "accepted"
        assert msg_rec.status == "accepted"
        assert msg_rec.wamid == "wamid_phase4_123"
        mock_whatsapp.send_template.assert_called_once()


# -----------------------------------------------------------------------------
# 2. WEBHOOK STATUS TRANSITIONS: DELIVERED & READ
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_webhook_status_delivered_transitions_to_delivered():
    """Verify webhook with status='delivered' updates message to delivered and logs message_delivered event."""
    service = DebtorSendService(whatsapp=AsyncMock())
    mock_db = AsyncMock()

    biz_id = uuid4()
    debt_id = uuid4()
    wamid = "wamid.phase4.test1"

    msg = DebtorMessage(
        id=uuid4(),
        business_id=biz_id,
        debt_id=debt_id,
        stage="due_today",
        template_name="iou_due_today_v2",
        wamid=wamid,
        status="accepted",
    )

    res_msg = MagicMock()
    res_msg.scalar_one_or_none.return_value = msg
    mock_db.execute.return_value = res_msg

    delivery_time = datetime.now(UTC)
    status_item = ParsedWhatsAppStatus(
        message_id=wamid,
        recipient_id="2348031234567",
        status="delivered",
        timestamp=delivery_time,
        raw_payload={"pricing": {"category": "utility"}},
    )

    with patch.object(service.recovery, "record_debt_event", AsyncMock()) as mock_event:
        handled = await service.handle_message_status_update(mock_db, status_item)
        assert handled is True
        assert msg.status == "delivered"
        assert msg.pricing_category == "utility"
        assert msg.updated_at == delivery_time

        # Verify DebtEvent recorded with event_type="message_delivered"
        mock_event.assert_called_once()
        call_kwargs = mock_event.call_args.kwargs
        assert call_kwargs["event_type"] == "message"
        assert call_kwargs["payload"]["status"] == "delivered"


@pytest.mark.asyncio
async def test_webhook_status_read_transitions_to_read():
    """Verify webhook with status='read' updates message to read and logs message_read event."""
    service = DebtorSendService(whatsapp=AsyncMock())
    mock_db = AsyncMock()

    biz_id = uuid4()
    debt_id = uuid4()
    wamid = "wamid.phase4.test2"

    msg = DebtorMessage(
        id=uuid4(),
        business_id=biz_id,
        debt_id=debt_id,
        stage="due_today",
        template_name="iou_due_today_v2",
        wamid=wamid,
        status="delivered",
    )

    res_msg = MagicMock()
    res_msg.scalar_one_or_none.return_value = msg
    mock_db.execute.return_value = res_msg

    read_time = datetime.now(UTC)
    status_item = ParsedWhatsAppStatus(
        message_id=wamid,
        recipient_id="2348031234567",
        status="read",
        timestamp=read_time,
    )

    with patch.object(service.recovery, "record_debt_event", AsyncMock()) as mock_event:
        handled = await service.handle_message_status_update(mock_db, status_item)
        assert handled is True
        assert msg.status == "read"
        assert msg.updated_at == read_time

        # Verify DebtEvent recorded with event_type="message" and status="read"
        mock_event.assert_called_once()
        assert mock_event.call_args.kwargs["event_type"] == "message"
        assert mock_event.call_args.kwargs["payload"]["status"] == "read"


# -----------------------------------------------------------------------------
# 3. IDEMPOTENCY & NON-REGRESSION
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_webhook_status_idempotency_duplicate_delivered():
    """Verify duplicate delivered webhooks do not duplicate state or cause regression."""
    service = DebtorSendService(whatsapp=AsyncMock())
    mock_db = AsyncMock()

    biz_id = uuid4()
    debt_id = uuid4()
    wamid = "wamid.phase4.test3"

    msg = DebtorMessage(
        id=uuid4(),
        business_id=biz_id,
        debt_id=debt_id,
        stage="due_today",
        template_name="iou_due_today_v2",
        wamid=wamid,
        status="delivered",
    )

    res_msg = MagicMock()
    res_msg.scalar_one_or_none.return_value = msg
    mock_db.execute.return_value = res_msg

    status_item = ParsedWhatsAppStatus(
        message_id=wamid,
        recipient_id="2348031234567",
        status="delivered",
        timestamp=datetime.now(UTC),
    )

    handled = await service.handle_message_status_update(mock_db, status_item)
    assert handled is True
    assert msg.status == "delivered"


@pytest.mark.asyncio
async def test_webhook_status_non_regression_read_does_not_regress_to_delivered():
    """Verify that an out-of-order 'delivered' webhook does not regress status from 'read'."""
    service = DebtorSendService(whatsapp=AsyncMock())
    mock_db = AsyncMock()

    biz_id = uuid4()
    debt_id = uuid4()
    wamid = "wamid.phase4.test4"

    read_time = datetime.now(UTC) - timedelta(minutes=5)
    msg = DebtorMessage(
        id=uuid4(),
        business_id=biz_id,
        debt_id=debt_id,
        stage="due_today",
        template_name="iou_due_today_v2",
        wamid=wamid,
        status="read",
        updated_at=read_time,
    )

    res_msg = MagicMock()
    res_msg.scalar_one_or_none.return_value = msg
    mock_db.execute.return_value = res_msg

    # Late-arriving delivered webhook
    late_delivered = ParsedWhatsAppStatus(
        message_id=wamid,
        recipient_id="2348031234567",
        status="delivered",
        timestamp=datetime.now(UTC) - timedelta(minutes=10),
    )

    handled = await service.handle_message_status_update(mock_db, late_delivered)
    assert handled is True
    # Must remain 'read', not regressed to 'delivered'!
    assert msg.status == "read"


# -----------------------------------------------------------------------------
# 4. META ERROR CODE TRANSLATIONS (HONEST REPORTING)
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_meta_error_code_131047_translation():
    """Verify error 131047 translates to 'Customer has not messaged in over 24 hours...'."""
    service = DebtorSendService(whatsapp=AsyncMock())
    mock_db = AsyncMock()

    wamid = "wamid.phase4.err131047"
    msg = DebtorMessage(
        id=uuid4(),
        business_id=uuid4(),
        debt_id=uuid4(),
        stage="due_today",
        template_name="iou_due_today_v2",
        wamid=wamid,
        status="accepted",
    )

    res_msg = MagicMock()
    res_msg.scalar_one_or_none.return_value = msg
    mock_db.execute.return_value = res_msg

    status_item = ParsedWhatsAppStatus(
        message_id=wamid,
        recipient_id="2348031234567",
        status="failed",
        error_code=131047,
        error_message="Re-engagement message",
    )

    handled = await service.handle_message_status_update(mock_db, status_item)
    assert handled is True
    assert msg.status == "failed"
    assert msg.error_code == "131047"
    assert "Customer has not messaged in over 24 hours" in msg.error_title
    assert "Pre-approved template required" in msg.error_title


@pytest.mark.asyncio
async def test_meta_error_code_131049_translation_and_24h_cooldown():
    """Verify error 131049 translates honestly and sets 24-hour next_retry_at cooldown."""
    service = DebtorSendService(whatsapp=AsyncMock())
    mock_db = AsyncMock()

    wamid = "wamid.phase4.err131049"
    msg = DebtorMessage(
        id=uuid4(),
        business_id=uuid4(),
        debt_id=uuid4(),
        stage="due_today",
        template_name="iou_due_today_v2",
        wamid=wamid,
        status="accepted",
    )

    res_msg = MagicMock()
    res_msg.scalar_one_or_none.return_value = msg
    mock_db.execute.return_value = res_msg

    status_item = ParsedWhatsAppStatus(
        message_id=wamid,
        recipient_id="2348031234567",
        status="failed",
        error_code=131049,
        error_message="Message failed to send due to Meta policy",
    )

    handled = await service.handle_message_status_update(mock_db, status_item)
    assert handled is True
    assert msg.status == "failed"
    assert msg.error_code == "131049"
    assert "recipient throttled" in msg.error_title
    # Next retry must be >= 23 hours in future
    assert msg.next_retry_at is not None
    assert msg.next_retry_at > datetime.now(UTC) + timedelta(hours=23)


@pytest.mark.asyncio
async def test_meta_error_code_132000_translation():
    """Verify error 132000 translates to 'Template parameter mismatch. Check template parameters.'"""
    service = DebtorSendService(whatsapp=AsyncMock())
    mock_db = AsyncMock()

    wamid = "wamid.phase4.err132000"
    msg = DebtorMessage(
        id=uuid4(),
        business_id=uuid4(),
        debt_id=uuid4(),
        stage="due_today",
        template_name="iou_due_today_v2",
        wamid=wamid,
        status="accepted",
    )

    res_msg = MagicMock()
    res_msg.scalar_one_or_none.return_value = msg
    mock_db.execute.return_value = res_msg

    status_item = ParsedWhatsAppStatus(
        message_id=wamid,
        recipient_id="2348031234567",
        status="failed",
        error_code=132000,
        error_message="Parameter count does not match",
    )

    handled = await service.handle_message_status_update(mock_db, status_item)
    assert handled is True
    assert msg.status == "failed"
    assert msg.error_code == "132000"
    assert "Template parameter mismatch" in msg.error_title


@pytest.mark.asyncio
async def test_meta_error_code_131026_suppresses_phone():
    """Verify unreachable recipient error 131026 triggers phone suppression."""
    service = DebtorSendService(whatsapp=AsyncMock())
    mock_db = AsyncMock()

    debt_id = uuid4()
    wamid = "wamid.phase4.err131026"
    msg = DebtorMessage(
        id=uuid4(),
        business_id=uuid4(),
        debt_id=debt_id,
        stage="due_today",
        template_name="iou_due_today_v2",
        wamid=wamid,
        status="accepted",
    )

    res_msg = MagicMock()
    res_msg.scalar_one_or_none.return_value = msg
    res_phone = MagicMock()
    res_phone.scalar_one_or_none.return_value = "+2348031234567"

    mock_db.execute.side_effect = [res_msg, res_phone]

    status_item = ParsedWhatsAppStatus(
        message_id=wamid,
        recipient_id="2348031234567",
        status="failed",
        error_code=131026,
        error_message="Undeliverable",
    )

    with patch.object(service.recovery, "suppress_phone", AsyncMock()) as mock_suppress:
        handled = await service.handle_message_status_update(mock_db, status_item)
        assert handled is True
        assert msg.status == "failed"
        assert "unreachable" in msg.error_title.lower() or "inactive" in msg.error_title.lower()
        mock_suppress.assert_called_once_with(mock_db, "+2348031234567", reason="unreachable")


# -----------------------------------------------------------------------------
# 5. MERCHANT VISIBILITY (STATUS LABELS AND TIMESTAMPS)
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_merchant_truthful_delivery_status_formatting():
    """Verify ReminderLadderService.format_delivery_status renders truthful descriptions."""
    # A. Accepted / Sent -> waiting for delivery confirmation
    sent_msg = DebtorMessage(status="accepted", updated_at=datetime.now(UTC))
    status_text = ReminderLadderService.format_delivery_status(sent_msg)
    assert "⏳ Sent (waiting for delivery confirmation)" in status_text

    # B. Delivered -> Delivered to phone
    delivered_msg = DebtorMessage(status="delivered", updated_at=datetime.now(UTC))
    status_text = ReminderLadderService.format_delivery_status(delivered_msg)
    assert "📬 Delivered to phone" in status_text

    # C. Read -> Read by customer
    read_msg = DebtorMessage(status="read", updated_at=datetime.now(UTC))
    status_text = ReminderLadderService.format_delivery_status(read_msg)
    assert "👁️ Read by customer" in status_text

    # D. Failed with 131047 -> Honest explanation
    failed_msg = DebtorMessage(
        status="failed",
        error_code="131047",
        error_title="Customer has not messaged in over 24 hours. Pre-approved template required.",
        updated_at=datetime.now(UTC),
    )
    status_text = ReminderLadderService.format_delivery_status(failed_msg)
    assert "⚠️ Failed to deliver" in status_text
    assert "Customer has not messaged in over 24 hours" in status_text
