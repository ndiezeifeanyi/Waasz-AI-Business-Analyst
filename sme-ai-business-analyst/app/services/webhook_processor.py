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
from app.services.unified_report_service import UnifiedReportService
from app.services.task_service import TaskService
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

        # Gate logic: pluggable access control BEFORE account creation and on message intake
        norm_phone = normalize_phone(parsed.from_phone)
        user_res = await db.execute(select(User).where(User.phone_number == norm_phone).limit(1))
        existing_user = user_res.scalar_one_or_none()

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

        if not existing_user:
            business, user = await self.ledger.get_or_create_business_and_user(db, parsed.from_phone)
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

        business, user = await self.ledger.get_or_create_business_and_user(db, parsed.from_phone)
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

        pending = await self.confirmations.latest_actionable(db, business.id)
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
                    send_result = await self.whatsapp.send_interactive_buttons(
                        parsed.from_phone,
                        handled,
                        [(f"receipt_{confirmed_tx.id}", "📄 Send receipt")],
                    )
                else:
                    send_result = await self.whatsapp.send_text(parsed.from_phone, handled)

                await self.ledger.record_outbound_message(
                    db, business.id, parsed.from_phone, handled, send_result, user_id=user.id
                )
                if hasattr(db, "commit"):
                    await db.commit()
                elif hasattr(db, "flush"):
                    await db.flush()
                return
        else:
            action = normalize_confirmation_reply(parsed.body, parsed.interactive_reply_id)
            if parsed.interactive_reply_id or action != "unknown":
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
            try:
                pdf_bytes, filename, tx = await receipt_svc.generate_receipt_pdf(
                    db, business_id=business.id, transaction_id=target_tx_id
                )
                caption = f"📄 {'Invoice' if tx.is_credit else 'Receipt'} #{filename.replace('.pdf', '')}"
                send_result = await self.whatsapp.send_document_bytes(
                    parsed.from_phone, pdf_bytes, filename=filename, caption=caption
                )
                await self.ledger.record_outbound_message(
                    db, business.id, parsed.from_phone, f"[Sent document {filename}]", send_result, user_id=user.id
                )
                inbound.status = "processed"
                if hasattr(db, "commit"):
                    await db.commit()
                elif hasattr(db, "flush"):
                    await db.flush()
                return
            except Exception as exc:
                logger.error("Failed to generate/deliver receipt: %s", exc)

        # Check for Google Drive connect/disconnect commands
        last_outbound = await self._get_last_outbound(db, business.id)
        last_outbound_body = (last_outbound.body or "").lower() if last_outbound else ""
        if lower_text in {"connect drive", "link drive", "link google drive", "backup to drive", "connect google drive"} or (
            lower_text == "yes" and "google drive" in last_outbound_body
        ):
            from app.services.google_drive_service import GoogleDriveService
            drive_svc = GoogleDriveService()
            auth_url = drive_svc.generate_auth_url(business_id=business.id, user_id=user.id)
            connect_msg = (
                "🔗 *Connect Google Drive for Automated Backups*\n\n"
                "Tap the link below to securely authorize Waasz to back up your monthly transaction summaries to your personal Google Drive:\n\n"
                f"{auth_url}\n\n"
                "🔒 *Security*: We request strictly the least-privilege `drive.file` scope — Waasz can only access files it creates itself, never your personal Drive files.\n\n"
                "You can disconnect anytime by texting *DISCONNECT DRIVE*."
            )
            send_res = await self.whatsapp.send_text(parsed.from_phone, connect_msg)
            await self.ledger.record_outbound_message(db, business.id, parsed.from_phone, connect_msg, send_res, user_id=user.id)
            inbound.status = "processed"
            if hasattr(db, "commit"):
                await db.commit()
            elif hasattr(db, "flush"):
                await db.flush()
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
        recent_clarification = await self._get_recent_pending_clarification(db, business.id)
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
                    db, business.id, inbound.id, extraction, merged_record
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
                db, pending.business_id, pending.source_message_id, extraction, corrected
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

    async def _get_recent_pending_clarification(self, db, business_id):
        if not business_id:
            return None
        from sqlalchemy import select
        from app.models.extraction import AiExtraction
        cutoff = datetime.now(UTC) - timedelta(minutes=15)
        result = await db.execute(
            select(AiExtraction)
            .where(
                AiExtraction.business_id == business_id,
                AiExtraction.status == "needs_clarification",
                AiExtraction.created_at >= cutoff,
            )
            .order_by(AiExtraction.created_at.desc())
            .limit(1)
        )
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
            # For image messages, use user's caption if provided, or default to a natural prompt
            return (parsed.body or "").strip() or "What is in this photo? If it is a receipt or expense, extract and record it; otherwise describe what you see."
        if parsed.message_type == "document" and parsed.media_id:
            media = await self.media_downloader.download(parsed.media_id)
            return await self.ocr.extract_text(media)
        if parsed.message_type == "audio" and parsed.media_id:
            media = await self.media_downloader.download(parsed.media_id)
            return await self.voice.transcribe(media)
        return parsed.body or ""


async def process_whatsapp_webhook(payload: dict) -> None:
    try:
        await WhatsAppWebhookProcessor().process_payload(payload)
    except AppError:
        raise
    except Exception:
        logger.exception("Unhandled webhook processing error")
