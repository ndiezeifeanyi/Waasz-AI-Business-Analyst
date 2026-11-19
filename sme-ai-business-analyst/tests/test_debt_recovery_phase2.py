from datetime import UTC, datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4
import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import settings
from app.core.database import Base
from app.models.business import Business
from app.models.customer import Customer
from app.models.debt import Debt
from app.models.debt_recovery import (
    DebtEvent,
    DebtorConsent,
    DebtorMessage,
    MessageSuppression,
    ReminderState,
)
from app.schemas.whatsapp import ParsedWhatsAppMessage, ParsedWhatsAppStatus, WhatsAppSendResult
from app.services.debtor_send_service import DebtorSendService, WAT_TZ
from app.services.template_service import TEMPLATES_V2, TemplateService
from app.utils.tokens import generate_debt_token, mask_phone_number, verify_debt_token


# 1. TOKEN ROUND TRIP AND EXPIRY TESTS
def test_token_round_trip_and_expiry():
    debt_id = uuid4()
    # Valid token
    token = generate_debt_token(debt_id, expires_in_seconds=3600)
    is_valid, decoded_id, reason = verify_debt_token(token)
    assert is_valid is True
    assert decoded_id == debt_id
    assert reason == "valid"

    # Tampered signature
    import base64
    padding = 4 - (len(token) % 4)
    raw = base64.urlsafe_b64decode((token + ("=" * padding if padding != 4 else "")).encode()).decode()
    parts = raw.split(":")
    parts[-1] = "0" * 32
    tampered_sig_token = base64.urlsafe_b64encode(":".join(parts).encode()).decode().rstrip("=")
    is_valid, _, reason = verify_debt_token(tampered_sig_token)
    assert is_valid is False
    assert reason == "invalid_signature"

    # Malformed token
    is_valid, _, reason = verify_debt_token("completely-invalid-base64-random-string!!!")
    assert is_valid is False
    assert reason == "malformed"

    # Expired token
    expired_token = generate_debt_token(debt_id, expires_in_seconds=-10)
    is_valid, expired_id, reason = verify_debt_token(expired_token)
    assert is_valid is False
    assert expired_id == debt_id
    assert reason == "expired"


def test_phone_privacy_masking():
    assert mask_phone_number("08031234567") == "+234 ••• ••• 4567"
    assert mask_phone_number("+2348031234567") == "+234 ••• ••• 4567"
    assert mask_phone_number("2348031234567") == "+234 ••• ••• 4567"
    assert mask_phone_number(None) == "Not on file"


# 2. TEMPLATE CONFORMANCE TESTS (HARD RULE 4: No bank details or PDF in templates)
def test_no_bank_details_or_pdf_inside_any_template():
    service = TemplateService()
    for name, tmpl in TEMPLATES_V2.items():
        body = tmpl["body"]
        # Rule 9: Do not start or end body with variable
        assert not body.startswith("{{"), f"{name} body starts with a variable"
        assert not body.endswith("}}"), f"{name} body ends with a variable"

        # Hard Rule 4: No bank details or PDF in template text
        lower_body = body.lower()
        assert "bank" not in lower_body, f"{name} contains 'bank'"
        assert "account number" not in lower_body, f"{name} contains 'account number'"
        assert ".pdf" not in lower_body, f"{name} contains '.pdf'"

        # Verify footer opt-out
        assert "Reply STOP to stop these messages." in tmpl["footer"]

        # Verify URL button exists with {{1}} token
        buttons = tmpl["buttons"]
        url_btns = [b for b in buttons if b["type"] == "URL"]
        assert len(url_btns) == 1
        assert "https://waaszai.com/r/{{1}}" in url_btns[0]["url"]


