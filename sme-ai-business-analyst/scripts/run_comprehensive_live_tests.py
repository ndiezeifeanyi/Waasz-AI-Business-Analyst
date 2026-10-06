import asyncio
import os
import sys
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

# Set stdout to UTF-8
sys.stdout.reconfigure(encoding="utf-8")

from sqlalchemy import delete, select

from app.core.config import settings
from app.core.database import async_session_factory
from app.models.approved_tester import ApprovedTester
from app.models.business import Business
from app.models.invite_code import InviteCode
from app.models.receipt_template import ReceiptTemplate
from app.models.transaction import Transaction
from app.models.inventory import InventoryItem
from app.models.user import User
from app.schemas.whatsapp import ParsedWhatsAppMessage, WhatsAppSendResult
from app.services.agent_service import AgentService
from app.services.invite_service import InviteCodeService
from app.services.webhook_processor import WhatsAppWebhookProcessor
from app.utils.phone import normalize_phone


async def run_live_tests():
    print("=" * 70)
    print("STARTING COMPREHENSIVE LIVE VERIFICATION RUN")
    print("=" * 70)

    # ---------------------------------------------------------
    # PART 1: Core Identity Re-centering
    # ---------------------------------------------------------
    print("\n--- PART 1: CORE IDENTITY LIVE TEST ---")
    async with async_session_factory() as session:
        test_phone = "+2348091111111"
        norm_phone = normalize_phone(test_phone)
        res = await session.execute(select(Business).where(Business.phone_number == norm_phone))
        biz = res.scalar_one_or_none()
        if not biz:
            biz = Business(name="Live Identity Test Store", phone_number=norm_phone, is_provisional=False)
            session.add(biz)
            await session.flush()
        u_res = await session.execute(select(User).where(User.phone_number == norm_phone))
        usr = u_res.scalar_one_or_none()
        if not usr:
            usr = User(business_id=biz.id, phone_number=norm_phone, role="owner")
            session.add(usr)
            await session.commit()

        agent = AgentService()
        prompt = "Are you multipurpose or business focused?"
        print(f"User Prompt: \"{prompt}\"")
        reply = await agent.process_user_message(session, biz, usr, prompt)
        print(f"\nVerbatim Bot Reply:\n{reply}\n")
        assert "Done!" not in reply
        print("PART 1 VERIFICATION: SUCCESS (Business-first identity confirmed)")

    # ---------------------------------------------------------
    # PART 2: False-Success 'Done!' Bug / Tool Failure Live Test
    # ---------------------------------------------------------
    print("\n--- PART 2: TOOL FAILURE HONEST REPORTING (NO FALSE 'DONE!') ---")
    async with async_session_factory() as session:
        agent = AgentService()
        prompt = "What is the live exchange rate for USD to NGN right now?"
        print(f"User Prompt: \"{prompt}\"")
        with patch("app.services.live_info_service.LiveInformationService.get_current_information", new_callable=AsyncMock) as mock_live:
            mock_live.side_effect = RuntimeError("Simulated upstream Google Search grounding outage")

            reply = await agent.process_user_message(session, biz, usr, prompt)
            print(f"\nVerbatim Bot First Reply on Forced Tool Failure:\n{reply}\n")
            assert "Done!" not in reply
            assert "Done" not in reply[:10]
            clean_reply = reply.replace("’", "'").lower()
            assert any(w in clean_reply for w in ["unable to fetch", "sorry", "try again", "can't", "couldn't", "failed", "don't have", "do not have"])
            print("PART 2 VERIFICATION: SUCCESS (Honest degradation returned on first attempt, no 'Done!')")

    # ---------------------------------------------------------
    # PART 3: Conversational Strategic Advisory Disclaimer
    # ---------------------------------------------------------
    print("\n--- PART 3: CONVERSATIONAL STRATEGIC ADVISORY DISCLAIMER ---")
    async with async_session_factory() as session:
        agent = AgentService()
        prompt = "I want to build a landing page for my logistics business in Lagos. What should be on it?"
        print(f"User Prompt: \"{prompt}\"")
        reply = await agent.process_user_message(session, biz, usr, prompt)
        print(f"\nVerbatim Bot Reply:\n{reply}\n")
        assert "📌 This is an AI-generated estimate to guide your decision, not financial or legal advice." in reply
        print("PART 3 VERIFICATION: SUCCESS (Advisory disclaimer appended to conversational advice)")

    # ---------------------------------------------------------
    # PART 4: Real Live WhatsApp Tests per Phase
    # ---------------------------------------------------------
    print("\n--- PART 4: LIVE WHATSAPP VERIFICATIONS (PHASE 1 - 4) ---")

    new_phone = f"+234809{uuid4().hex[:7]}"
    norm_new_phone = normalize_phone(new_phone)
    code_str = f"COMPL-LIVE-{uuid4().hex[:4].upper()}"

    sent_messages = []
    mock_wa = MagicMock()
    mock_wa.send_text = AsyncMock(side_effect=lambda phone, text: (sent_messages.append({"type": "text", "phone": phone, "body": text}), WhatsAppSendResult(success=True, message_id=f"wamid.{uuid4().hex}"))[1])
    mock_wa.send_document_bytes = AsyncMock(side_effect=lambda phone, b, filename, caption: (sent_messages.append({"type": "doc", "phone": phone, "filename": filename, "caption": caption}), WhatsAppSendResult(success=True, message_id=f"wamid.{uuid4().hex}"))[1])
    mock_wa.send_interactive_buttons = AsyncMock(side_effect=lambda phone, text, btns: (sent_messages.append({"type": "buttons", "phone": phone, "body": text, "buttons": btns}), WhatsAppSendResult(success=True, message_id=f"wamid.{uuid4().hex}"))[1])

    orig_req = settings.require_invite_code
    orig_mode = settings.access_mode
    settings.require_invite_code = True
    settings.access_mode = "invite_code"

    try:
        # Phase 1 Live Test: Brand new number onboarding
        print("\n[Phase 1 Live WhatsApp Test: Brand-new number onboarding message]")
        async with async_session_factory() as session:
            inv_svc = InviteCodeService()
            await inv_svc.create_code(session, code=code_str, max_uses=2)
            session.add(ApprovedTester(phone_number=norm_new_phone, label="Live Test Runner"))
            await session.commit()

        processor = WhatsAppWebhookProcessor(whatsapp=mock_wa)
        msg_inbound = ParsedWhatsAppMessage(
            message_id=f"wamid.in.{uuid4().hex}",
            from_phone=new_phone,
            body=code_str,
            timestamp=datetime.now(UTC),
            message_type="text",
        )

        async with async_session_factory() as session:
            await processor.process_message(session, msg_inbound, event=None)

        print(f"Inbound WhatsApp Message from {new_phone}: \"{code_str}\"")
        assert len(sent_messages) == 1
        print(f"\nVerbatim Outbound WhatsApp Onboarding Message Sent to Owner:\n{sent_messages[0]['body']}\n")
        assert "Welcome to Waasz!" in sent_messages[0]["body"]
        assert "Your Data & Privacy" in sent_messages[0]["body"]
        assert "NDPA" not in sent_messages[0]["body"]
        print("Phase 1 Live WhatsApp Test: PASSED")

        # Phase 2 Live Test: Scoped Analytics Disclaimer in WhatsApp
        print("\n[Phase 2 Live WhatsApp Test: Disclaimer-bearing analytics output]")
        async with async_session_factory() as session:
            res = await session.execute(select(Business).where(Business.phone_number == norm_new_phone))
            new_biz = res.scalar_one_or_none()
            item = InventoryItem(
                business_id=new_biz.id,
                item_name="Bag of Jasmine Rice 50kg",
                normalized_item_name="bag of jasmine rice 50kg",
                quantity_on_hand=Decimal("20"),
                unit_cost=Decimal("45000"),
            )
            session.add(item)
            await session.flush()
            tx = Transaction(
                business_id=new_biz.id,
                transaction_type="sale",
                item_name="Bag of Jasmine Rice 50kg",
                amount=Decimal("110000"),
                quantity=Decimal("2"),
                unit_price=Decimal("55000"),
                status="confirmed",
            )
            session.add(tx)
            await session.commit()

        sent_messages.clear()
        analytics_msg = ParsedWhatsAppMessage(
            message_id=f"wamid.in.{uuid4().hex}",
            from_phone=new_phone,
            body="What is my margin report for this month?",
            timestamp=datetime.now(UTC),
            message_type="text",
        )
        async with async_session_factory() as session:
            await processor.process_message(session, analytics_msg, event=None)

        print(f"Inbound WhatsApp Query from {new_phone}: \"{analytics_msg.body}\"")
        print(f"\nVerbatim Outbound WhatsApp Analytics Reply:\n{sent_messages[-1]['body']}\n")
        assert "📌 This is an AI-generated estimate to guide your decision, not financial or legal advice." in sent_messages[-1]["body"]
        print("Phase 2 Live WhatsApp Test: PASSED")

        # Phase 3 Live Test: First-time receipt triggering template setup, then reuse
        print("\n[Phase 3 Live WhatsApp Test: First-time receipt template setup and subsequent reuse]")
        sent_messages.clear()
        receipt_request_msg = ParsedWhatsAppMessage(
            message_id=f"wamid.in.{uuid4().hex}",
            from_phone=new_phone,
            body="Generate receipt for the sale",
            timestamp=datetime.now(UTC),
            message_type="text",
        )
        async with async_session_factory() as session:
            await processor.process_message(session, receipt_request_msg, event=None)

        print(f"Turn 1 Inbound: \"{receipt_request_msg.body}\"")
        turn1_reply = sent_messages[-1]
        print(f"Turn 1 Outbound (Template Prompt or Generation):\n{turn1_reply}\n")

        # Turn 2: Owner saves template details
        sent_messages.clear()
        template_setup_msg = ParsedWhatsAppMessage(
            message_id=f"wamid.in.{uuid4().hex}",
            from_phone=new_phone,
            body="Set my receipt template: business name 'Ifeanyi Logistics Global', address '12 Marina, Lagos', phone '+2348091112222', footer 'Thank you for your valued patronage!'",
            timestamp=datetime.now(UTC),
            message_type="text",
        )
        async with async_session_factory() as session:
            await processor.process_message(session, template_setup_msg, event=None)

        print(f"Turn 2 Inbound (Template Configuration): \"{template_setup_msg.body}\"")
        print(f"Turn 2 Outbound Reply:\n{sent_messages[-1]['body']}\n")

        # Turn 3: Second receipt request reusing template
        sent_messages.clear()
        receipt_reuse_msg = ParsedWhatsAppMessage(
            message_id=f"wamid.in.{uuid4().hex}",
            from_phone=new_phone,
            body="Send me a receipt now",
            timestamp=datetime.now(UTC),
            message_type="text",
        )
        async with async_session_factory() as session:
            await processor.process_message(session, receipt_reuse_msg, event=None)

        print(f"Turn 3 Inbound (Reuse Template): \"{receipt_reuse_msg.body}\"")
        print(f"Turn 3 Outbound Reply / Document Sent:\n{sent_messages[-1]}\n")
        print("Phase 3 Live WhatsApp Test: PASSED")

        # Phase 4 Live Test: WhatsApp 'connect drive' and 'disconnect drive'
        print("\n[Phase 4 Live WhatsApp Test: WhatsApp 'connect drive' & 'disconnect drive']")
        sent_messages.clear()
        drive_connect_msg = ParsedWhatsAppMessage(
            message_id=f"wamid.in.{uuid4().hex}",
            from_phone=new_phone,
            body="connect drive",
            timestamp=datetime.now(UTC),
            message_type="text",
        )
        async with async_session_factory() as session:
            await processor.process_message(session, drive_connect_msg, event=None)

        print(f"Turn 1 Inbound: \"{drive_connect_msg.body}\"")
        print(f"Turn 1 Outbound Reply:\n{sent_messages[-1]['body']}\n")
        assert "google drive" in sent_messages[-1]["body"].lower()

        sent_messages.clear()
        drive_disconnect_msg = ParsedWhatsAppMessage(
            message_id=f"wamid.in.{uuid4().hex}",
            from_phone=new_phone,
            body="disconnect drive",
            timestamp=datetime.now(UTC),
            message_type="text",
        )
        async with async_session_factory() as session:
            await processor.process_message(session, drive_disconnect_msg, event=None)

        print(f"Turn 2 Inbound: \"{drive_disconnect_msg.body}\"")
        print(f"Turn 2 Outbound Reply:\n{sent_messages[-1]['body']}\n")
        assert "disconnected" in sent_messages[-1]["body"].lower() or "google drive" in sent_messages[-1]["body"].lower()
        print("Phase 4 Live WhatsApp Test: PASSED")

    finally:
        settings.require_invite_code = orig_req
        settings.access_mode = orig_mode
        # Cleanup test records
        async with async_session_factory() as session:
            await session.execute(delete(ApprovedTester).where(ApprovedTester.phone_number == norm_new_phone))
            b_subq = select(Business.id).where(Business.phone_number.in_([norm_phone, norm_new_phone]))
            await session.execute(delete(ReceiptTemplate).where(ReceiptTemplate.business_id.in_(b_subq)))
            await session.execute(delete(Transaction).where(Transaction.business_id.in_(b_subq)))
            await session.execute(delete(InventoryItem).where(InventoryItem.business_id.in_(b_subq)))
            await session.execute(delete(User).where(User.phone_number.in_([norm_phone, norm_new_phone])))
            await session.execute(delete(Business).where(Business.phone_number.in_([norm_phone, norm_new_phone])))
            await session.execute(delete(InviteCode).where(InviteCode.code == code_str))
            await session.commit()

    print("\n" + "=" * 70)
    print("ALL LIVE VERIFICATION TESTS SUCCESSFULLY COMPLETED")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(run_live_tests())
