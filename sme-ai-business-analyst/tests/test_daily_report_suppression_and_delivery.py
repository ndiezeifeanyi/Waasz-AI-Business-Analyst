from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4
import pytest
from app.models.activity import Activity
from app.models.business import Business
from app.models.message import WhatsAppMessage
from app.models.user import User
from app.services.unified_report_service import UnifiedReportService
from app.services.whatsapp_client import WhatsAppSendResult


@pytest.mark.asyncio
async def test_daily_9pm_summary_works_when_user_records_transaction_today():
    """
    Confirm that when a user records a transaction today:
    - user.last_inbound_at is within 24 hours.
    - 9pm daily summary sends the full rich report via send_text (not a template).
    """
    now = datetime.now(UTC)
    user_id = uuid4()
    biz_id = uuid4()

    user = User(
        id=user_id,
        business_id=biz_id,
        phone_number="+2348011223344",
        display_name="Amaka Store",
        role="owner",
        niche="sme_owner",
        is_active=True,
        last_inbound_at=now - timedelta(hours=2),  # messaged today to record transaction
    )

    activity = Activity(
        id=uuid4(),
        business_id=biz_id,
        user_id=user_id,
        activity_type="sale",
        title="Sold 3 Bags of Rice ₦105,000",
        occurred_at=now - timedelta(hours=2),
    )

    mock_db = AsyncMock()
    # Mock db.get(User)
    mock_db.get.return_value = user

    # Mock execute results
    async def mock_execute(stmt):
        mock_result = MagicMock()
        stmt_str = str(stmt).lower()
        if "from activities" in stmt_str and "occurred_at <" not in stmt_str:
            # Current period activities
            mock_result.scalars.return_value.all.return_value = [activity]
        elif "from user_goals" in stmt_str:
            mock_result.scalars.return_value.all.return_value = []
        elif "from members" in stmt_str:
            mock_result.scalars.return_value.first.return_value = None
        else:
            mock_result.scalars.return_value.all.return_value = []
            mock_result.scalars.return_value.first.return_value = None
        return mock_result

    mock_db.execute = AsyncMock(side_effect=mock_execute)
    mock_db.scalar = AsyncMock(return_value=1)

    mock_whatsapp = MagicMock()
    mock_whatsapp.send_text = AsyncMock(return_value=WhatsAppSendResult(message_id="msg_9pm_ok"))
    mock_whatsapp.send_template = AsyncMock()

    mock_ledger = MagicMock()
    mock_ledger.record_outbound_message = AsyncMock()

    service = UnifiedReportService(whatsapp=mock_whatsapp, ledger=mock_ledger)
    service._synthesize_report = AsyncMock(return_value="📊 Daily Highlights: ₦105,000 Sales")

    res = await service.generate_and_deliver_report(mock_db, user_id, cadence="daily")

    assert res["status"] == "sent"
    assert res["within_24h"] is True
    # Confirm send_text was invoked with the synthesized report
    mock_whatsapp.send_text.assert_awaited_once_with(
        "+2348011223344", "📊 Daily Highlights: ₦105,000 Sales"
    )
    # Confirm send_template was NEVER invoked
    mock_whatsapp.send_template.assert_not_awaited()


