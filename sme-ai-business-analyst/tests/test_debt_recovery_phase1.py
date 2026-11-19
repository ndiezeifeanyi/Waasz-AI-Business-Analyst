from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4
import pytest

from app.core.exceptions import SecurityError
from app.models.business import Business
from app.models.customer import Customer
from app.models.debt import Debt
from app.models.debt_recovery import (
    DebtEvent,
    DebtorConsent,
    MessageSuppression,
    ReminderState,
)
from app.models.transaction import Transaction
from app.services.debt_recovery_service import DebtRecoveryService
from app.utils.phone import (
    hash_phone_for_suppression,
    normalize_e164_phone,
    validate_nigerian_phone,
)


def test_phone_normalization_and_validation():
    # Valid Nigerian formats
    valid_numbers = [
        ("08031234567", "+2348031234567"),
        ("2348031234567", "+2348031234567"),
        ("+2348031234567", "+2348031234567"),
        ("07012345678", "+2347012345678"),
        ("09099998888", "+2349099998888"),
        ("08123456789", "+2348123456789"),
        ("09123456789", "+2349123456789"),
    ]
    for raw, expected in valid_numbers:
        is_valid, e164, err = validate_nigerian_phone(raw)
        assert is_valid is True, f"Expected {raw} to be valid, got {err}"
        assert e164 == expected
        assert normalize_e164_phone(raw) == expected

    # Invalid formats
    invalid_numbers = [
        "",
        "not_a_phone",
        "0803",  # too short
        "0803123456789",  # too long
        "01234567890",  # invalid prefix 01
        "06012345678",  # invalid prefix 06
        "12345",
    ]
    for raw in invalid_numbers:
        is_valid, e164, err = validate_nigerian_phone(raw)
        assert is_valid is False, f"Expected {raw} to be invalid"
        assert e164 == ""


def test_suppression_phone_hashing_is_deterministic_and_one_way():
    phone_a = "08031234567"
    phone_b = "+2348031234567"
    phone_c = "2348031234567"
    hash_a = hash_phone_for_suppression(phone_a)
    hash_b = hash_phone_for_suppression(phone_b)
    hash_c = hash_phone_for_suppression(phone_c)
    assert hash_a == hash_b == hash_c
    assert len(hash_a) == 64
    # Ensure raw phone is NOT in hash string
    assert "0803" not in hash_a
    assert "234" not in hash_a


@pytest.mark.asyncio
async def test_tenant_isolation_on_debt_consent():
    """Merchant B cannot record or modify consent on Merchant A's debt."""
    service = DebtRecoveryService()
    mock_db = AsyncMock()

    biz_a = uuid4()
    biz_b = uuid4()
    debt_a = Debt(id=uuid4(), business_id=biz_a, amount=50000)

    mock_db.get.return_value = debt_a

    # Merchant B tries to record consent for Debt A
    with pytest.raises(SecurityError) as exc_info:
        await service.record_merchant_consent(
            mock_db,
            business_id=biz_b,
            debt_id=debt_a.id,
            debtor_phone="08031234567",
        )
    assert "Tenant isolation violation" in str(exc_info.value)


@pytest.mark.asyncio
async def test_consent_is_required_before_any_automated_send():
    """A debt without consent returns can_send = False (no_consent)."""
    service = DebtRecoveryService()
    mock_db = AsyncMock()

    biz_id = uuid4()
    debt_id = uuid4()

    # Query for active consent returns None
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = None
    mock_db.execute.return_value = result_mock

    can_send, reason = await service.can_send_to_debtor(
        mock_db, business_id=biz_id, debt_id=debt_id, debtor_phone="+2348031234567"
    )
    assert can_send is False
    assert reason == "no_consent"


