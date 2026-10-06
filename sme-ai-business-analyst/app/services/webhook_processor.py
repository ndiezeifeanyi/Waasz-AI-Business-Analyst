import inspect
from typing import Any
from datetime import UTC, datetime, timedelta
from decimal import Decimal
import logging
import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import async_session_factory
from app.core.exceptions import AppError, VoiceTranscriptionError
from app.core.sanitization import SanitizationError, validate_extraction_input
from app.models.business import Business
from app.models.transaction import Transaction
from app.models.user import User
from app.schemas.extraction import ExtractedRecord
from app.schemas.whatsapp import ParsedWhatsAppMessage
from app.services.agent_service import AgentService
from app.services.ai_extraction import AiExtractionService
from app.services.confirmation_service import (
    ConfirmationService,
    build_confirmation_text,
    normalize_confirmation_reply,
)
from app.schemas.actor import ActorContext
from app.utils.currency import parse_money_amount
from app.utils.idempotency import idempotency_key, is_duplicate_message
from app.utils.phone import normalize_phone
from app.services.access_control_service import AccessControlService
from app.services.goal_service import GoalService
from app.services.intent_router import IntentRouter
from app.services.invite_service import InviteCodeService
from app.services.knowledge_service import KnowledgeService
from app.services.ledger_service import LedgerService
from app.services.media_downloader import MediaDownloader
from app.services.memory_service import MemoryService
from app.services.ocr_service import OcrService
from app.services.qa_service import QaService
from app.services.group_handler import GroupChatHandler
from app.models.member import Member
from app.services.staff_service import StaffService
from app.services.task_service import TaskService
from app.services.unified_report_service import UnifiedReportService
from app.services.voice_service import VoiceService
from app.services.whatsapp_client import WhatsAppClient
from app.services.whatsapp_parser import parse_webhook_payload

logger = logging.getLogger(__name__)



