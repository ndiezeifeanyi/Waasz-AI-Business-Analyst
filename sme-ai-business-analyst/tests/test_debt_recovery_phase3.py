"""
Phase 3 Tests: The Automated Reminder Ladder, Merchant Controls & Smart Escalation Menu
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4
import pytest

from app.models.business import Business
from app.models.customer import Customer
from app.models.debt import Debt
from app.models.debt_recovery import DebtEvent, DebtorConsent, DebtorMessage, ReminderState
from app.models.user import User
from app.services.command_router import CommandRouter
from app.services.debtor_send_service import WAT_TZ
from app.services.reminder_ladder_service import LADDER_SCHEDULE, ReminderLadderService


# -----------------------------------------------------------------------------
# 1. REMINDER LADDER ENGINE TESTS
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_reminder_ladder_day_matching():
    """Verify correct templates correspond to exact offsets."""
    assert LADDER_SCHEDULE[0]["day_offset"] == -2
    assert LADDER_SCHEDULE[0]["template"] == "iou_due_soon_v2"

    assert LADDER_SCHEDULE[1]["day_offset"] == 0
    assert LADDER_SCHEDULE[1]["template"] == "iou_due_today_v2"

    assert LADDER_SCHEDULE[2]["day_offset"] == 3
    assert LADDER_SCHEDULE[2]["template"] == "iou_overdue_v2"

    assert LADDER_SCHEDULE[3]["day_offset"] == 7
    assert LADDER_SCHEDULE[3]["template"] == "iou_overdue_v2"

    assert LADDER_SCHEDULE[4]["day_offset"] == 10
    assert LADDER_SCHEDULE[4]["template"] == "iou_final_notice_v2"


@pytest.mark.asyncio
async def test_reminder_ladder_daily_execution_dispatches_due_today():
    """Verify run_daily_ladder_check dispatches iou_due_today_v2 when due today."""
    mock_wa = AsyncMock()
    mock_sender = AsyncMock()
    mock_sender.send_debtor_notice.return_value = (True, "sent", {"id": "wamid.123"})
    service = ReminderLadderService(whatsapp=mock_wa, debtor_sender=mock_sender)

    mock_db = AsyncMock()
    biz_id = uuid4()
    cust_id = uuid4()
    debt_id = uuid4()

    # Debt due today in WAT
    now_wat = datetime.now(WAT_TZ)
    debt = Debt(
        id=debt_id,
        business_id=biz_id,
        customer_id=cust_id,
        amount=30000,
        due_date=now_wat,
        status="outstanding",
    )
    cust = Customer(id=cust_id, business_id=biz_id, name="Emeka", phone_number="+2348031234567")
    biz = Business(id=biz_id, name="Emeka Stores", phone_number="+2348000000001", settings={"reminders_enabled": True})
    consent = DebtorConsent(id=uuid4(), business_id=biz_id, debt_id=debt_id, debtor_phone="+2348031234567")
    rem_state = ReminderState(business_id=biz_id, debt_id=debt_id, stage="due_soon")

    # Mock candidate query
    mock_query_res = MagicMock()
    mock_query_res.all.return_value = [(debt, cust, biz, consent, rem_state)]
    mock_db.execute.return_value = mock_query_res

    # Mock recent message check (none sent yet for due_today)
    mock_recent_res = MagicMock()
    mock_recent_res.scalar_one_or_none.return_value = None
    mock_db.execute.side_effect = [mock_query_res, mock_recent_res]

    stats = await service.run_daily_ladder_check(mock_db)

    assert stats["evaluated"] == 1
    assert stats["dispatched"] == 1
    mock_sender.send_debtor_notice.assert_called_once_with(
        db=mock_db,
        business_id=biz_id,
        debt_id=debt_id,
        notice_type="due_today",
        template_name="iou_due_today_v2",
    )
    assert rem_state.stage == "due_today"


@pytest.mark.asyncio
async def test_reminder_ladder_skips_when_shop_reminders_disabled():
    """Verify ladder skips when business setting reminders_enabled is False."""
    mock_wa = AsyncMock()
    mock_sender = AsyncMock()
    service = ReminderLadderService(whatsapp=mock_wa, debtor_sender=mock_sender)

    mock_db = AsyncMock()
    biz_id = uuid4()
    now_wat = datetime.now(WAT_TZ)
    debt = Debt(id=uuid4(), business_id=biz_id, customer_id=uuid4(), amount=30000, due_date=now_wat, status="outstanding")
    cust = Customer(id=uuid4(), business_id=biz_id, name="Emeka", phone_number="+2348031234567")
    # Reminders disabled
    biz = Business(id=biz_id, name="Emeka Stores", phone_number="+2348000000001", settings={"reminders_enabled": False})
    consent = DebtorConsent(id=uuid4(), business_id=biz_id, debt_id=debt.id, debtor_phone="+2348031234567")
    rem_state = ReminderState(business_id=biz_id, debt_id=debt.id)

    mock_query_res = MagicMock()
    mock_query_res.all.return_value = [(debt, cust, biz, consent, rem_state)]
    mock_db.execute.return_value = mock_query_res

    stats = await service.run_daily_ladder_check(mock_db)
    assert stats["evaluated"] == 1
    assert stats["skipped_shop_disabled"] == 1
    assert stats["dispatched"] == 0
    mock_sender.send_debtor_notice.assert_not_called()


@pytest.mark.asyncio
async def test_reminder_ladder_skips_when_paused_until_future():
    """Verify ladder skips debtor when reminder_state.paused_until is in the future."""
    mock_wa = AsyncMock()
    mock_sender = AsyncMock()
    service = ReminderLadderService(whatsapp=mock_wa, debtor_sender=mock_sender)

    mock_db = AsyncMock()
    biz_id = uuid4()
    now_wat = datetime.now(WAT_TZ)
    debt = Debt(id=uuid4(), business_id=biz_id, customer_id=uuid4(), amount=30000, due_date=now_wat, status="outstanding")
    cust = Customer(id=uuid4(), business_id=biz_id, name="Emeka", phone_number="+2348031234567")
    biz = Business(id=biz_id, name="Emeka Stores", phone_number="+2348000000001", settings={"reminders_enabled": True})
    consent = DebtorConsent(id=uuid4(), business_id=biz_id, debt_id=debt.id, debtor_phone="+2348031234567")

    # Paused until 3 days in future
    paused_until = datetime.now(UTC) + timedelta(days=3)
    rem_state = ReminderState(business_id=biz_id, debt_id=debt.id, paused_until=paused_until)

    mock_query_res = MagicMock()
    mock_query_res.all.return_value = [(debt, cust, biz, consent, rem_state)]
    mock_db.execute.return_value = mock_query_res

    stats = await service.run_daily_ladder_check(mock_db)
    assert stats["evaluated"] == 1
    assert stats["skipped_paused"] == 1
    assert stats["dispatched"] == 0
    mock_sender.send_debtor_notice.assert_not_called()


@pytest.mark.asyncio
async def test_reminder_ladder_escalates_on_day_21():
    """Verify ladder triggers merchant escalation card on day 21 unpaid."""
    mock_wa = AsyncMock()
    mock_sender = AsyncMock()
    service = ReminderLadderService(whatsapp=mock_wa, debtor_sender=mock_sender)

    mock_db = AsyncMock()
    biz_id = uuid4()
    # 22 days past due date
    past_due_date = datetime.now(WAT_TZ) - timedelta(days=22)
    debt = Debt(id=uuid4(), business_id=biz_id, customer_id=uuid4(), amount=45000, due_date=past_due_date, status="outstanding")
    cust = Customer(id=uuid4(), business_id=biz_id, name="Emeka Okeke", phone_number="+2348031234567")
    biz = Business(id=biz_id, name="Emeka Stores", phone_number="+2348000000001", settings={"reminders_enabled": True})
    consent = DebtorConsent(id=uuid4(), business_id=biz_id, debt_id=debt.id, debtor_phone="+2348031234567")
    rem_state = ReminderState(business_id=biz_id, debt_id=debt.id, stage="final_notice")

    mock_query_res = MagicMock()
    mock_query_res.all.return_value = [(debt, cust, biz, consent, rem_state)]
    mock_db.execute.return_value = mock_query_res

    with patch.object(service, "send_merchant_escalation_card", AsyncMock()) as mock_esc:
        stats = await service.run_daily_ladder_check(mock_db)
        assert stats["escalated"] == 1
        mock_esc.assert_called_once_with(
            mock_db,
            business=biz,
            debt=debt,
            customer=cust,
            reason="day_14_unpaid",
        )
        assert rem_state.stage == "escalated"


# -----------------------------------------------------------------------------
# 2. MERCHANT COMMAND ROUTER TESTS (PLAIN WORDS: status, pause, resume, reminders)
# -----------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_command_status_alone_returns_portfolio_overview():
    """Verify 'status' alone returns portfolio summary."""
    mock_wa = AsyncMock()
    router = CommandRouter(whatsapp=mock_wa)
    mock_db = AsyncMock()

    biz = Business(id=uuid4(), name="Victory Chemist", phone_number="+2348000000001", settings={"reminders_enabled": True})
    user = User(id=uuid4(), business_id=biz.id, phone_number="+2348000000001")

    # Mock status overview from ladder service
    with patch("app.services.reminder_ladder_service.ReminderLadderService.get_status_overview", AsyncMock(return_value="📊 Total Outstanding: ₦50,000")) as mock_overview:
        handled = await router.handle_command(
            db=mock_db,
            business=biz,
            user=user,
            phone="+2348000000001",
            source_text="status",
        )
        assert handled is True
        mock_overview.assert_called_once_with(mock_db, biz)
        mock_wa.send_text.assert_called_once_with("+2348000000001", "📊 Total Outstanding: ₦50,000")


@pytest.mark.asyncio
async def test_command_status_with_customer_name():
    """Verify 'status Emeka' returns specific customer status."""
    mock_wa = AsyncMock()
    router = CommandRouter(whatsapp=mock_wa)
    mock_db = AsyncMock()

    biz = Business(id=uuid4(), name="Victory Chemist", phone_number="+2348000000001")
    user = User(id=uuid4(), business_id=biz.id, phone_number="+2348000000001")

    with patch("app.services.reminder_ladder_service.ReminderLadderService.get_customer_status", AsyncMock(return_value="📋 Debtor Status: Emeka\n• Amount: ₦45,000")) as mock_cust_status:
        handled = await router.handle_command(
            db=mock_db,
            business=biz,
            user=user,
            phone="+2348000000001",
            source_text="status Emeka",
        )
        assert handled is True
        mock_cust_status.assert_called_once_with(mock_db, biz, "emeka")
        mock_wa.send_text.assert_called_once()


@pytest.mark.asyncio
async def test_command_pause_and_resume_customer():
    """Verify 'pause Emeka' and 'resume Emeka' trigger corresponding methods."""
    mock_wa = AsyncMock()
    router = CommandRouter(whatsapp=mock_wa)
    mock_db = AsyncMock()

    biz = Business(id=uuid4(), name="Victory Chemist", phone_number="+2348000000001")
    user = User(id=uuid4(), business_id=biz.id, phone_number="+2348000000001")

    with patch("app.services.reminder_ladder_service.ReminderLadderService.pause_reminders_for_customer", AsyncMock(return_value="⏸️ Paused for Emeka")) as mock_pause, \
         patch("app.services.reminder_ladder_service.ReminderLadderService.resume_reminders_for_customer", AsyncMock(return_value="▶️ Resumed for Emeka")) as mock_resume:

        # Test pause Emeka
        handled_pause = await router.handle_command(
            db=mock_db,
            business=biz,
            user=user,
            phone="+2348000000001",
            source_text="pause Emeka",
        )
        assert handled_pause is True
        mock_pause.assert_called_once_with(mock_db, biz, "emeka")

        # Test resume Emeka
        handled_resume = await router.handle_command(
            db=mock_db,
            business=biz,
            user=user,
            phone="+2348000000001",
            source_text="resume Emeka",
        )
        assert handled_resume is True
        mock_resume.assert_called_once_with(mock_db, biz, "emeka")


@pytest.mark.asyncio
async def test_command_reminders_on_off_toggle():
    """Verify 'reminders off' and 'reminders on' toggle reminders shop-wide."""
    mock_wa = AsyncMock()
    router = CommandRouter(whatsapp=mock_wa)
    mock_db = AsyncMock()

    biz = Business(id=uuid4(), name="Victory Chemist", phone_number="+2348000000001")
    user = User(id=uuid4(), business_id=biz.id, phone_number="+2348000000001")

    with patch("app.services.reminder_ladder_service.ReminderLadderService.toggle_shop_reminders", AsyncMock(return_value="Reminders toggled")) as mock_toggle:
        # Off
        await router.handle_command(db=mock_db, business=biz, user=user, phone="+2348000000001", source_text="reminders off")
        mock_toggle.assert_called_with(mock_db, biz, enabled=False)

        # On
        await router.handle_command(db=mock_db, business=biz, user=user, phone="+2348000000001", source_text="reminders on")
        mock_toggle.assert_called_with(mock_db, biz, enabled=True)


@pytest.mark.asyncio
async def test_quick_menu_contains_cmd_status_within_10_rows_limit():
    """Verify quick menu list drawer includes cmd_status and strictly satisfies Meta <= 10 rows limit."""
    mock_wa = AsyncMock()
    router = CommandRouter(whatsapp=mock_wa)
    mock_db = AsyncMock()

    biz = Business(id=uuid4(), name="Victory Chemist", phone_number="+2348000000001")
    user = User(id=uuid4(), business_id=biz.id, phone_number="+2348000000001")

    await router._send_quick_menu(mock_db, biz, user, "+2348000000001")

    mock_wa.send_interactive_list.assert_called_once()
    call_kwargs = mock_wa.send_interactive_list.call_args.kwargs
    sections = call_kwargs.get("sections")
    if not sections and len(mock_wa.send_interactive_list.call_args[0]) > 2:
        sections = mock_wa.send_interactive_list.call_args[0][2]

    # Calculate total rows across all sections
    total_rows = sum(len(s.get("rows", [])) for s in sections)
    assert total_rows <= 10, f"Exceeded Meta 10-row limit: {total_rows} rows"

    # Verify cmd_status is present
    all_row_ids = [r["id"] for s in sections for r in s.get("rows", [])]
    assert "cmd_status" in all_row_ids
