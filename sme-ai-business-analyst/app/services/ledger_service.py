import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.activity import Activity
from app.models.business import Business
from app.models.confirmation import Confirmation
from app.models.customer import Customer
from app.models.extraction import AiExtraction
from app.models.inventory import InventoryMovement
from app.models.message import WhatsAppMessage
from app.models.task import Task
from app.models.transaction import Transaction
from app.models.user import User
from app.models.webhook import WebhookEvent
from app.schemas.extraction import ExtractedRecord
from app.schemas.whatsapp import ParsedWhatsAppMessage, WhatsAppSendResult
from app.services.confirmation_service import build_confirmation_text
from app.utils.phone import normalize_phone


class LedgerService:
    async def create_webhook_event(self, db: AsyncSession, payload: dict) -> WebhookEvent:
        event = WebhookEvent(payload=payload, status="received")
        db.add(event)
        await db.flush()
        return event

    async def get_or_create_business_and_user(
        self, db: AsyncSession, phone_number: str
    ) -> tuple[Business, User]:
        normalized = normalize_phone(phone_number)

        user_result = await db.execute(select(User).where(User.phone_number == normalized).limit(1))
        user = user_result.scalar_one_or_none()

        business = None
        if user and user.business_id:
            b_res = await db.execute(select(Business).where(Business.id == user.business_id).limit(1))
            business = b_res.scalar_one_or_none()

        if not business:
            result = await db.execute(
                select(Business).where(Business.phone_number == normalized).limit(1)
            )
            business = result.scalar_one_or_none()

        if not business:
            business = Business(
                name=f"WhatsApp Business {normalized[-4:]}",
                owner_name=None,
                phone_number=normalized,
                business_type=None,
                is_provisional=True,
            )
            db.add(business)
            await db.flush()
        else:
            if business.deleted_at is not None:
                business.deleted_at = None
                await db.flush()

        if not user:
            user = User(
                business_id=business.id,
                phone_number=normalized,
                display_name=None,
                role="owner",
            )
            db.add(user)
            await db.flush()
        else:
            changed = False
            if user.business_id != business.id:
                user.business_id = business.id
                changed = True
            if not user.is_active:
                user.is_active = True
                changed = True
            if user.deleted_at is not None:
                user.deleted_at = None
                changed = True
            if changed:
                await db.flush()

        return business, user

    async def record_inbound_message(
        self,
        db: AsyncSession,
        parsed: ParsedWhatsAppMessage,
        business: Business,
        user: User,
        webhook_event: WebhookEvent | None = None,
    ) -> WhatsAppMessage:
        existing = await db.execute(
            select(WhatsAppMessage)
            .where(WhatsAppMessage.whatsapp_message_id == parsed.message_id)
            .limit(1)
        )
        message = existing.scalar_one_or_none()
        if message:
            setattr(message, "_is_duplicate", True)
            return message

        message = WhatsAppMessage(
            business_id=business.id,
            user_id=user.id,
            webhook_event_id=webhook_event.id if webhook_event else None,
            direction="inbound",
            whatsapp_message_id=parsed.message_id,
            from_phone=normalize_phone(parsed.from_phone),
            message_type=parsed.message_type,
            body=parsed.body,
            media_id=parsed.media_id,
            interactive_reply_id=parsed.interactive_reply_id,
            raw_payload=parsed.raw_payload,
            status="received",
            received_at=parsed.timestamp,
        )
        db.add(message)
        await db.flush()
        return message

    async def record_outbound_message(
        self,
        db: AsyncSession,
        business_id: UUID,
        to_phone: str,
        body: str,
        result: WhatsAppSendResult,
        message_type: str = "text",
        user_id: UUID | None = None,
        media_id: str | None = None,
    ) -> WhatsAppMessage:
        message = WhatsAppMessage(
            business_id=business_id,
            user_id=user_id,
            direction="outbound",
            whatsapp_message_id=result.message_id,
            to_phone=normalize_phone(to_phone),
            message_type=message_type,
            body=body,
            media_id=media_id,
            status="sent" if not result.error_message else "failed",
            error_message=result.error_message,
            sent_at=datetime.now(UTC) if not result.error_message else None,
        )
        db.add(message)
        await db.flush()
        return message

    async def create_ai_extraction(
        self,
        db: AsyncSession,
        business_id: UUID,
        message_id: UUID,
        source_text: str,
        record: ExtractedRecord,
        status: str = "succeeded",
        provider: str = "local_heuristic",
        model: str = "local-regex-v1",
        input_tokens: int = 0,
        output_tokens: int = 0,
        estimated_cost_usd=0,
    ) -> AiExtraction:
        valid_providers = {"gemini", "groq", "openai", "google_vision", "tesseract", "whisper", "local_heuristic"}
        safe_provider = provider if provider in valid_providers else "local_heuristic"

        dumped = record.model_dump(mode="json")
        if dumped.get("amount") is not None:
            try:
                amt = Decimal(str(dumped["amount"]))
                dumped["amount"] = int(amt) if amt % 1 == 0 else float(amt)
            except Exception:
                pass
        if dumped.get("unit_price") is not None:
            try:
                amt = Decimal(str(dumped["unit_price"]))
                dumped["unit_price"] = int(amt) if amt % 1 == 0 else float(amt)
            except Exception:
                pass

        extraction = AiExtraction(
            business_id=business_id,
            whatsapp_message_id=message_id,
            source_text=source_text,
            provider=safe_provider,
            model=model,
            raw_response=dumped,
            extracted_record=dumped,
            record_type=record.record_type,
            confidence=record.confidence,
            needs_clarification=record.needs_clarification,
            status=status,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            estimated_cost_usd=estimated_cost_usd,
        )
        db.add(extraction)
        await db.flush()
        return extraction

    async def stage_record_for_confirmation(
        self,
        db: AsyncSession,
        business_id: UUID,
        source_message_id: UUID,
        extraction: AiExtraction,
        record: ExtractedRecord,
    ) -> Confirmation:
        dumped = record.model_dump(mode="json")
        if dumped.get("amount") is not None:
            try:
                amt = Decimal(str(dumped["amount"]))
                dumped["amount"] = int(amt) if amt % 1 == 0 else float(amt)
            except Exception:
                pass
        if dumped.get("unit_price") is not None:
            try:
                amt = Decimal(str(dumped["unit_price"]))
                dumped["unit_price"] = int(amt) if amt % 1 == 0 else float(amt)
            except Exception:
                pass

        # Invalidate/reject any older pending confirmations for this business so only 1 confirmation is active
        prev_pendings = (
            await db.execute(
                select(Confirmation).where(
                    Confirmation.business_id == business_id,
                    Confirmation.status.in_(("pending", "needs_edit")),
                )
            )
        ).scalars().all()
        for prev in prev_pendings:
            prev.status = "rejected"
            # Cancel any unconfirmed staged transactions
            prev_txs = (
                await db.execute(
                    select(Transaction).where(
                        Transaction.confirmation_id == prev.id,
                        Transaction.status == "pending_confirmation",
                    )
                )
            ).scalars().all()
            for tx in prev_txs:
                tx.status = "rejected"

            prev_moves = (
                await db.execute(
                    select(InventoryMovement).where(
                        InventoryMovement.confirmation_id == prev.id,
                        InventoryMovement.status == "pending_confirmation",
                    )
                )
            ).scalars().all()
            for mv in prev_moves:
                mv.status = "rejected"

        confirmation = Confirmation(
            business_id=business_id,
            ai_extraction_id=extraction.id,
            source_message_id=source_message_id,
            token=secrets.token_urlsafe(12),
            confirmation_text=build_confirmation_text(record),
            extracted_record=dumped,
            status="pending",
            expires_at=datetime.now(UTC) + timedelta(hours=24),
        )
        db.add(confirmation)
        await db.flush()

        if record.record_type in {"sale", "expense"}:
            customer_id = None
            if record.record_type == "sale" and record.is_credit and record.customer_name:
                cust_name = record.customer_name.strip()
                norm_name = cust_name.lower()
                cust_res = await db.execute(
                    select(Customer).where(
                        Customer.business_id == business_id,
                        Customer.normalized_name == norm_name,
                    ).limit(1)
                )
                customer = cust_res.scalar_one_or_none()
                if not customer:
                    customer = Customer(
                        business_id=business_id,
                        name=cust_name,
                        normalized_name=norm_name,
                    )
                    db.add(customer)
                    await db.flush()
                customer_id = customer.id

            db.add(
                Transaction(
                    business_id=business_id,
                    confirmation_id=confirmation.id,
                    ai_extraction_id=extraction.id,
                    source_message_id=source_message_id,
                    transaction_type=record.record_type,
                    item_name=record.item_name,
                    description=record.description,
                    quantity=record.quantity,
                    unit=record.unit,
                    unit_price=record.unit_price,
                    amount=record.amount,
                    currency=record.currency,
                    status="pending_confirmation",
                    is_credit=record.is_credit,
                    customer_id=customer_id,
                )
            )
        elif record.record_type == "inventory_update":
            db.add(
                InventoryMovement(
                    business_id=business_id,
                    confirmation_id=confirmation.id,
                    ai_extraction_id=extraction.id,
                    source_message_id=source_message_id,
                    movement_type="stock_in",
                    item_name=record.item_name or "unknown item",
                    quantity=record.quantity or 0,
                    unit=record.unit,
                    note=record.description,
                    status="pending_confirmation",
                )
            )

        elif record.record_type == "task":
            # For tasks, we use the extracted item_name as title
            db.add(
                Task(
                    id=extraction.id,  # Use same ID for easy lookup
                    business_id=business_id,
                    title=record.item_name or "New Task",
                    description=record.description,
                    due_at=None,  # We'll parse record.due_at or ask the user
                )
            )
        elif record.record_type == "activity":
            db.add(
                Activity(
                    business_id=business_id,
                    category=record.category or "general",
                    description=record.description or "Business activity",
                )
            )

        await db.flush()
        return confirmation
