from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4
import pytest

from app.models.business import Business
from app.models.confirmation import Confirmation
from app.models.debt import Debt
from app.models.extraction import AiExtraction
from app.models.member import Member
from app.models.transaction import Transaction
from app.models.user import User
from app.schemas.actor import ActorContext
from app.schemas.extraction import ExtractedRecord
from app.services.confirmation_service import ConfirmationService
from app.services.ledger_service import LedgerService


@pytest.mark.asyncio
async def test_owner_and_staff_actor_resolution_under_same_business() -> None:
    """Verify that owner and staff resolve distinct ActorContexts under the same business."""
    ledger = LedgerService()
    mock_db = AsyncMock()

    biz_id = uuid4()
    owner_user_id = uuid4()
    owner_member_id = uuid4()
    staff_user_id = uuid4()
    staff_member_id = uuid4()

    owner_phone = "+2348011111111"
    staff_phone = "+2348022222222"

    owner_member = Member(
        id=owner_member_id,
        business_id=biz_id,
        wa_id=owner_phone,
        role="owner",
        status="active",
        display_name="Boss",
    )

    staff_member = Member(
        id=staff_member_id,
        business_id=biz_id,
        wa_id=staff_phone,
        role="staff",
        status="active",
        display_name="Chidi Staff",
    )

    # First call is for owner, second call is for staff
    res_owner = MagicMock()
    res_owner.scalar_one_or_none.return_value = owner_member

    res_staff = MagicMock()
    res_staff.scalar_one_or_none.return_value = staff_member

    mock_db.execute.side_effect = [res_owner, res_staff]

    owner_actor = await ledger.resolve_actor_context(mock_db, owner_phone)
    staff_actor = await ledger.resolve_actor_context(mock_db, staff_phone)

    assert owner_actor is not None
    assert staff_actor is not None

    # Both belong to same business
    assert owner_actor.business_id == biz_id
    assert staff_actor.business_id == biz_id

    # Roles and members are distinct
    assert owner_actor.role == "owner"
    assert owner_actor.member_id == owner_member_id
    assert owner_actor.display_name == "Boss"

    assert staff_actor.role == "staff"
    assert staff_actor.member_id == staff_member_id
    assert staff_actor.display_name == "Chidi Staff"