@pytest.mark.asyncio
async def test_global_suppression_blocks_sends_across_all_merchants():
    """
    When a debtor opts out, their phone is globally suppressed (hash only).
    Both Merchant A and Merchant B are blocked from sending.
    """
    service = DebtRecoveryService()
    mock_db = AsyncMock()

    debtor_phone = "08031234567"
    phone_hash = hash_phone_for_suppression(debtor_phone)

    biz_a = uuid4()
    biz_b = uuid4()
    debt_a = uuid4()
    debt_b = uuid4()

    # Mock consent present for both merchants
    consent_a = DebtorConsent(
        id=uuid4(), business_id=biz_a, debt_id=debt_a, debtor_phone="+2348031234567"
    )
    consent_b = DebtorConsent(
        id=uuid4(), business_id=biz_b, debt_id=debt_b, debtor_phone="+2348031234567"
    )

    # 1. Suppress phone
    mock_db.get.return_value = None
    supp = await service.suppress_phone(mock_db, debtor_phone, reason="stop_keyword")
    assert supp.phone_hash == phone_hash
    assert supp.reason == "stop_keyword"

    # 2. Check send for Merchant A when suppressed
    res_consent = MagicMock()
    res_consent.scalar_one_or_none.return_value = consent_a

    res_suppressed = MagicMock()
    res_suppressed.scalar_one_or_none.return_value = phone_hash

    # Sequence of queries in can_send_to_debtor:
    # 1: consent check, 2: suppression check
    mock_db.execute.side_effect = [res_consent, res_suppressed]

    can_send_a, reason_a = await service.can_send_to_debtor(
        mock_db, business_id=biz_a, debt_id=debt_a, debtor_phone="+2348031234567"
    )
    assert can_send_a is False
    assert reason_a == "suppressed"

    # 3. Check send for Merchant B when suppressed
    res_consent_b = MagicMock()
    res_consent_b.scalar_one_or_none.return_value = consent_b
    res_suppressed_b = MagicMock()
    res_suppressed_b.scalar_one_or_none.return_value = phone_hash
    mock_db.execute.side_effect = [res_consent_b, res_suppressed_b]

    can_send_b, reason_b = await service.can_send_to_debtor(
        mock_db, business_id=biz_b, debt_id=debt_b, debtor_phone="+2348031234567"
    )
    assert can_send_b is False
    assert reason_b == "suppressed"


@pytest.mark.asyncio
async def test_start_by_debtor_lifts_suppression():
    """When debtor texts START, their suppression is lifted."""
    service = DebtRecoveryService()
    mock_db = AsyncMock()

    debtor_phone = "+2348031234567"
    p_hash = hash_phone_for_suppression(debtor_phone)
    existing_supp = MessageSuppression(phone_hash=p_hash, reason="stop_keyword", resumed_at=None)

    mock_db.get.return_value = existing_supp

    resumed = await service.resume_phone_by_debtor(mock_db, debtor_phone)
    assert resumed is True
    assert existing_supp.resumed_at is not None


def test_merchant_cannot_lift_suppression():
    """Hard Rule 3: A merchant CANNOT reverse an opt out."""
    service = DebtRecoveryService()
    biz_id = uuid4()
    with pytest.raises(SecurityError) as exc_info:
        service.resume_phone_by_merchant(biz_id, "08031234567")
    assert "A merchant cannot reverse a debtor's opt-out" in str(exc_info.value)