# 3. SEND RULES SEQUENCE TESTS
@pytest.mark.asyncio
async def test_send_rules_check_order():
    mock_whatsapp = MagicMock()
    service = DebtorSendService(whatsapp=mock_whatsapp)
    mock_db = AsyncMock()

    biz_id = uuid4()
    debt_id = uuid4()
    phone = "+2348031234567"

    # Rule 1: Kill switch
    with patch.object(service, "is_kill_switch_active", return_value=True):
        ok, reason, _ = await service.send_debtor_notice(mock_db, biz_id, debt_id, "record", "iou_record_v2")
        assert ok is False
        assert reason == "kill_switch_active"

    # Rule 2: Consent check (no consent)
    res_no_consent = MagicMock()
    res_no_consent.scalar_one_or_none.return_value = None
    mock_db.execute.return_value = res_no_consent

    ok, reason, _ = await service.send_debtor_notice(mock_db, biz_id, debt_id, "record", "iou_record_v2")
    assert ok is False
    assert reason == "no_consent"

    # Setup active consent for subsequent checks
    consent = DebtorConsent(id=uuid4(), business_id=biz_id, debt_id=debt_id, debtor_phone=phone)
    res_consent = MagicMock()
    res_consent.scalar_one_or_none.return_value = consent

    # Rule 3: Suppression check
    with patch.object(service.recovery, "is_phone_suppressed", AsyncMock(return_value=True)):
        mock_db.execute.return_value = res_consent
        ok, reason, _ = await service.send_debtor_notice(mock_db, biz_id, debt_id, "record", "iou_record_v2")
        assert ok is False
        assert reason == "suppressed"

    # Rule 4: Quiet hours check
    with patch.object(service.recovery, "is_phone_suppressed", AsyncMock(return_value=False)), \
         patch.object(service, "is_within_sending_hours", return_value=False):
        mock_db.execute.return_value = res_consent
        ok, reason, _ = await service.send_debtor_notice(mock_db, biz_id, debt_id, "record", "iou_record_v2")
        assert ok is False
        assert reason == "outside_sending_hours"

    # Rule 5: Frequency caps check
    with patch.object(service.recovery, "is_phone_suppressed", AsyncMock(return_value=False)), \
         patch.object(service, "is_within_sending_hours", return_value=True), \
         patch.object(service, "check_frequency_caps", AsyncMock(return_value=(False, "daily_cap_reached"))):
        mock_db.execute.return_value = res_consent
        ok, reason, _ = await service.send_debtor_notice(mock_db, biz_id, debt_id, "record", "iou_record_v2")
        assert ok is False
        assert "frequency_cap_exceeded" in reason

    # Rule 6: Reminder pause check
    rem_paused = ReminderState(debt_id=debt_id, business_id=biz_id, paused_until=datetime.now(UTC) + timedelta(days=2))
    res_rem = MagicMock()
    res_rem.scalar_one_or_none.return_value = rem_paused

    with patch.object(service.recovery, "is_phone_suppressed", AsyncMock(return_value=False)), \
         patch.object(service, "is_within_sending_hours", return_value=True), \
         patch.object(service, "check_frequency_caps", AsyncMock(return_value=(True, "ok"))):
        mock_db.execute.side_effect = [res_consent, res_rem]
        ok, reason, _ = await service.send_debtor_notice(mock_db, biz_id, debt_id, "record", "iou_record_v2")
        assert ok is False
        assert "paused_until" in reason


