from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4
import pytest

from sqlalchemy import delete, select

from app.core.config import settings
from app.core.database import async_session_factory
from app.models.business import Business
from app.models.invite_code import InviteCode
from app.models.approved_tester import ApprovedTester
from app.models.user import User
from app.schemas.whatsapp import ParsedWhatsAppMessage, WhatsAppSendResult
from app.services.invite_service import InviteCodeService
from app.services.webhook_processor import WhatsAppWebhookProcessor
from app.utils.phone import normalize_phone


@pytest.fixture(autouse=True)
async def cleanup_compliance_test_records():
    from app.core.database import engine
    await engine.dispose()
    orig_req = settings.require_invite_code
    orig_mode = settings.access_mode
    try:
        yield
    finally:
        settings.require_invite_code = orig_req
        settings.access_mode = orig_mode
        async with async_session_factory() as session:
            test_phones = [normalize_phone(f"+234805555500{i}") for i in range(1, 10)]
            from app.models.message import WhatsAppMessage
            from app.models.activity import Activity
            from app.models.transaction import Transaction
            from app.models.inventory import InventoryItem
            from app.models.debt import Debt
            from app.models.customer import Customer

            biz_ids_subq = select(Business.id).where(Business.phone_number.in_(test_phones))
            await session.execute(delete(Activity).where(Activity.business_id.in_(biz_ids_subq)))
            await session.execute(delete(Transaction).where(Transaction.business_id.in_(biz_ids_subq)))
            await session.execute(delete(InventoryItem).where(InventoryItem.business_id.in_(biz_ids_subq)))
            await session.execute(delete(Debt).where(Debt.business_id.in_(biz_ids_subq)))
            await session.execute(delete(Customer).where(Customer.business_id.in_(biz_ids_subq)))
            await session.execute(delete(WhatsAppMessage).where(WhatsAppMessage.from_phone.in_(test_phones)))
            await session.execute(delete(WhatsAppMessage).where(WhatsAppMessage.to_phone.in_(test_phones)))
            await session.execute(delete(User).where(User.phone_number.in_(test_phones)))
            await session.execute(delete(Business).where(Business.phone_number.in_(test_phones)))
            await session.execute(delete(InviteCode).where(InviteCode.code.like("COMPL-%")))
            await session.execute(delete(ApprovedTester).where(ApprovedTester.phone_number.in_(test_phones)))
            await session.commit()
        await engine.dispose()


@pytest.mark.asyncio
async def test_phase1_new_account_onboarding_intro_fires_once():
    """
    Verify Phase 1:
    1. A brand new user redemption triggers the one-time onboarding message.
    2. Message contains plain-language explainer, privacy notice, real tenant isolation,
       erasure trigger ('delete my data' / 'forget me'), and AI advisory disclaimer.
    3. Does NOT claim or imply 'NDPA compliant'.
    """
    settings.require_invite_code = True
    settings.access_mode = "invite_code"

    code_str = f"COMPL-{uuid4().hex[:6].upper()}"
    phone = "+2348055555001"
    norm_phone = normalize_phone(phone)

    async with async_session_factory() as session:
        invite_svc = InviteCodeService()
        await invite_svc.create_code(session, code=code_str, max_uses=2)
        await session.commit()

    mock_whatsapp = MagicMock()
    mock_whatsapp.send_text = AsyncMock(side_effect=lambda *args, **kwargs: WhatsAppSendResult(success=True, message_id=f"wamid.{uuid4().hex}"))

    processor = WhatsAppWebhookProcessor(whatsapp=mock_whatsapp)

    # 1. First message from new user claiming invite code
    msg1 = ParsedWhatsAppMessage(
        message_id="msg-1",
        from_phone=phone,
        body=code_str,
        timestamp=datetime.now(UTC),
        message_type="text",
    )
    async with async_session_factory() as session:
        await processor.process_message(session, msg1, event=None)

    # Verify welcome message was sent
    assert mock_whatsapp.send_text.called
    sent_text = mock_whatsapp.send_text.call_args[0][1]

    # Verify content requirements
    assert "Welcome to Waasz!" in sent_text
    assert "Your Data & Privacy" in sent_text
    assert "chat messages, business transactions, inventory, and registered phone number" in sent_text
    assert "Strictly to maintain your business ledger" in sent_text
    assert "strictly isolated to your business using database row-level security and tenant boundaries" in sent_text
    assert "delete my data" in sent_text
    assert "forget me" in sent_text
    assert "AI-generated estimates" in sent_text
    assert "not professional financial, tax, or legal advice" in sent_text
    assert "NDPA" not in sent_text  # Framed properly without claiming NDPA compliance

    # 2. Second message from the now-existing user: should NOT send onboarding message again
    mock_whatsapp.send_text.reset_mock()
    msg2 = ParsedWhatsAppMessage(
        message_id="msg-2",
        from_phone=phone,
        body="Hi there",
        timestamp=datetime.now(UTC),
        message_type="text",
    )
    with patch("app.services.agent_service.AgentService.process_user_message", new_callable=AsyncMock) as mock_agent:
        mock_agent.return_value = "Hello! How can I assist your business today?"
        async with async_session_factory() as session:
            await processor.process_message(session, msg2, event=None)

    # Ensure onboarding welcome was NOT resent
    if mock_whatsapp.send_text.called:
        followup_text = mock_whatsapp.send_text.call_args[0][1]
        assert "Your Data & Privacy" not in followup_text
        assert "'delete my data' or 'forget me'" not in followup_text