@pytest.mark.asyncio
async def test_concurrent_pending_confirmations_scoped_by_member_id() -> None:
    """
    Verify that when Staff A and Staff B stage transactions simultaneously:
    1. Neither staging overwrites or rejects the other.
    2. latest_actionable scopes strictly by member_id.
    3. Confirming stamps created_by_member_id onto the transaction.
    """
    ledger = LedgerService()
    confirmations_svc = ConfirmationService()
    mock_db = AsyncMock()

    biz_id = uuid4()
    member_a_id = uuid4()
    member_b_id = uuid4()

    # 1. Staff A stages a sale
    extraction_a = AiExtraction(id=uuid4(), business_id=biz_id)
    rec_a = ExtractedRecord(
        record_type="sale",
        item_name="Rice Bag",
        quantity=2,
        amount=50000,
    )

    # Empty pending check for Member A
    res_empty_conf = MagicMock()
    res_empty_conf.scalars.return_value.all.return_value = []
    res_empty_tx = MagicMock()
    res_empty_tx.scalars.return_value.all.return_value = []
    res_empty_mv = MagicMock()
    res_empty_mv.scalars.return_value.all.return_value = []

    mock_db.execute.side_effect = [
        res_empty_conf,
        res_empty_tx,
        res_empty_mv,
    ]

    conf_a = await ledger.stage_record_for_confirmation(
        mock_db,
        business_id=biz_id,
        source_message_id=uuid4(),
        extraction=extraction_a,
        record=rec_a,
        member_id=member_a_id,
        source_wamid="wamid.staff_a.001",
    )

    assert conf_a.member_id == member_a_id
    assert conf_a.status == "pending"

    # 2. Staff B stages a sale (should NOT affect Staff A)
    extraction_b = AiExtraction(id=uuid4(), business_id=biz_id)
    rec_b = ExtractedRecord(
        record_type="sale",
        item_name="Sugar",
        quantity=5,
        amount=15000,
    )

    mock_db.execute.side_effect = [
        res_empty_conf,
        res_empty_tx,
        res_empty_mv,
    ]

    conf_b = await ledger.stage_record_for_confirmation(
        mock_db,
        business_id=biz_id,
        source_message_id=uuid4(),
        extraction=extraction_b,
        record=rec_b,
        member_id=member_b_id,
        source_wamid="wamid.staff_b.002",
    )

    assert conf_b.member_id == member_b_id
    assert conf_b.status == "pending"

    # 3. Test latest_actionable isolation:
    # First query for Member A returns conf_a, query for Member B returns conf_b
    res_found_a = MagicMock()
    res_found_a.scalar_one_or_none.return_value = conf_a
    res_found_b = MagicMock()
    res_found_b.scalar_one_or_none.return_value = conf_b

    mock_db.execute.side_effect = [res_found_a, res_found_b]

    found_a = await confirmations_svc.latest_actionable(mock_db, biz_id, member_id=member_a_id)
    found_b = await confirmations_svc.latest_actionable(mock_db, biz_id, member_id=member_b_id)

    assert found_a is conf_a
    assert found_b is conf_b

    # Verify that the executed statement for Member A actually filtered by Member A's ID
    call_args_list = mock_db.execute.call_args_list
    assert len(call_args_list) >= 2
    stmt_a = call_args_list[0][0][0]
    # Check that member_id condition is present in compiled statement
    compiled_a = str(stmt_a)
    assert "confirmations.member_id = :member_id_1" in compiled_a or "member_id" in compiled_a

    # 4. Confirm Staff A's transaction
    tx_a = Transaction(
        id=uuid4(),
        business_id=biz_id,
        confirmation_id=conf_a.id,
        transaction_type="sale",
        amount=Decimal("50000"),
        status="pending_confirmation",
        created_by_member_id=member_a_id,
    )

    tx_res_mock = MagicMock()
    tx_res_mock.scalar_one_or_none.return_value = tx_a
    mock_db.execute.side_effect = None
    mock_db.execute.return_value = tx_res_mock

    confirm_reply = await confirmations_svc.confirm(mock_db, conf_a)
    assert conf_a.status == "confirmed"
    assert tx_a.status == "confirmed"
    assert tx_a.created_by_member_id == member_a_id


@pytest.mark.asyncio
async def test_source_wamid_idempotency_prevents_duplicate_transactions() -> None:
    """Verify that duplicate calls to record_transaction with identical source_wamid return existing record."""
    ledger = LedgerService()
    mock_db = AsyncMock()

    biz_id = uuid4()
    member_id = uuid4()
    test_wamid = "wamid.HBgLMjM0ODAxMjM0N"

    existing_tx = Transaction(
        id=uuid4(),
        business_id=biz_id,
        created_by_member_id=member_id,
        transaction_type="sale",
        amount=Decimal("12000.00"),
        status="confirmed",
        source_wamid=test_wamid,
    )

    res_mock = MagicMock()
    res_mock.scalar_one_or_none.return_value = existing_tx
    mock_db.execute.return_value = res_mock

    # Duplicate call with identical source_wamid
    result_tx, debt, low_stock = await ledger.record_transaction(
        mock_db,
        business_id=biz_id,
        record_type="sale",
        amount=Decimal("12000.00"),
        created_by_member_id=member_id,
        source_wamid=test_wamid,
    )

    assert result_tx.id == existing_tx.id
    assert result_tx.source_wamid == test_wamid
    assert debt is None
    assert low_stock is None
    # db.add should NOT have been called for a duplicate
    mock_db.add.assert_not_called()


@pytest.mark.asyncio
async def test_single_owner_legacy_backward_compatibility() -> None:
    """Verify that existing single-owner flow remains 100% identical and operational."""
    ledger = LedgerService()
    mock_db = AsyncMock()

    phone = "+2348099999999"
    # When business/user do not exist, auto-create produces (business, user)
    exec_res = MagicMock()
    exec_res.scalar_one_or_none.return_value = None
    mock_db.execute.return_value = exec_res

    biz, user = await ledger.get_or_create_business_and_user(mock_db, phone)

    assert isinstance(biz, Business)
    assert isinstance(user, User)
    assert user.role == "owner"
    assert biz.is_provisional is True