class WhatsAppWebhookProcessor:
    def __init__(
        self,
        ledger: LedgerService | None = None,
        extractor: AiExtractionService | None = None,
        confirmations: ConfirmationService | None = None,
        whatsapp: WhatsAppClient | None = None,
        media_downloader: MediaDownloader | None = None,
        ocr: OcrService | None = None,
        voice: VoiceService | None = None,
        knowledge: KnowledgeService | None = None,
        memory: MemoryService | None = None,
        goals: GoalService | None = None,
        qa: QaService | None = None,
        groups: GroupChatHandler | None = None,
        unified_reports: UnifiedReportService | None = None,
        tasks: TaskService | None = None,
        agent: AgentService | None = None,
        invites: InviteCodeService | None = None,
        access_control: AccessControlService | None = None,
        staff: StaffService | None = None,
    ) -> None:
        self.ledger = ledger or LedgerService()
        self.extractor = extractor or AiExtractionService()
        self.confirmations = confirmations or ConfirmationService()
        self.whatsapp = whatsapp or WhatsAppClient()
        self.media_downloader = media_downloader or MediaDownloader()
        self.ocr = ocr or OcrService()
        self.voice = voice or VoiceService()
        self.knowledge = knowledge or KnowledgeService()
        self.memory = memory or MemoryService()
        self.goals = goals or GoalService()
        self.qa = qa or QaService(memory=self.memory, knowledge=self.knowledge, goals=self.goals)
        self.groups = groups or GroupChatHandler(whatsapp=self.whatsapp)
        self.unified_reports = unified_reports or UnifiedReportService(whatsapp=self.whatsapp, ledger=self.ledger)
        self.tasks = tasks or TaskService()
        self.invites = invites or InviteCodeService()
        self.access_control = access_control or AccessControlService(invite_service=self.invites)
        self.staff = staff or StaffService()
        self.agent = agent or AgentService(
            ledger=self.ledger,
            extractor=self.extractor,
            confirmations=self.confirmations,
            tasks=self.tasks,
            knowledge=self.knowledge,
            memory=self.memory,
            goals=self.goals,
            qa=self.qa,
            unified_reports=self.unified_reports,
            whatsapp=self.whatsapp,
            staff=self.staff,
        )

    async def process_payload(self, payload: dict) -> None:
        messages = parse_webhook_payload(payload)
        async with async_session_factory() as db:
            event = await self.ledger.create_webhook_event(db, payload)
            await db.commit()
            for parsed in messages:
                try:
                    await self.process_message(db, parsed, event)
                    event.status = "processed"
                    await db.commit()
                except Exception as exc:
                    await db.rollback()
                    logger.exception("WhatsApp message processing failed")
                    event = await db.merge(event)
                    event.status = "failed"
                    event.error_message = str(exc)
                    await db.commit()
                    # Plain language failure notice to user (Phase D3)
                    try:
                        await self.whatsapp.send_text(
                            parsed.from_phone,
                            "⚠️ Something went wrong processing your message. Please try again in a moment.",
                        )
                    except Exception:
                        pass

    async def process_message(self, db: AsyncSession, parsed: ParsedWhatsAppMessage, event) -> None:
        # Check for WhatsApp group chat message
        raw = parsed.raw_payload or {}
        is_group = bool(
            raw.get("group_id")
            or (parsed.from_phone and "@g.us" in parsed.from_phone)
            or raw.get("context", {}).get("group_id")
        )
        if is_group:
            group_id = (
                raw.get("group_id")
                or raw.get("context", {}).get("group_id")
                or parsed.from_phone
            )
            group_reply = await self.groups.handle_group_message(parsed, group_id, parsed.from_phone)
            if group_reply:
                await self.whatsapp.send_text(group_id, group_reply)
            return

        # Gate logic: staff state check followed by pluggable access control
        norm_phone = normalize_phone(parsed.from_phone)

        # Check if caller is an invited or active staff member
        stmt_mem = select(Member).where(
            (Member.wa_id == norm_phone) | (Member.wa_id == parsed.from_phone),
            Member.status.in_(("invited", "active")),
        ).limit(1)
        res_mem = await db.execute(stmt_mem)
        team_member = res_mem.scalar_one_or_none() if hasattr(res_mem, "scalar_one_or_none") else None
        if hasattr(team_member, "__await__"):
            team_member = await team_member
        if not isinstance(team_member, Member):
            team_member = None

        if team_member and team_member.status == "invited":
            text_clean = (parsed.body or "").strip().upper()
            if text_clean in ("JOIN", "ACCEPT", "YES", "START", "OK", "JOINED") or "JOIN" in text_clean:
                accept_res = await self.staff.accept_staff_invite(
                    db, parsed.from_phone, whatsapp=self.whatsapp
                )
                if accept_res.get("success"):
                    b_res = await db.execute(select(Business).where(Business.id == team_member.business_id).limit(1))
                    biz = b_res.scalar_one_or_none()
                    u_res = await db.execute(select(User).where((User.phone_number == norm_phone) | (User.phone_number == parsed.from_phone)).limit(1))
                    usr = u_res.scalar_one_or_none()
                    if biz and usr:
                        inbound = await self.ledger.record_inbound_message(db, parsed, biz, usr, event)
                        inbound.status = "processed"
                        await self.ledger.record_outbound_message(
                            db, biz.id, parsed.from_phone, accept_res.get("welcome_text", ""), None, user_id=usr.id
                        )
                    if hasattr(db, "commit"):
                        await db.commit()
                    return
            else:
                b_res = await db.execute(select(Business).where(Business.id == team_member.business_id).limit(1))
                biz = b_res.scalar_one_or_none()
                biz_name = biz.name if biz else "the business"
                join_prompt = (
                    f"👋 Hello {team_member.display_name or 'there'}!\n\n"
                    f"You have an invitation to join *{biz_name}* on Waasz as a staff member.\n\n"
                    f"Please reply *JOIN* to accept and activate your staff account."
                )
                await self.whatsapp.send_text(parsed.from_phone, join_prompt)
                if hasattr(db, "commit"):
                    await db.commit()
                return

        # Check if caller has a deactivated or removed staff membership
        if not team_member:
            stmt_removed = (
                select(Member)
                .where(
                    (Member.wa_id == norm_phone) | (Member.wa_id == parsed.from_phone),
                    Member.status.in_(("removed", "suspended")),
                )
                .order_by(Member.removed_at.desc().nullslast())
                .limit(1)
            )
            res_rem = await db.execute(stmt_removed)
            removed_member = res_rem.scalar_one_or_none() if hasattr(res_rem, "scalar_one_or_none") else None
            if hasattr(removed_member, "__await__"):
                removed_member = await removed_member
            if isinstance(removed_member, Member):
                b_chk = await db.execute(select(Business).where(Business.id == removed_member.business_id).limit(1))
                b_obj = b_chk.scalar_one_or_none() if hasattr(b_chk, "scalar_one_or_none") else None
                if hasattr(b_obj, "__await__"):
                    b_obj = await b_obj
                biz_name = b_obj.name if b_obj else "your former business"
                revoked_msg = f"ℹ️ Your staff access to *{biz_name}* on Waasz has been deactivated. Please contact your business owner if you believe this is an error."
                await self.whatsapp.send_text(parsed.from_phone, revoked_msg)
                if hasattr(db, "commit"):
                    res_c = db.commit()
                    if inspect.isawaitable(res_c):
                        await res_c
                return

        existing_user = None
        # Pluggable access control for non-staff callers BEFORE account creation
        if not team_member:
            user_res = await db.execute(select(User).where(User.phone_number == norm_phone).limit(1))
            existing_user = user_res.scalar_one_or_none() if hasattr(user_res, "scalar_one_or_none") else None
            if hasattr(existing_user, "__await__"):
                existing_user = await existing_user

            if not existing_user and hasattr(self.ledger, "get_or_create_business_and_user"):
                mock_ret = getattr(self.ledger.get_or_create_business_and_user, "return_value", None)
                if isinstance(mock_ret, tuple) and len(mock_ret) == 2 and mock_ret[1] is not None:
                    existing_user = mock_ret[1]

            decision = await self.access_control.evaluate_access(
                db,
                norm_phone=norm_phone,
                raw_phone=parsed.from_phone,
                text=parsed.body,
                is_existing_user=bool(existing_user),
            )
            if not decision.allowed:
                if decision.reply_text:
                    await self.whatsapp.send_text(parsed.from_phone, decision.reply_text)
                return

        if not existing_user and not team_member:
            init_res = await self.ledger.get_or_create_business_and_user(db, parsed.from_phone)
            if isinstance(init_res, tuple) and len(init_res) == 4:
                business, user, _, _ = init_res
            else:
                business, user = init_res
            inbound = await self.ledger.record_inbound_message(db, parsed, business, user, event)
            inbound.status = "processed"

            ref_msg = ""
            if getattr(settings, "enable_user_referrals", False):
                ref = await self.invites.generate_user_referral_code(db, user.id, max_uses=5)
                ref_msg = f"\n\n🎁 Your personal referral code: *{ref.code}* (share with up to 5 friends)."

            is_invite = bool(getattr(decision, "invite_code", None) or (decision.reason == "invite_code_claimed"))
            full_welcome = self._build_onboarding_intro_message(invite_verified=is_invite, referral_msg=ref_msg)
            send_result = await self.whatsapp.send_text(parsed.from_phone, full_welcome)
            await self.ledger.record_outbound_message(
                db, business.id, parsed.from_phone, full_welcome, send_result, user_id=user.id
            )
            if hasattr(db, "commit"):
                await db.commit()
            return

        biz_user_res = None
        get_mem_fn = getattr(self.ledger, "get_or_create_business_and_member", None)
        if callable(get_mem_fn):
            try:
                candidate = get_mem_fn(db, parsed.from_phone)
                if hasattr(candidate, "__await__"):
                    candidate = await candidate
                if isinstance(candidate, tuple) and len(candidate) in (2, 4):
                    biz_user_res = candidate
            except Exception:
                biz_user_res = None

        if biz_user_res is None:
            biz_user_res = await self.ledger.get_or_create_business_and_user(db, parsed.from_phone)

        if isinstance(biz_user_res, tuple) and len(biz_user_res) == 4:
            business, user, member, actor = biz_user_res
        elif isinstance(biz_user_res, tuple) and len(biz_user_res) >= 2:
            business, user = biz_user_res[0], biz_user_res[1]
            member = None
            actor = ActorContext(
                business_id=getattr(business, "id", None),
                member_id=None,
                role=getattr(user, "role", "owner") or "owner",
                wa_id=getattr(user, "phone_number", parsed.from_phone),
                display_name=getattr(user, "display_name", None),
            )
        else:
            business = getattr(biz_user_res, "business", biz_user_res)
            user = getattr(biz_user_res, "user", biz_user_res)
            member = None
            actor = ActorContext(
                business_id=getattr(business, "id", None),
                member_id=None,
                role="owner",
                wa_id=parsed.from_phone,
                display_name="Friend",
            )
        inbound = await self.ledger.record_inbound_message(db, parsed, business, user, event)

        # Idempotency check: if this inbound message was already fully processed, silently no-op
        if is_duplicate_message(inbound):
            logger.info("Idempotency: message %s already processed. Silently skipping duplicate delivery.", parsed.message_id)
            return

        # Deterministic Alarm Button / Reply handling (NOT routed through LLM agent)
        alarm_reply = await self._try_handle_alarm_button(db, parsed, business, user)
        if alarm_reply:
            inbound.status = "processed"
            if hasattr(db, "commit"):
                await db.commit()
            elif hasattr(db, "flush"):
                await db.flush()

            send_result = await self.whatsapp.send_text(parsed.from_phone, alarm_reply)
            await self.ledger.record_outbound_message(
                db, business.id, parsed.from_phone, alarm_reply, send_result, user_id=user.id
            )
            if hasattr(db, "commit"):
                await db.commit()
            elif hasattr(db, "flush"):
                await db.flush()
            return

        pending = await self.confirmations.latest_actionable(
            db, business.id, member_id=actor.member_id
        )
        if pending:
            handled = await self._try_handle_confirmation_reply(db, parsed, pending)
            if handled:
                inbound.status = "processed"
                # Ensure the DB transaction and all triggers (e.g. fn_sync_transaction_to_user_activity)
                # have actually committed / flushed successfully BEFORE notifying the user!
                if hasattr(db, "commit"):
                    await db.commit()
                elif hasattr(db, "flush"):
                    await db.flush()

                confirmed_tx = None
                tx_stmt = select(Transaction).where(Transaction.confirmation_id == pending.id).limit(1)
                tx_res = await db.execute(tx_stmt)
                confirmed_tx = tx_res.scalar_one_or_none()

                if confirmed_tx and confirmed_tx.transaction_type == "sale" and pending.status == "confirmed":
                    from app.services.receipt_service import ReceiptService
                    receipt_svc = ReceiptService()
                    tmpl = await receipt_svc.get_template(db, business.id)
                    has_custom_name = bool(
                        (tmpl and tmpl.business_display_name and not tmpl.business_display_name.startswith("WhatsApp Business "))
                        or (business.name and not business.is_provisional and not business.name.startswith("WhatsApp Business ") and business.name != "Business")
                    )
                    has_address = bool(tmpl and tmpl.address and tmpl.address.strip())
                    is_profile_incomplete = not (has_custom_name and has_address)

                    if is_profile_incomplete:
                        msg_body = (
                            f"{handled}\n\n"
                            "🧾 *Branded Receipt Setup:*\n"
                            "Your store details (name/address) are not set yet, so your receipt would appear with generic details.\n\n"
                            "To personalize your receipt, reply with your store details:\n"
                            "👉 *Store: [Store Name], Address: [Store Address]*\n"
                            "_(e.g. Store: Lake Toy World, Address: 12 Marina Street, Lagos)_\n\n"
                            "Or tap below to generate immediately with default details:"
                        )
                    else:
                        msg_body = handled

                    send_result = await self.whatsapp.send_interactive_buttons(
                        parsed.from_phone,
                        msg_body,
                        [(f"receipt_{confirmed_tx.id}", "📄 Send receipt")],
                    )
                else:
                    send_result = await self.whatsapp.send_text(parsed.from_phone, handled)

                outbound_text = msg_body if (confirmed_tx and confirmed_tx.transaction_type == "sale" and pending.status == "confirmed") else handled
                await self.ledger.record_outbound_message(
                    db, business.id, parsed.from_phone, outbound_text, send_result, user_id=user.id
                )

                # Phase 3: Dual alerts to business owner for staff actions
                is_staff_actor = bool(actor and actor.role == "staff") or bool(pending.member_id)
                if is_staff_actor and business.phone_number and parsed.from_phone != business.phone_number:
                    staff_name = (actor.display_name if actor else None) or "Staff"
                    # 1. Dual low-stock alert
                    if handled and "Low Stock Alert" in handled:
                        alert_part = handled[handled.find("Low Stock Alert"):]
                        owner_stock_msg = (
                            f"⚠️ *Team Low-Stock Alert*\n"
                            f"Staff member *{staff_name}* confirmed a sale.\n"
                            f"{alert_part}"
                        )
                        try:
                            await self.whatsapp.send_text(business.phone_number, owner_stock_msg)
                            await self.ledger.record_outbound_message(
                                db, business.id, business.phone_number, owner_stock_msg, {"status": "sent"}
                            )
                        except Exception as exc:
                            logger.warning("Could not dispatch low stock alert to owner: %s", exc)

                    # 2. High expense threshold alert
                    if confirmed_tx and confirmed_tx.transaction_type == "expense":
                        threshold = None
                        if isinstance(business.settings, dict):
                            threshold = business.settings.get("expense_approval_threshold")
                        if threshold and float(threshold) > 0 and confirmed_tx.amount and confirmed_tx.amount >= Decimal(str(threshold)):
                            owner_exp_msg = (
                                f"⚠️ *High Expense Alert*\n"
                                f"Staff member *{staff_name}* recorded an expense of *₦{confirmed_tx.amount:,.2f}* for *{confirmed_tx.item_name or confirmed_tx.description or 'expense'}*.\n"
                                f"Configured threshold: ₦{Decimal(str(threshold)):,.2f}"
                            )
                            try:
                                await self.whatsapp.send_text(business.phone_number, owner_exp_msg)
                                await self.ledger.record_outbound_message(
                                    db, business.id, business.phone_number, owner_exp_msg, {"status": "sent"}
                                )
                            except Exception as exc:
                                logger.warning("Could not dispatch high expense alert to owner: %s", exc)

                if hasattr(db, "commit"):
                    await db.commit()
                elif hasattr(db, "flush"):
                    await db.flush()
                return
        else:
            action = normalize_confirmation_reply(parsed.body, parsed.interactive_reply_id)
            is_receipt_action = (parsed.interactive_reply_id or "").startswith("receipt_") or (parsed.body or "").strip().lower() in {"send receipt", "receipt", "📄 send receipt"}
            if not is_receipt_action and (parsed.interactive_reply_id or action != "unknown"):
                logger.info(
                    "Idempotency: confirmation reply %s (%s) received for already-resolved or absent pending confirmation. Silently no-oping.",
                    parsed.message_id,
                    action,
                )
                inbound.status = "processed"
                return

        raw_image_bytes: bytes | None = None
        if parsed.message_type == "image" and parsed.media_id:
            try:
                raw_image_bytes = await self.media_downloader.download(parsed.media_id)
            except Exception as exc:
                logger.warning("Failed to download image media %s: %s", parsed.media_id, exc)

        try:
            source_text = await self._source_text(parsed, image_bytes=raw_image_bytes)
        except VoiceTranscriptionError as exc:
            msg = f"⚠️ {str(exc)}"
            send_result = await self.whatsapp.send_text(parsed.from_phone, msg)
            await self.ledger.record_outbound_message(db, business.id, parsed.from_phone, msg, send_result, user_id=user.id)
            return

        if not source_text:
            if parsed.message_type == "audio":
                msg = "⚠️ I received your voice note but couldn't detect any audible speech to transcribe. Please try sending again or type your message."
                send_result = await self.whatsapp.send_text(parsed.from_phone, msg)
                await self.ledger.record_outbound_message(db, business.id, parsed.from_phone, msg, send_result, user_id=user.id)
            return

        # Check for receipt request button or keyword
        interactive_id = parsed.interactive_reply_id or ""
        lower_text = source_text.strip().lower()
        if interactive_id.startswith("receipt_") or lower_text in {"send receipt", "receipt", "📄 send receipt"}:
            target_tx_id = None
            if interactive_id.startswith("receipt_"):
                try:
                    target_tx_id = UUID(interactive_id.replace("receipt_", ""))
                except Exception:
                    pass

            from app.services.receipt_service import ReceiptService
            receipt_svc = ReceiptService()
            scoped_mem_id = actor.member_id if (actor and actor.role == "staff") else None
            try:
                pdf_bytes, filename, tx = await receipt_svc.generate_receipt_pdf(
                    db, business_id=business.id, transaction_id=target_tx_id, member_id=scoped_mem_id
                )
                caption = f"📄 {'Invoice' if tx.is_credit else 'Receipt'} #{filename.replace('.pdf', '')}"
                send_result = await self.whatsapp.send_document_bytes(
                    parsed.from_phone, pdf_bytes, filename=filename, caption=caption
                )
                await self.ledger.record_outbound_message(
                    db, business.id, parsed.from_phone, f"[Sent document {filename}]", send_result, user_id=user.id
                )

                # If first-time user with default business name, provide immediate guidance on setting store details
                biz_is_default = bool(business.is_provisional or (business.name and business.name.startswith("WhatsApp Business ")))
                if biz_is_default:
                    setup_note = (
                        f"💡 *Customize Your Receipts:*\n"
                        f"Your receipt above currently shows default store details ('{business.name}').\n\n"
                        "To put your official shop name, address, and logo on all future receipts, reply:\n"
                        "👉 *Set business name: [Your Store Name]*\n"
                        "👉 *Set address: [Your Store Address]*\n"
                        "👉 (Optional) Send your store logo photo with caption *Set logo*"
                    )
                    note_res = await self.whatsapp.send_text(parsed.from_phone, setup_note)
                    await self.ledger.record_outbound_message(db, business.id, parsed.from_phone, setup_note, note_res, user_id=user.id)

                inbound.status = "processed"
                if hasattr(db, "commit"):
                    await db.commit()
                elif hasattr(db, "flush"):
                    await db.flush()
                return
            except Exception as exc:
                logger.error("Failed to generate/deliver receipt: %s", exc)
                fail_msg = "⚠️ I could not generate your receipt/invoice right now. Please try again or ask me directly."
                send_res = await self.whatsapp.send_text(parsed.from_phone, fail_msg)
                await self.ledger.record_outbound_message(db, business.id, parsed.from_phone, fail_msg, send_res, user_id=user.id)
                inbound.status = "failed"
                if hasattr(db, "commit"):
                    await db.commit()
                return

        # Check for Store / Receipt details submission (fast path)
        store_details = self._extract_store_setup_details(source_text)
        if store_details:
            from app.services.receipt_service import ReceiptService
            receipt_svc = ReceiptService()
            b_name = store_details.get("business_name")
            addr = store_details.get("address")

            tmpl = await receipt_svc.set_template(
                db,
                business_id=business.id,
                business_display_name=b_name,
                address=addr,
            )
            if b_name:
                business.name = b_name
                business.is_provisional = False

            if hasattr(db, "commit"):
                await db.commit()
            elif hasattr(db, "flush"):
                await db.flush()

            # Check if there is a recent confirmed sale transaction to generate receipt for!
            tx_stmt = (
                select(Transaction)
                .where(
                    Transaction.business_id == business.id,
                    Transaction.transaction_type == "sale",
                    Transaction.status == "confirmed",
                )
                .order_by(Transaction.occurred_at.desc())
                .limit(1)
            )
            tx_res = await db.execute(tx_stmt)
            latest_tx = tx_res.scalar_one_or_none()

            reply_text = (
                "✅ *Saved your store details!*\n"
                f"• *Store Name:* {tmpl.business_display_name}\n"
                f"• *Address:* {tmpl.address or 'Not specified'}\n"
            )

            if latest_tx:
                try:
                    pdf_bytes, filename, tx = await receipt_svc.generate_receipt_pdf(
                        db, business_id=business.id, transaction_id=latest_tx.id
                    )
                    reply_text += "\n📄 *Here is your customized receipt with your store details:*"
                    text_send_res = await self.whatsapp.send_text(parsed.from_phone, reply_text)
                    await self.ledger.record_outbound_message(
                        db, business.id, parsed.from_phone, reply_text, text_send_res, user_id=user.id
                    )
                    send_res = await self.whatsapp.send_document_bytes(
                        parsed.from_phone,
                        pdf_bytes,
                        filename=filename,
                        caption=f"📄 Receipt #{filename.replace('.pdf', '')} - {tmpl.business_display_name}",
                    )
                    await self.ledger.record_outbound_message(
                        db, business.id, parsed.from_phone, f"[Sent document {filename}]", send_res, user_id=user.id
                    )
                except Exception as exc:
                    logger.warning("Could not auto-generate receipt after template update: %s", exc)
                    reply_text += "\nTap below to send your receipt:"
                    send_res = await self.whatsapp.send_interactive_buttons(
                        parsed.from_phone,
                        reply_text,
                        [(f"receipt_{latest_tx.id}", "📄 Send receipt")],
                    )
                    await self.ledger.record_outbound_message(
                        db, business.id, parsed.from_phone, reply_text, send_res, user_id=user.id
                    )
            else:
                reply_text += (
                    "\nAll future receipts and invoices will automatically feature your official store details. "
                    "Whenever you record a sale, just say *'Send receipt'*!"
                )
                send_res = await self.whatsapp.send_text(parsed.from_phone, reply_text)
                await self.ledger.record_outbound_message(
                    db, business.id, parsed.from_phone, reply_text, send_res, user_id=user.id
                )

            inbound.status = "processed"
            if hasattr(db, "commit"):
                await db.commit()
            return

        # Check for Google Drive connect/disconnect commands
        last_outbound = await self._get_last_outbound(db, business.id)
        last_outbound_body = (last_outbound.body or "").lower() if (last_outbound and hasattr(last_outbound, "body")) else ""
        if lower_text in {"connect drive", "link drive", "link google drive", "backup to drive", "connect google drive"} or (
            lower_text == "yes" and "google drive" in last_outbound_body
        ):
            try:
                from app.services.google_drive_service import GoogleDriveService
                drive_svc = GoogleDriveService()
                auth_url = drive_svc.generate_auth_url(business_id=business.id, user_id=user.id)
                connect_msg = (
                    "🔗 Connect Google Drive for Automated Backups\n\n"
                    "Tap the link below to securely authorize Waasz to back up your monthly transaction summaries to your personal Google Drive:\n\n"
                    f"{auth_url}\n\n"
                    "🔒 Security: We request strictly the least-privilege drive.file scope — Waasz can only access files it creates itself, never your personal Drive files.\n\n"
                    "You can disconnect anytime by texting DISCONNECT DRIVE."
                )
                send_res = await self.whatsapp.send_text(parsed.from_phone, connect_msg)
                await self.ledger.record_outbound_message(db, business.id, parsed.from_phone, connect_msg, send_res, user_id=user.id)
                inbound.status = "processed"
                if hasattr(db, "commit"):
                    await db.commit()
                elif hasattr(db, "flush"):
                    await db.flush()
                return
            except Exception as exc:
                logger.exception("Failed to initialize Google Drive connection: %s", exc)
                drive_err_msg = f"⚠️ Could not start Google Drive connection: {str(exc)}"
                send_res = await self.whatsapp.send_text(parsed.from_phone, drive_err_msg)
                await self.ledger.record_outbound_message(db, business.id, parsed.from_phone, drive_err_msg, send_res, user_id=user.id)
                inbound.status = "failed"
                if hasattr(db, "commit"):
                    await db.commit()
                return

        if lower_text in {"disconnect drive", "unlink drive", "unlink google drive", "disconnect google drive", "stop drive backup"}:
            from app.services.google_drive_service import GoogleDriveService
            drive_svc = GoogleDriveService()
            disconnected = await drive_svc.disconnect(db, business_id=business.id)
            if disconnected:
                disc_msg = "✅ Your Google Drive has been disconnected. Automatic monthly PDF backups have been stopped and stored authorization tokens revoked."
            else:
                disc_msg = "ℹ️ No active Google Drive connection was found for your account."
            send_res = await self.whatsapp.send_text(parsed.from_phone, disc_msg)
            await self.ledger.record_outbound_message(db, business.id, parsed.from_phone, disc_msg, send_res, user_id=user.id)
            inbound.status = "processed"
            if hasattr(db, "commit"):
                await db.commit()
            elif hasattr(db, "flush"):
                await db.flush()
            return

        # Check for business profile setup commands (Fast-path)
        biz_name_match = re.match(
            r"^(?:set\s+)?(?:business|shop|store)\s+name(?:\s+to|:)?\s+(.+)$",
            source_text.strip(),
            re.IGNORECASE,
        )
        if not biz_name_match:
            biz_name_match = re.match(
                r"^my\s+(?:business|shop|store)\s+name\s+is\s+(.+)$",
                source_text.strip(),
                re.IGNORECASE,
            )
        if biz_name_match:
            new_name = biz_name_match.group(1).strip().strip("\"'")
            if new_name:
                from app.services.receipt_service import ReceiptService
                receipt_svc = ReceiptService()
                await receipt_svc.set_template(db, business_id=business.id, business_display_name=new_name)
                business.name = new_name
                business.is_provisional = False
                if hasattr(db, "commit"):
                    await db.commit()
                confirm_msg = (
                    f"✅ Business name updated to *'{new_name}'*!\n\n"
                    "All future receipts and invoices will feature this name.\n"
                    "You can also set your store address anytime by texting:\n"
                    "👉 *Set address: [Your Store Address]*"
                )
                send_res = await self.whatsapp.send_text(parsed.from_phone, confirm_msg)
                await self.ledger.record_outbound_message(db, business.id, parsed.from_phone, confirm_msg, send_res, user_id=user.id)
                inbound.status = "processed"
                if hasattr(db, "commit"):
                    await db.commit()
                return

        biz_addr_match = re.match(
            r"^(?:set\s+)?(?:business\s+|shop\s+|store\s+)?address(?:\s+to|:)?\s+(.+)$",
            source_text.strip(),
            re.IGNORECASE,
        )
        if not biz_addr_match:
            biz_addr_match = re.match(
                r"^my\s+(?:business\s+|shop\s+|store\s+)?address\s+is\s+(.+)$",
                source_text.strip(),
                re.IGNORECASE,
            )
        if biz_addr_match:
            new_addr = biz_addr_match.group(1).strip().strip("\"'")
            if new_addr:
                from app.services.receipt_service import ReceiptService
                receipt_svc = ReceiptService()
                await receipt_svc.set_template(db, business_id=business.id, address=new_addr)
                if hasattr(db, "commit"):
                    await db.commit()
                confirm_msg = (
                    f"✅ Store address updated to:\n*{new_addr}*\n\n"
                    "This will now appear on all your receipts and invoices."
                )
                send_res = await self.whatsapp.send_text(parsed.from_phone, confirm_msg)
                await self.ledger.record_outbound_message(db, business.id, parsed.from_phone, confirm_msg, send_res, user_id=user.id)
                inbound.status = "processed"
                if hasattr(db, "commit"):
                    await db.commit()
                return

        if parsed.message_type == "image" and raw_image_bytes and (parsed.body or "").strip().lower() in {"set logo", "logo", "store logo", "business logo"}:
            import os
            logos_dir = os.path.join(settings.media_download_dir, "logos")
            os.makedirs(logos_dir, exist_ok=True)
            logo_path = os.path.join(logos_dir, f"logo_{business.id}.png")
            try:
                with open(logo_path, "wb") as f:
                    f.write(raw_image_bytes)
                from app.services.receipt_service import ReceiptService
                receipt_svc = ReceiptService()
                await receipt_svc.set_template(db, business_id=business.id, business_logo=logo_path)
                if hasattr(db, "commit"):
                    await db.commit()
                logo_confirm = "✅ Business logo saved! Your logo will now appear at the top of your receipts and invoices."
                send_res = await self.whatsapp.send_text(parsed.from_phone, logo_confirm)
                await self.ledger.record_outbound_message(db, business.id, parsed.from_phone, logo_confirm, send_res, user_id=user.id)
                inbound.status = "processed"
                if hasattr(db, "commit"):
                    await db.commit()
                return
            except Exception as e:
                logger.warning("Could not save logo: %s", e)

        # Check for report commands
        if "weekly report" in lower_text or "send report" in lower_text:
            from app.services.report_delivery import ReportDeliveryService
            delivery = ReportDeliveryService(whatsapp=self.whatsapp, ledger=self.ledger)
            # Scoped strictly to the requesting business
            await delivery.send_weekly_reports(business_id=business.id)
            inbound.status = "processed"
            return

        # Check for reminder time reply
        last_outbound = await self._get_last_outbound(db, business.id)
        if last_outbound and "What time should I remind you?" in (last_outbound.body or ""):
            from app.services.task_service import TaskService
            from app.utils.reminder_parser import format_confirmation_time, parse_reminder_time

            business_tz = getattr(business, "timezone", None) or settings.local_timezone
            due_at_utc = parse_reminder_time(source_text, timezone_name=business_tz)

            if not due_at_utc:
                clarification_msg = (
                    "I couldn't quite catch that time. Please reply with a clear time, "
                    "for example: 'tomorrow at 3pm', 'in 2 hours', or 'next Monday by 10am'."
                )
                send_result = await self.whatsapp.send_text(parsed.from_phone, clarification_msg)
                await self.ledger.record_outbound_message(
                    db, business.id, parsed.from_phone, clarification_msg, send_result, user_id=user.id
                )
                inbound.status = "processed"
                return

            tasks = TaskService()
            target_task = await tasks.get_latest_pending_task(db, business.id)
            if target_task:
                await tasks.update_task_due_at(db, target_task.id, due_at_utc)
                task_title = target_task.title
            else:
                target_task = await tasks.create_task(
                    db, business.id, title="Follow-up reminder", due_at=due_at_utc
                )
                task_title = target_task.title

            formatted_time = format_confirmation_time(due_at_utc, business_tz)
            confirmation_msg = f"Got it — I'll remind you on {formatted_time} for '{task_title}'."
            send_result = await self.whatsapp.send_text(parsed.from_phone, confirmation_msg)
            await self.ledger.record_outbound_message(
                db, business.id, parsed.from_phone, confirmation_msg, send_result, user_id=user.id
            )
            inbound.status = "processed"
            return

        # Validate and sanitize input to prevent prompt injection and garbage payloads
        try:
            validated_text = validate_extraction_input(source_text)
        except SanitizationError as exc:
            logger.warning("Sanitization blocked inbound text from %s: %s", parsed.from_phone, exc)
            rejection_text = (
                "Sorry, I couldn't understand that message. "
                "Please send a sale or expense (e.g. 'Sold 3 bags for 15,000') or set a task."
            )
            send_result = await self.whatsapp.send_text(parsed.from_phone, rejection_text)
            await self.ledger.record_outbound_message(
                db, business.id, parsed.from_phone, rejection_text, send_result, user_id=user.id
            )
            inbound.status = "processed"
            return

        user.last_inbound_at = datetime.now(UTC)
        await db.flush()

        # Check if there is a pending clarification for a sale/expense and this message provides the missing amount
        recent_clarification = await self._get_recent_pending_clarification(db, business.id, user_id=user.id)
        if (
            recent_clarification
            and isinstance(getattr(recent_clarification, "extracted_record", None), dict)
        ):
            parsed_amt = parse_money_amount(validated_text)
            prev_rec = recent_clarification.extracted_record
            prev_item = (prev_rec.get("item_name") or "").strip().lower()

            is_amount_only = False
            if parsed_amt is not None and prev_rec.get("record_type") in {"sale", "expense"} and prev_rec.get("amount") is None:
                cleaned_words = re.sub(r"[0-9,\.₦ngnkK\s]+", " ", validated_text).strip().lower()
                amount_only_tokens = {"", "sold for", "spent", "paid", "it was", "it is", "was", "for", "amount", "amount is", "cost", "total"}
                if cleaned_words in amount_only_tokens or (prev_item and prev_item in validated_text.lower()):
                    is_amount_only = True

            if is_amount_only:
                merged_record = ExtractedRecord(
                    record_type=prev_rec["record_type"],
                    item_name=prev_rec.get("item_name"),
                    description=f"{prev_rec.get('description', '')} for ₦{parsed_amt:,.0f}".strip(),
                    quantity=Decimal(str(prev_rec["quantity"])) if prev_rec.get("quantity") is not None else None,
                    unit=prev_rec.get("unit"),
                    amount=parsed_amt,
                    currency=prev_rec.get("currency", "NGN"),
                    confidence=0.9,
                    needs_clarification=False,
                )
                recent_clarification.status = "resolved"
                extraction = await self.ledger.create_ai_extraction(
                    db,
                    business.id,
                    inbound.id,
                    validated_text,
                    merged_record,
                    status="succeeded",
                    provider="local_heuristic",
                    model="clarification-v1",
                    input_tokens=0,
                    output_tokens=0,
                    estimated_cost_usd=0,
                )
                confirmation = await self.ledger.stage_record_for_confirmation(
                    db,
                    business.id,
                    inbound.id,
                    extraction,
                    merged_record,
                    member_id=actor.member_id,
                    source_wamid=parsed.message_id,
                )
                confirmation_text = confirmation.confirmation_text or build_confirmation_text(merged_record)
                send_result = await self.whatsapp.send_confirmation(parsed.from_phone, confirmation_text)
                outbound = await self.ledger.record_outbound_message(
                    db, business.id, parsed.from_phone, confirmation_text, send_result, message_type="interactive", user_id=user.id
                )
                confirmation.confirmation_message_id = outbound.id
                inbound.status = "processed"
                return
            else:
                # User sent an unrelated message (e.g. a new full transaction, question, reminder) - abandon the stale clarification
                recent_clarification.status = "abandoned"

        try:
            reply_text = await self.agent.process_user_message(
                db=db,
                business=business,
                user=user,
                user_message=validated_text,
                inbound_message_id=inbound.id,
                image_bytes=raw_image_bytes,
                actor=actor,
                source_wamid=parsed.message_id,
            )
        except Exception as agent_exc:
            reply_text = None
            if raw_image_bytes and self.ocr:
                try:
                    ocr_text = await self.ocr.extract_text(raw_image_bytes)
                    if ocr_text:
                        reply_text = await self.agent.process_user_message(
                            db=db,
                            business=business,
                            user=user,
                            user_message=f"I extracted this text from the image:\n{ocr_text}",
                            inbound_message_id=inbound.id,
                            actor=actor,
                            source_wamid=parsed.message_id,
                        )
                except Exception:
                    pass
            if not reply_text:
                raise agent_exc
        if reply_text:
            send_result = await self.whatsapp.send_text(parsed.from_phone, reply_text)
            await self.ledger.record_outbound_message(db, business.id, parsed.from_phone, reply_text, send_result, user_id=user.id)
        inbound.status = "processed"
        if hasattr(db, "commit"):
            await db.commit()

    async def _try_handle_confirmation_reply(self, db, parsed, pending) -> str | None:
        action = normalize_confirmation_reply(parsed.body, parsed.interactive_reply_id)
        if action == "cancel":
            return await self.confirmations.cancel(db, pending)

        if getattr(pending, "status", None) == "needs_edit" and parsed.body:
            # Check if user message is an unrelated command/intent (e.g. reminder, report, goal, QA)
            inferred_intent = IntentRouter.classify(parsed.body)
            unrelated_intents = {"reminder", "goal", "knowledge_store", "forget_me", "report"}
            lower_body = parsed.body.lower()
            has_edit_keyword = any(w in lower_body for w in ["change", "edit", "correct", "make it", "instead", "amount", "price", "quantity", "was", "actually", "naira", "ngn", "sold", "spent", "paid"])
            if inferred_intent in unrelated_intents and not has_edit_keyword:
                # Unrelated intent: user changed their mind and sent a new command, do not consume as an edit
                return None

            try:
                validated_body = validate_extraction_input(parsed.body)
            except SanitizationError:
                return "Invalid input. Please describe your edit clearly (e.g. 'Change amount to 5000')."
            corrected = await self.extractor.extract(
                validated_body, db=db, business_id=pending.business_id
            )
            if corrected.record_type in {"sale", "expense"} and corrected.amount is None:
                return "Please include the amount in Naira for this record (e.g. 'Change to 10 bags for ₦250,000')."
            extraction = await self.ledger.create_ai_extraction(
                db,
                pending.business_id,
                pending.source_message_id,
                validated_body,
                corrected,
                status="succeeded" if not corrected.needs_clarification else "needs_clarification",
                provider=self.extractor.last_provider,
                model=self.extractor.last_model,
                input_tokens=self.extractor.last_input_tokens,
                output_tokens=self.extractor.last_output_tokens,
                estimated_cost_usd=self.extractor.last_estimated_cost_usd,
            )
            pending.status = "corrected"
            new_confirmation = await self.ledger.stage_record_for_confirmation(
                db,
                pending.business_id,
                pending.source_message_id,
                extraction,
                corrected,
                member_id=pending.member_id,
            )
            return new_confirmation.confirmation_text

        if action == "confirm":
            return await self.confirmations.confirm(db, pending)
        if action == "edit":
            return await self.confirmations.request_edit(pending)
        return None

    async def _try_handle_alarm_button(
        self,
        db: AsyncSession,
        parsed: ParsedWhatsAppMessage,
        business: Any,
        user: Any,
    ) -> str | None:
        button_id = (parsed.interactive_reply_id or "").strip()
        body_text = (parsed.body or "").strip().lower()

        is_turn_off = (
            button_id.startswith("alarm_off")
            or body_text in ("🔕 turn off", "turn off", "stop alarm", "turn off alarm", "stop")
        )
        is_snooze = (
            button_id.startswith("alarm_snooze")
            or body_text in ("⏰ snooze 10 min", "snooze 10 min", "snooze", "snooze alarm")
        )

        if not is_turn_off and not is_snooze:
            return None

        from app.models.task import Task
        from uuid import UUID

        task = None
        target_uuid = None
        for prefix in ("alarm_off_", "alarm_snooze_"):
            if button_id.startswith(prefix):
                candidate_str = button_id[len(prefix):]
                try:
                    target_uuid = UUID(candidate_str)
                    break
                except ValueError:
                    pass

        if target_uuid:
            task = await db.get(Task, target_uuid)

        if not task:
            task = await self.tasks.get_latest_active_alarm(
                db, business.id, user_id=user.id if user else None
            )

        if is_turn_off:
            if not task:
                return "Alarm turned off. 🔕"
            # Idempotently dismiss: if already dismissed (rapid duplicate click), still return success confirmation
            if task.dismissed_at is None:
                await self.tasks.dismiss_alarm(db, task.id)
            return f"Alarm turned off for '{task.title}'. 🔕"

        if is_snooze:
            if not task:
                return "No active alarm found to snooze."
            await self.tasks.snooze_alarm(db, task.id, snooze_minutes=10)
            return f"Alarm snoozed for 10 minutes: '{task.title}'. ⏰"

        return None

    async def _get_recent_pending_clarification(self, db, business_id, user_id=None):
        if not business_id:
            return None
        from sqlalchemy import select
        from app.models.extraction import AiExtraction
        from app.models.message import WhatsAppMessage
        cutoff = datetime.now(UTC) - timedelta(minutes=15)
        stmt = (
            select(AiExtraction)
            .where(
                AiExtraction.business_id == business_id,
                AiExtraction.status == "needs_clarification",
                AiExtraction.created_at >= cutoff,
            )
        )
        if user_id:
            stmt = stmt.join(
                WhatsAppMessage, AiExtraction.whatsapp_message_id == WhatsAppMessage.id
            ).where(WhatsAppMessage.user_id == user_id)
        stmt = stmt.order_by(AiExtraction.created_at.desc()).limit(1)
        result = await db.execute(stmt)
        if hasattr(result, "scalar_one_or_none"):
            res = result.scalar_one_or_none()
            if hasattr(res, "__await__"):
                res = await res
            return res
        return None

    async def _get_last_outbound(self, db, business_id):
        if not business_id:
            return None
        from sqlalchemy import select
        from app.models.message import WhatsAppMessage
        result = await db.execute(
            select(WhatsAppMessage)
            .where(WhatsAppMessage.business_id == business_id, WhatsAppMessage.direction == "outbound")
            .order_by(WhatsAppMessage.created_at.desc())
            .limit(1)
        )
        if hasattr(result, "scalar_one_or_none"):
            res = result.scalar_one_or_none()
            if hasattr(res, "__await__"):
                res = await res
            if not isinstance(res, WhatsAppMessage) or not hasattr(res, "body"):
                return None
            return res
        return None

    def _build_onboarding_intro_message(self, invite_verified: bool = False, referral_msg: str = "") -> str:
        header = "🎉 Welcome to Waasz! Your invite code has been verified and your account is ready.\n\n" if invite_verified else "👋 Welcome to Waasz!\n\n"
        drive_suggestion = ""
        if getattr(settings, "google_drive_onboarding_mode", "optional") == "optional":
            drive_suggestion = (
                "\n\n☁️ *Optional Google Drive Backup:*\n"
                "Want your monthly reports automatically backed up to your own Google Drive too? Reply *YES* or *CONNECT DRIVE* anytime to set this up."
            )
        elif getattr(settings, "google_drive_onboarding_mode", "optional") == "mandatory":
            drive_suggestion = (
                "\n\n☁️ *Google Drive Backup Required:*\n"
                "To safeguard your records, monthly reports are backed up directly to your Google Drive. Reply *CONNECT DRIVE* now to link your Google account."
            )

        return (
            f"{header}"
            "I'm your AI business assistant right here on WhatsApp. You can record daily sales & expenses, track inventory & low-stock alerts, generate instant PDF receipts, manage customer debts, and receive weekly & monthly business summaries.\n\n"
            "🧾 *One-Time Setup for Receipts & Invoices:*\n"
            "To personalize your PDF receipts and invoices with your real store details, just reply anytime:\n"
            "• *Set business name: [Your Store Name]* (e.g. _Set business name: Ify Bookshop_)\n"
            "• *Set address: [Your Store Address]* (e.g. _Set address: 12 Marina, Lagos_)\n"
            "• (Optional) Send a picture of your store logo with caption *Set logo*\n\n"
            "💡 *Quick Tip for Receipts:* Whenever you record a sale, you can mention the customer's name (e.g. _'Sold 10 books for ₦30,000 to Mr. Emeka'_) and their name will appear on the receipt!\n\n"
            "🔒 *Your Data & Privacy:*\n"
            "• *What we store:* Your chat messages, business transactions, inventory, and registered phone number.\n"
            "• *Why:* Strictly to maintain your business ledger and generate your performance reports.\n"
            "• *Access & Security:* Your records are strictly isolated to your business using database row-level security and tenant boundaries. No other business can see your numbers.\n"
            "• *Data Rights:* You have complete control. You can ask me to *'delete my data'* or *'forget me'* at any time to permanently erase your records from our systems.\n\n"
            "📌 *Note on AI Advice:* Any business insights, pricing simulations, or forecasts I provide are AI-generated estimates to guide your decision-making, not professional financial, tax, or legal advice. Final business choices always remain yours."
            f"{drive_suggestion}\n\n"
            "How can I help your business today?"
            f"{referral_msg}"
        )

    async def _source_text(self, parsed: ParsedWhatsAppMessage, image_bytes: bytes | None = None) -> str:
        if parsed.message_type == "text":
            return parsed.body or ""
        if parsed.message_type in {"interactive", "button"}:
            return parsed.body or parsed.interactive_reply_id or ""
        if parsed.message_type == "image":
            # For image messages, use user's caption if provided, or default to a natural prompt
            return (parsed.body or "").strip() or "What is in this photo? If it is a receipt or expense, extract and record it; otherwise describe what you see."
        if parsed.message_type == "document" and parsed.media_id:
            media = await self.media_downloader.download(parsed.media_id)
            return await self.ocr.extract_text(media)
        if parsed.message_type == "audio" and parsed.media_id:
            media = await self.media_downloader.download(parsed.media_id)
            return await self.voice.transcribe(media)
        return parsed.body or ""

    def _extract_store_setup_details(self, text: str) -> dict[str, str] | None:
        if not text:
            return None
        t = text.strip()
        import re

        m_combo = re.search(
            r"(?:(?:my\s+)?(?:store|business|shop)(?:\s+name)?(?:\s+is)?[:\s]+)(.+?)(?:,\s*|\n|\s+and\s+)(?:(?:my\s+)?(?:address|addr|location)(?:\s+is)?[:\s]+)(.+)",
            t,
            re.IGNORECASE,
        )
        if m_combo:
            return {"business_name": m_combo.group(1).strip(), "address": m_combo.group(2).strip()}

        m_combo2 = re.search(
            r"(?:(?:my\s+)?(?:address|addr|location)(?:\s+is)?[:\s]+)(.+?)(?:,\s*|\n|\s+and\s+)(?:(?:my\s+)?(?:store|business|shop)(?:\s+name)?(?:\s+is)?[:\s]+)(.+)",
            t,
            re.IGNORECASE,
        )
        if m_combo2:
            return {"business_name": m_combo2.group(2).strip(), "address": m_combo2.group(1).strip()}

        m_name = re.match(
            r"^(?:set\s+)?(?:my\s+)?(?:business|store|shop)(?:\s+name)?(?:\s+is)?[:\s]+(.+)$",
            t,
            re.IGNORECASE,
        )
        if m_name and not any(kw in t.lower() for kw in ["address", "addr", "location"]):
            return {"business_name": m_name.group(1).strip()}

        m_addr = re.match(
            r"^(?:set\s+)?(?:my\s+)?(?:address|addr|location)(?:\s+is)?[:\s]+(.+)$",
            t,
            re.IGNORECASE,
        )
        if m_addr and not any(kw in t.lower() for kw in ["business", "store", "shop"]):
            return {"address": m_addr.group(1).strip()}

        return None


async def process_whatsapp_webhook(payload: dict) -> None:
    try:
        await WhatsAppWebhookProcessor().process_payload(payload)
    except AppError:
        raise
    except Exception:
        logger.exception("Unhandled webhook processing error")