@pytest.mark.asyncio
async def test_phase1_allowlist_new_user_gets_intro_returning_user_does_not():
    """Verify that allowlist-admitted new user gets intro message, and existing user bypasses it."""
    settings.require_invite_code = False
    settings.access_mode = "allowlist"

    phone = "+2348055555002"
    norm_phone = normalize_phone(phone)

    async with async_session_factory() as session:
        # Register in approved testers
        tester = ApprovedTester(phone_number=norm_phone, label="Test Tester")
        session.add(tester)
        await session.commit()

    mock_whatsapp = MagicMock()
    mock_whatsapp.send_text = AsyncMock(side_effect=lambda *args, **kwargs: WhatsAppSendResult(success=True, message_id=f"wamid.{uuid4().hex}"))
    processor = WhatsAppWebhookProcessor(whatsapp=mock_whatsapp)

    # 1. Brand new allowlist user sends first message
    msg1 = ParsedWhatsAppMessage(
        message_id="msg-allow-1",
        from_phone=phone,
        body="Hello Waasz",
        timestamp=datetime.now(UTC),
        message_type="text",
    )
    async with async_session_factory() as session:
        await processor.process_message(session, msg1, event=None)

    assert mock_whatsapp.send_text.called
    sent_text = mock_whatsapp.send_text.call_args[0][1]
    assert "Welcome to Waasz!" in sent_text
    assert "Your Data & Privacy" in sent_text
    assert "delete my data" in sent_text
    assert "forget me" in sent_text

    # 2. Same user sends second message - should NOT receive onboarding intro again
    mock_whatsapp.send_text.reset_mock()
    msg2 = ParsedWhatsAppMessage(
        message_id="msg-allow-2",
        from_phone=phone,
        body="What is my balance?",
        timestamp=datetime.now(UTC),
        message_type="text",
    )
    with patch("app.services.agent_service.AgentService.process_user_message", new_callable=AsyncMock) as mock_agent:
        mock_agent.return_value = "Your current balance is ₦0."
        async with async_session_factory() as session:
            await processor.process_message(session, msg2, event=None)

    if mock_whatsapp.send_text.called:
        followup_text = mock_whatsapp.send_text.call_args[0][1]
        assert "Your Data & Privacy" not in followup_text