@pytest.mark.asyncio
async def test_truthful_delivery_reporting_never_delivered_on_http_200():
    """Hard Rule 6: Say 'delivered' to the merchant only after Meta's delivery webhook says so."""
    mock_whatsapp = AsyncMock()
    mock_whatsapp.send_template.return_value = WhatsAppSendResult(
        message_id="wamid.HBgNMjM0Nzg=",
        success=True,
    )
    service = DebtorSendService(whatsapp=mock_whatsapp)
    mock_db = AsyncMock()

    biz_id = uuid4()
    debt_id = uuid4()
    phone = "+2348031234567"

    consent = DebtorConsent(id=uuid4(), business_id=biz_id, debt_id=debt_id, debtor_phone=phone)
    debt = Debt(id=debt_id, business_id=biz_id, customer_id=uuid4(), amount=50000, due_date=datetime.now(UTC))
    biz = Business(id=biz_id, name="Test Shop", phone_number="2348000000001")
    cust = Customer(id=debt.customer_id, business_id=biz_id, name="Emeka", phone_number=phone)

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
        ok, status_str, msg_rec = await service.send_debtor_notice(mock_db, biz_id, debt_id, "record", "iou_record_v2")

        assert ok is True
        # CRITICAL: Initial status is 'accepted' on HTTP 200, NEVER 'delivered'!
        assert status_str == "accepted"
        assert msg_rec.status == "accepted"
        assert msg_rec.wamid == "wamid.HBgNMjM0Nzg="


# 4. WEBHOOK STATUS INGESTION & IDEMPOTENCY TESTS
@pytest.mark.asyncio
async def test_webhook_status_ingestion_and_idempotency():
    service = DebtorSendService()
    mock_db = AsyncMock()

    msg = DebtorMessage(
        id=uuid4(),
        business_id=uuid4(),
        debt_id=uuid4(),
        stage="record",
        template_name="iou_record_v2",
        wamid="wamid.123",
        status="accepted",
    )
    res_msg = MagicMock()
    res_msg.scalar_one_or_none.return_value = msg
    mock_db.execute.return_value = res_msg

    # 1. Delivery webhook arrives -> status becomes 'delivered'
    status_delivered = ParsedWhatsAppStatus(
        message_id="wamid.123",
        status="delivered",
        raw_payload={"pricing": {"category": "utility"}},
    )
    handled = await service.handle_message_status_update(mock_db, status_delivered)
    assert handled is True
    assert msg.status == "delivered"
    assert msg.pricing_category == "utility"

    # 2. Read webhook arrives -> status becomes 'read'
    status_read = ParsedWhatsAppStatus(message_id="wamid.123", status="read")
    await service.handle_message_status_update(mock_db, status_read)
    assert msg.status == "read"

    # 3. Duplicate/delayed 'delivered' webhook arrives -> does NOT regress status back to 'delivered'
    await service.handle_message_status_update(mock_db, status_delivered)
    assert msg.status == "read", "Status regressed from read to delivered"


# 5. DEBTOR INBOUND ACTIONS (STOP, START, PAID, NEED TIME, NOT ME)
@pytest.mark.asyncio
async def test_debtor_inbound_stop_suppresses_globally():
    service = DebtorSendService(whatsapp=AsyncMock())
    mock_db = AsyncMock()

    phone = "+2348031234567"
    parsed = ParsedWhatsAppMessage(
        message_id="wamid.stop1",
        from_phone="08031234567",
        message_type="text",
        body="STOP",
    )

    with patch.object(service.recovery, "suppress_phone", AsyncMock()) as mock_supp:
        # Mock no active consent
        res_c = MagicMock()
        res_c.scalars.return_value.all.return_value = []
        mock_db.execute.return_value = res_c

        handled = await service.try_handle_inbound_debtor_message(mock_db, parsed)
        assert handled is True
        mock_supp.assert_called_once_with(mock_db, phone, reason="stop_keyword")


@pytest.mark.asyncio
async def test_debtor_inbound_start_lifts_suppression():
    service = DebtorSendService(whatsapp=AsyncMock())
    mock_db = AsyncMock()

    phone = "+2348031234567"
    parsed = ParsedWhatsAppMessage(
        message_id="wamid.start1",
        from_phone="08031234567",
        message_type="text",
        body="START",
    )

    with patch.object(service.recovery, "resume_phone_by_debtor", AsyncMock(return_value=True)) as mock_resume:
        handled = await service.try_handle_inbound_debtor_message(mock_db, parsed)
        assert handled is True
        mock_resume.assert_called_once_with(mock_db, phone)