@pytest.mark.asyncio
async def test_daily_9pm_summary_suppressed_if_6pm_nudge_already_sent():
    """
    Option 1: If user is outside 24h window and already received 6pm reconciliation nudge today,
    suppress the duplicate 9pm template.
    """
    now = datetime.now(UTC)
    user_id = uuid4()
    biz_id = uuid4()

    user = User(
        id=user_id,
        business_id=biz_id,
        phone_number="+2348011223344",
        display_name="Amaka Store",
        role="owner",
        niche="sme_owner",
        is_active=True,
        last_inbound_at=now - timedelta(hours=36),  # Outside 24h
    )

    mock_db = AsyncMock()
    mock_db.get.return_value = user

    async def mock_execute(stmt):
        mock_result = MagicMock()
        stmt_str = str(stmt).lower()
        if "from whatsapp_messages" in stmt_str:
            # Already received 6pm reconciliation nudge
            mock_result.scalars.return_value.first.return_value = uuid4()
        elif "from activities" in stmt_str:
            mock_result.scalars.return_value.all.return_value = []
        elif "from members" in stmt_str:
            mock_result.scalars.return_value.first.return_value = None
        else:
            mock_result.scalars.return_value.all.return_value = []
            mock_result.scalars.return_value.first.return_value = None
        return mock_result

    mock_db.execute = AsyncMock(side_effect=mock_execute)
    mock_db.scalar = AsyncMock(return_value=1)  # Lifetime transactions exist

    mock_whatsapp = MagicMock()
    mock_whatsapp.send_text = AsyncMock()
    mock_whatsapp.send_template = AsyncMock()

    mock_ledger = MagicMock()
    service = UnifiedReportService(whatsapp=mock_whatsapp, ledger=mock_ledger)
    service._synthesize_report = AsyncMock(return_value="Dummy report")

    res = await service.generate_and_deliver_report(mock_db, user_id, cadence="daily")

    assert res["status"] == "skipped"
    assert res["reason"] == "suppressed_duplicate_nudge_template"
    # Neither send_text nor send_template was called
    mock_whatsapp.send_text.assert_not_awaited()
    mock_whatsapp.send_template.assert_not_awaited()


@pytest.mark.asyncio
async def test_daily_9pm_summary_uses_dedicated_template_when_not_suppressed():
    """
    Option 2: If user is outside 24h window and was NOT nudged at 6pm,
    dispatch dedicated daily_business_summary_v1 template.
    """
    now = datetime.now(UTC)
    user_id = uuid4()
    biz_id = uuid4()

    user = User(
        id=user_id,
        business_id=biz_id,
        phone_number="+2348011223344",
        display_name="Amaka Store",
        role="owner",
        niche="sme_owner",
        is_active=True,
        last_inbound_at=now - timedelta(hours=36),  # Outside 24h
    )

    mock_db = AsyncMock()
    mock_db.get.return_value = user

    async def mock_execute(stmt):
        mock_result = MagicMock()
        stmt_str = str(stmt).lower()
        if "from whatsapp_messages" in stmt_str:
            # NOT nudged
            mock_result.scalars.return_value.first.return_value = None
        elif "from activities" in stmt_str:
            mock_result.scalars.return_value.all.return_value = []
        elif "from members" in stmt_str:
            mock_result.scalars.return_value.first.return_value = None
        else:
            mock_result.scalars.return_value.all.return_value = []
            mock_result.scalars.return_value.first.return_value = None
        return mock_result

    mock_db.execute = AsyncMock(side_effect=mock_execute)
    mock_db.scalar = AsyncMock(return_value=1)

    mock_whatsapp = MagicMock()
    mock_whatsapp.send_template = AsyncMock(return_value=WhatsAppSendResult(message_id="tmpl_9pm_ok"))

    mock_ledger = MagicMock()
    mock_ledger.record_outbound_message = AsyncMock()

    service = UnifiedReportService(whatsapp=mock_whatsapp, ledger=mock_ledger)
    service._synthesize_report = AsyncMock(return_value="Dummy report")

    res = await service.generate_and_deliver_report(mock_db, user_id, cadence="daily")

    assert res["status"] == "sent"
    assert res["within_24h"] is False
    # Check that dedicated template was called
    called_template = mock_whatsapp.send_template.await_args.kwargs.get("template_name")
    assert called_template == "daily_business_summary_v1"


if __name__ == "__main__":
    import asyncio
    print("Running test 1...")
    asyncio.run(test_daily_9pm_summary_works_when_user_records_transaction_today())
    print("Running test 2...")
    asyncio.run(test_daily_9pm_summary_suppressed_if_6pm_nudge_already_sent())
    print("Running test 3...")
    asyncio.run(test_daily_9pm_summary_uses_dedicated_template_when_not_suppressed())
    print("SUCCESS: All 3 tests passed!")