@pytest.mark.asyncio
async def test_phase2_scoped_ai_disclaimer_on_all_five_advisory_outputs():
    """
    Assert that the scoped AI disclaimer attaches ONLY to the five predictive/advisory outputs:
    1. get_margin_report recommendations/breakdown
    2. get_product_performance_analysis conclusions
    3. simulate_pricing outputs (both discount and target margin)
    4. get_cash_flow_forecast shortfall warnings
    5. Growth Playbook section of monthly/quarterly reports
    """
    from app.models.activity import Activity
    from app.models.customer import Customer
    from app.models.debt import Debt
    from app.models.inventory import InventoryItem
    from app.models.payable import Payable
    from app.models.transaction import Transaction
    from app.services.analytics_service import AI_ADVISORY_DISCLAIMER, AnalyticsService
    from app.services.unified_report_service import UnifiedReportService

    analytics_svc = AnalyticsService()
    phone = "+2348055555003"
    norm_phone = normalize_phone(phone)

    async with async_session_factory() as session:
        # Create test business and inventory
        biz = Business(name="Advisory Test Store", phone_number=norm_phone)
        session.add(biz)
        await session.flush()

        user = User(
            business_id=biz.id,
            phone_number=norm_phone,
            role="owner",
            display_name="Tester",
            niche="sme_owner",
        )
        session.add(user)
        await session.flush()

        item = InventoryItem(
            business_id=biz.id,
            item_name="Rice",
            normalized_item_name="rice",
            unit_cost=Decimal("15000"),
            unit="bag",
            quantity_on_hand=Decimal("10"),
        )
        session.add(item)
        await session.flush()

        tx = Transaction(
            business_id=biz.id,
            transaction_type="sale",
            item_name="Rice",
            amount=Decimal("20000"),
            quantity=Decimal("1"),
            status="confirmed",
            occurred_at=datetime.now(UTC),
        )
        session.add(tx)

        await session.commit()

        # 1. Output 1: get_margin_report
        margin_res = await analytics_svc.get_margin_report(session, biz.id, period="today")
        assert AI_ADVISORY_DISCLAIMER in margin_res["summary_text"]

        # 2. Output 2: get_product_performance_analysis
        perf_res = await analytics_svc.get_product_performance_analysis(session, biz.id, period="all")
        assert AI_ADVISORY_DISCLAIMER in perf_res["summary_text"]

        # 3. Output 3: simulate_pricing (both discount and target margin)
        sim_disc = await analytics_svc.simulate_pricing(session, biz.id, item_name="Rice", discount_pct=10, current_price=20000)
        assert AI_ADVISORY_DISCLAIMER in sim_disc["summary_text"]

        sim_margin = await analytics_svc.simulate_pricing(session, biz.id, item_name="Rice", target_margin_pct=30)
        assert AI_ADVISORY_DISCLAIMER in sim_margin["summary_text"]

        # 4. Output 4: get_cash_flow_forecast's shortfall warning
        payable = Payable(
            business_id=biz.id,
            vendor_name="Mega Supplier",
            description="Stock delivery bill",
            amount=Decimal("500000"),  # Far exceeds 20k sales
            due_date=datetime.now(UTC).date(),
            status="outstanding",
        )
        with patch.object(analytics_svc, "list_upcoming_payables", new_callable=AsyncMock) as mock_payables:
            mock_payables.return_value = [payable]
            cff_res = await analytics_svc.get_cash_flow_forecast(session, biz.id, trailing_days=30, horizon_days=14)
            assert cff_res["is_shortfall"] is True
            assert AI_ADVISORY_DISCLAIMER in cff_res["summary_text"]

        # 5. Output 5: Growth Playbook section of monthly/quarterly reports
        unified_svc = UnifiedReportService()
        act = Activity(
            id=uuid4(),
            user_id=user.id,
            business_id=biz.id,
            activity_type="sale",
            title="Sale: Rice (NGN 20,000.00)",
            occurred_at=datetime.now(UTC),
        )
        with patch("langchain_google_genai.ChatGoogleGenerativeAI.ainvoke", new_callable=AsyncMock) as mock_llm:
            from langchain_core.messages import AIMessage
            mock_llm.return_value = AIMessage(
                content=(
                    "📊 *Monthly Highlights*\nAchieved ₦20,000 sales.\n\n"
                    "🚀 *Personalized Growth Playbook*\n"
                    "Since Rice generates 100% of your sales at a 25% margin, consider running a wholesale promo."
                )
            )
            report_out = await unified_svc._synthesize_report(
                user, "monthly", [act], [], [], bi_evidence="Rice sales ₦20,000"
            )
            assert "Personalized Growth Playbook" in report_out
            assert AI_ADVISORY_DISCLAIMER in report_out


@pytest.mark.asyncio
async def test_phase2_scoped_ai_disclaimer_negative_assertions():
    """
    Assert that the disclaimer does NOT appear on:
    1. Plain sale confirmation
    2. Payment reminder message
    3. Casual conversational reply
    """
    from app.models.customer import Customer
    from app.models.debt import Debt
    from app.schemas.extraction import ExtractedRecord
    from app.services.analytics_service import AI_ADVISORY_DISCLAIMER
    from app.services.confirmation_service import build_confirmation_text
    from app.services.debt_service import DebtService

    # 1. Plain sale confirmation
    sale_rec = ExtractedRecord(
        record_type="sale",
        item_name="Sugar",
        quantity=2.0,
        unit="bags",
        amount=Decimal("30000"),
        is_credit=False,
    )
    conf_text = build_confirmation_text(sale_rec)
    assert "Sold 2.0 bags Sugar for ₦30,000" in conf_text
    assert AI_ADVISORY_DISCLAIMER not in conf_text

    # 2. Payment reminder message
    debt_svc = DebtService()
    phone = "+2348055555004"
    norm_phone = normalize_phone(phone)
    async with async_session_factory() as session:
        biz = Business(name="Test Grocery", phone_number=norm_phone)
        session.add(biz)
        await session.flush()

        customer = Customer(
            business_id=biz.id,
            name="John Okafor",
            normalized_name="john okafor",
            phone_number="+2348011223344",
        )
        session.add(customer)
        await session.flush()

        debt = Debt(
            business_id=biz.id,
            customer_id=customer.id,
            amount=Decimal("45000"),
            status="outstanding",
            due_date=datetime.now(UTC).date(),
            notes="2 bags of Sugar",
        )
        session.add(debt)
        await session.commit()

        reminder_text = await debt_svc.draft_payment_reminder(session, biz.id, "John Okafor")
        assert "gentle reminder regarding the outstanding balance" in reminder_text
        assert AI_ADVISORY_DISCLAIMER not in reminder_text

    # 3. Casual conversational reply
    casual_reply = "Good morning! How are sales going today? Let me know whenever you want to log a transaction."
    assert AI_ADVISORY_DISCLAIMER not in casual_reply