@pytest.mark.asyncio
async def test_debtor_inbound_i_have_paid():
    mock_wa = AsyncMock()
    service = DebtorSendService(whatsapp=mock_wa)
    mock_db = AsyncMock()

    biz_id = uuid4()
    debt_id = uuid4()
    phone = "+2348031234567"

    consent = DebtorConsent(id=uuid4(), business_id=biz_id, debt_id=debt_id, debtor_phone=phone)
    debt = Debt(id=debt_id, business_id=biz_id, customer_id=uuid4(), amount=35000, status="outstanding")
    biz = Business(id=biz_id, name="Super Shop", phone_number="2348000000001")
    cust = Customer(id=debt.customer_id, business_id=biz_id, name="Emeka", phone_number=phone)

    async def mock_get(model, pk):
        if model is Debt: return debt
        if model is Business: return biz
        if model is Customer: return cust
        return None
    mock_db.get.side_effect = mock_get

    res_c = MagicMock()
    res_c.scalar_one_or_none.return_value = consent
    mock_db.execute.return_value = res_c

    parsed = ParsedWhatsAppMessage(
        message_id="wamid.paid1",
        from_phone="08031234567",
        message_type="text",
        body="I have paid",
    )

    mock_wa.send_template.return_value.success = False
    handled = await service.try_handle_inbound_debtor_message(mock_db, parsed)
    assert handled is True

    # Verifies merchant was notified on WhatsApp to verify before closing debt
    assert mock_wa.send_text.call_count >= 2
    merchant_call = mock_wa.send_text.call_args_list[1]
    assert merchant_call[0][0] == "2348000000001"
    assert "Customer Payment Notification" in merchant_call[0][1]
    assert "paid Emeka" in merchant_call[0][1]


# 6. DETAILS PAGE ENDPOINT SECURITY & RENDERING TESTS
@pytest.mark.asyncio
async def test_details_page_rendering_and_security_headers():
    from fastapi import FastAPI
    from app.api.routes import debt_details

    app = FastAPI()
    app.include_router(debt_details.router)
    debt_id = uuid4()
    token = generate_debt_token(debt_id, expires_in_seconds=3600)

    biz_id = uuid4()
    cust_id = uuid4()
    debt = Debt(id=debt_id, business_id=biz_id, customer_id=cust_id, amount=45000, due_date=datetime.now(UTC), status="outstanding")
    biz = Business(id=biz_id, name="Victory Chemist", settings={"bank_name": "Zenith", "account_number": "1234567890", "enable_public_bank_details": True})
    cust = Customer(id=cust_id, business_id=biz_id, name="Nkechi", phone_number="08031234567")

    from app.api.deps import get_db_session

    mock_session = AsyncMock()
    async def mock_get(model, pk):
        if model is Debt: return debt
        if model is Business: return biz
        if model is Customer: return cust
        return None
    mock_session.get.side_effect = mock_get

    # No revoked consent
    mock_res = MagicMock()
    mock_res.scalar_one_or_none.return_value = None
    mock_session.execute.return_value = mock_res

    async def override_db():
        yield mock_session

    app.dependency_overrides[get_db_session] = override_db

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        res = await client.get(f"/r/{token}")

        assert res.status_code == 200
        # CRITICAL SECURITY HEADERS: Block indexing
        assert res.headers.get("X-Robots-Tag") == "noindex, nofollow"
        html = res.text
        assert '<meta name="robots" content="noindex, nofollow">' in html

        # Verify contents
        assert "Victory Chemist" in html
        assert "45,000" in html
        # Masked phone
        assert "+234 ••• ••• 4567" in html
        # Bank details
        assert "Zenith — 1234567890" in html
        # Buttons
        assert "I Have Paid" in html
        assert "Need More Time" in html
        assert "This is not me" in html