@pytest.mark.asyncio
async def test_counter_consent_capture_at_sale_confirmation():
    """
    Verifies that when a credit sale is confirmed, Waasz stages the counter consent question:
    '{name} is buying on credit. Does {name} agree to receive the record and payment reminders on WhatsApp? Reply 1 for yes, 2 for no.'
    and does NOT automatically dispatch a notice before consent is answered.
    """
    from app.services.confirmation_service import ConfirmationService
    from app.models.confirmation import Confirmation

    service = ConfirmationService()
    mock_db = AsyncMock()

    biz_id = uuid4()
    cust_id = uuid4()
    tx_id = uuid4()

    biz = Business(id=biz_id, name="Emeka Stores", phone_number="2348000000001", settings={})
    cust = Customer(id=cust_id, business_id=biz_id, name="Soma", phone_number="08031234567")
    tx = Transaction(
        id=tx_id,
        business_id=biz_id,
        customer_id=cust_id,
        is_credit=True,
        amount=50000,
        item_name="Rice",
        transaction_type="sale",
    )

    conf = Confirmation(
        id=uuid4(),
        business_id=biz_id,
        status="pending",
        extracted_record={"record_type": "sale", "is_credit": True, "customer_name": "Soma", "due_date": "2026-11-20"},
    )

    # Database get and select mocks
    async def mock_get(model, pk):
        if model is Customer and pk == cust_id:
            return cust
        if model is Business and pk == biz_id:
            return biz
        return None

    mock_db.get.side_effect = mock_get

    tx_res = MagicMock()
    tx_res.scalars.return_value.all.return_value = [tx]

    debt_res = MagicMock()
    debt_res.scalar_one_or_none.return_value = None

    move_res = MagicMock()
    move_res.scalar_one_or_none.return_value = None

    task_res = MagicMock()
    task_res.scalar_one_or_none.return_value = None

    mock_db.execute.side_effect = [tx_res, debt_res, move_res, task_res]

    with patch("app.services.debt_notifier_service.DebtNotifierService.dispatch_debt_notice") as mock_dispatch:
        response_text = await service.confirm(mock_db, conf)

        # CRITICAL: Verify automated message was NOT dispatched before consent
        mock_dispatch.assert_not_called()

    # Verify counter consent question is present in response
    assert "Soma is buying on credit. Does Soma agree to receive the record and payment reminders on WhatsApp?" in response_text
    assert ("'1 Soma'" in response_text or "YES Soma" in response_text) and "decline" in response_text.lower()

    # Verify pending consent was saved in business settings
    assert "pending_counter_consent" in biz.settings
    assert biz.settings["pending_counter_consent"]["customer_name"] == "Soma"
    assert biz.settings["pending_counter_consent"]["customer_phone"] == "+2348031234567"


@pytest.mark.asyncio
async def test_merchant_replies_yes_records_consent():
    """When merchant replies 1/yes to counter consent, consent is recorded with E.164 phone and audit event."""
    service = DebtRecoveryService()
    mock_db = AsyncMock()

    biz_id = uuid4()
    debt_id = uuid4()
    debt = Debt(id=debt_id, business_id=biz_id, amount=50000)

    mock_db.get.return_value = debt

    res_consent_none = MagicMock()
    res_consent_none.scalar_one_or_none.return_value = None

    res_rem_none = MagicMock()
    res_rem_none.scalar_one_or_none.return_value = None

    mock_db.execute.side_effect = [res_consent_none, res_rem_none]

    consent = await service.record_merchant_consent(
        mock_db,
        business_id=biz_id,
        debt_id=debt_id,
        debtor_phone="08031234567",
        wording_version="counter_v1",
    )

    assert consent.debtor_phone == "+2348031234567"
    assert consent.source == "attested_by_merchant"
    assert consent.wording_version == "counter_v1"
    assert consent.business_id == biz_id


@pytest.mark.asyncio
async def test_merchant_replies_no_declines_consent():
    """When merchant replies 2/no, consent is declined, audit event is recorded with agreed=False, and no consent row is created."""
    service = DebtRecoveryService()
    mock_db = AsyncMock()

    biz_id = uuid4()
    debt_id = uuid4()
    debt = Debt(id=debt_id, business_id=biz_id, amount=50000)

    mock_db.get.return_value = debt

    await service.record_declined_consent(
        mock_db,
        business_id=biz_id,
        debt_id=debt_id,
        debtor_phone="08031234567",
    )

    # Verify debt event was added with agreed=False
    call_args = mock_db.add.call_args[0][0]
    assert isinstance(call_args, DebtEvent)
    assert call_args.type == "consent"
    assert call_args.payload["agreed"] is False
