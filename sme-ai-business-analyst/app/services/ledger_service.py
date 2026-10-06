import secrets
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.activity import Activity
from app.models.audit import AuditLog
from app.models.business import Business
from app.models.confirmation import Confirmation
from app.models.customer import Customer
from app.models.debt import Debt
from app.models.extraction import AiExtraction
from app.models.inventory import InventoryItem, InventoryMovement
from app.models.member import Member
from app.models.message import WhatsAppMessage
from app.models.task import Task
from app.models.transaction import Transaction
from app.models.user import User
from app.models.webhook import WebhookEvent
from app.schemas.actor import ActorContext
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

    async def get_or_create_business_and_member(
        self, db: AsyncSession, phone_number: str
    ) -> tuple[Business, User, Member, ActorContext]:
        normalized = normalize_phone(phone_number)

        # 1. First check if phone belongs to an existing active or invited member
        mem_res = await db.execute(
            select(Member)
            .where(
                (Member.wa_id == normalized) | (Member.wa_id == phone_number),
                Member.status.in_(("active", "invited")),
            )
            .limit(1)
        )
        member = mem_res.scalar_one_or_none()

        business = None
        user = None

        if member:
            b_res = await db.execute(
                select(Business).where(Business.id == member.business_id).limit(1)
            )
            business = b_res.scalar_one_or_none()
            if business and business.deleted_at is not None:
                business.deleted_at = None
                await db.flush()

            u_res = await db.execute(
                select(User).where(User.phone_number == normalized).limit(1)
            )
            user = u_res.scalar_one_or_none()
            if not user and business:
                user = User(
                    id=member.id,
                    business_id=business.id,
                    phone_number=normalized,
                    display_name=member.display_name,
                    role=member.role if member.role in ("owner", "staff") else "staff",
                )
                db.add(user)
                await db.flush()
            elif user and business:
                changed = False
                if user.business_id != business.id:
                    user.business_id = business.id
                    changed = True
                if user.role != member.role and member.role in ("owner", "staff"):
                    user.role = member.role
                    changed = True
                if changed:
                    await db.flush()

        if not business or not user:
            # Fall back to standard business & user resolution
            business, user = await self._resolve_business_and_user_fallback(db, normalized)

        # Ensure an owner Member exists if not already present
        if not member:
            mem_chk = await db.execute(
                select(Member).where(
                    Member.business_id == business.id,
                    Member.wa_id == normalized,
                    Member.status.in_(("active", "invited")),
                ).limit(1)
            )
            member = mem_chk.scalar_one_or_none()
            if not member and (business.phone_number == normalized or not business.phone_number):
                member = Member(
                    id=user.id,
                    business_id=business.id,
                    wa_id=user.phone_number,
                    display_name=user.display_name or "Business Owner",
                    role="owner",
                    status="active",
                    consent_accepted_at=user.created_at,
                )
                db.add(member)
                await db.flush()

        role = member.role if member else "owner"
        actor = ActorContext(
            business_id=business.id,
            member_id=member.id if member else user.id,
            role=role,
            wa_id=member.wa_id if member else user.phone_number,
            display_name=member.display_name if member else user.display_name,
        )

        return business, user, member, actor

    async def _resolve_business_and_user_fallback(
        self, db: AsyncSession, normalized: str
    ) -> tuple[Business, User]:
        user_result = await db.execute(select(User).where(User.phone_number == normalized).limit(1))
        user = user_result.scalar_one_or_none()

        business = None
        if user and user.business_id:
            b_res = await db.execute(select(Business).where(Business.id == user.business_id).limit(1))
            business = b_res.scalar_one_or_none()
            if business and business.phone_number != normalized:
                # User is not the owner; verify active/invited membership
                mem_chk = await db.execute(
                    select(Member).where(
                        Member.business_id == business.id,
                        Member.wa_id == normalized,
                        Member.status.in_(("active", "invited")),
                    ).limit(1)
                )
                if not mem_chk.scalar_one_or_none():
                    # Membership has been removed or suspended; dissociate
                    business = None

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

    async def get_or_create_business_and_user(
        self, db: AsyncSession, phone_number: str
    ) -> tuple[Business, User]:
        business, user, _, _ = await self.get_or_create_business_and_member(db, phone_number)
        return business, user

    async def resolve_actor_context(
        self, db: AsyncSession, phone_number: str
    ) -> ActorContext | None:
        normalized = normalize_phone(phone_number)
        mem_res = await db.execute(
            select(Member)
            .where(
                (Member.wa_id == normalized) | (Member.wa_id == phone_number),
                Member.status.in_(("active", "invited")),
            )
            .limit(1)
        )
        member = mem_res.scalar_one_or_none()
        if member:
            return ActorContext(
                business_id=member.business_id,
                member_id=member.id,
                role=member.role,
                wa_id=member.wa_id,
                display_name=member.display_name,
            )
        return None

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
        member_id: UUID | None = None,
        source_wamid: str | None = None,
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

        # Invalidate/reject older pending confirmations scoped to this member/business
        where_cond = [
            Confirmation.business_id == business_id,
            Confirmation.status.in_(("pending", "needs_edit")),
        ]
        if member_id is not None:
            where_cond.append(Confirmation.member_id == member_id)

        prev_pendings = (
            await db.execute(select(Confirmation).where(*where_cond))
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
            member_id=member_id,
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
            if record.record_type == "sale" and record.customer_name:
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
                        id=uuid4(),
                        business_id=business_id,
                        created_by_member_id=member_id,
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
                    created_by_member_id=member_id,
                    source_wamid=source_wamid,
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
                    created_by_member_id=member_id,
                    source_wamid=source_wamid,
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
                    created_by_member_id=member_id,
                    title=record.item_name or "New Task",
                    description=record.description,
                    due_at=None,  # We'll parse record.due_at or ask the user
                )
            )
        elif record.record_type == "activity":
            db.add(
                Activity(
                    business_id=business_id,
                    created_by_member_id=member_id,
                    activity_type=record.category or "general",
                    title=record.item_name or "Business activity",
                    details={"description": record.description} if record.description else {},
                )
            )

        await db.flush()
        return confirmation

    async def set_item_cost(
        self,
        db: AsyncSession,
        business_id: UUID,
        item_name: str,
        unit_cost: Decimal | float,
    ) -> InventoryItem:
        """Set or update the unit cost (COGS) for an inventory item."""
        norm_name = item_name.strip().lower()
        cost_dec = Decimal(str(unit_cost))
        res = await db.execute(
            select(InventoryItem)
            .where(
                InventoryItem.business_id == business_id,
                InventoryItem.normalized_item_name == norm_name,
            )
            .limit(1)
        )
        item = res.scalar_one_or_none()
        if not item:
            item = InventoryItem(
                business_id=business_id,
                item_name=item_name.strip(),
                normalized_item_name=norm_name,
                unit_cost=cost_dec,
                quantity_on_hand=Decimal("0"),
            )
            db.add(item)
        else:
            item.unit_cost = cost_dec
            item.updated_at = datetime.now(UTC)
        await db.flush()
        return item

    async def set_reorder_threshold(
        self,
        db: AsyncSession,
        business_id: UUID,
        item_name: str,
        threshold: Decimal | float | None,
        current_quantity: Decimal | float | None = None,
    ) -> InventoryItem:
        """Set or update the reorder / low-stock threshold and optionally current quantity for an inventory item."""
        norm_name = item_name.strip().lower()
        thresh_dec = Decimal(str(threshold)) if threshold is not None else None
        qty_dec = Decimal(str(current_quantity)) if current_quantity is not None else None
        res = await db.execute(
            select(InventoryItem)
            .where(
                InventoryItem.business_id == business_id,
                InventoryItem.normalized_item_name == norm_name,
            )
            .limit(1)
        )
        item = res.scalar_one_or_none()
        if not item:
            item = InventoryItem(
                business_id=business_id,
                item_name=item_name.strip(),
                normalized_item_name=norm_name,
                low_stock_threshold=thresh_dec,
                quantity_on_hand=qty_dec if qty_dec is not None else Decimal("0"),
            )
            db.add(item)
        else:
            item.low_stock_threshold = thresh_dec
            if qty_dec is not None:
                item.quantity_on_hand = qty_dec
            item.updated_at = datetime.now(UTC)
        await db.flush()
        return item

    async def correct_stock_level(
        self,
        db: AsyncSession,
        business_id: UUID,
        item_name: str,
        actual_quantity: Decimal | float,
    ) -> InventoryItem:
        """Set or correct the physical stock level (quantity_on_hand) directly without requiring a unit cost."""
        norm_name = item_name.strip().lower()
        qty_dec = Decimal(str(actual_quantity))
        res = await db.execute(
            select(InventoryItem)
            .where(
                InventoryItem.business_id == business_id,
                InventoryItem.normalized_item_name == norm_name,
            )
            .limit(1)
        )
        item = res.scalar_one_or_none()
        if not item:
            item = InventoryItem(
                business_id=business_id,
                item_name=item_name.strip(),
                normalized_item_name=norm_name,
                quantity_on_hand=qty_dec,
            )
            db.add(item)
        else:
            item.quantity_on_hand = qty_dec
            item.updated_at = datetime.now(UTC)
        await db.flush()
        return item

    async def record_transaction(
        self,
        db: AsyncSession,
        business_id: UUID,
        record_type: str,
        amount: Decimal | float,
        item_name: str | None = None,
        quantity: Decimal | float | None = None,
        unit: str | None = None,
        unit_price: Decimal | float | None = None,
        description: str | None = None,
        is_credit: bool = False,
        customer_name: str | None = None,
        due_date: datetime | str | None = None,
        status: str = "confirmed",
        created_by_member_id: UUID | None = None,
        source_wamid: str | None = None,
    ) -> tuple[Transaction, Debt | None, str | None]:
        """
        Record a transaction directly into the ledger.
        If is_credit=True and customer_name is provided:
        - Links or creates a lightweight Customer record
        - Automatically creates an open Debt record
        If status='confirmed' and record_type='sale' and item is tracked in inventory:
        - Decrements quantity_on_hand inline
        - Proactively checks reorder_threshold and returns a low-stock alert message if crossed.
        """
        # Idempotency check via source_wamid
        if source_wamid:
            existing_res = await db.execute(
                select(Transaction).where(
                    Transaction.business_id == business_id,
                    Transaction.source_wamid == source_wamid,
                ).limit(1)
            )
            existing_tx = existing_res.scalar_one_or_none()
            if existing_tx:
                return existing_tx, None, None

        amount_dec = Decimal(str(amount))
        qty_dec = Decimal(str(quantity)) if quantity is not None else None
        unit_price_dec = Decimal(str(unit_price)) if unit_price is not None else None

        customer_id = None
        if customer_name and customer_name.strip():
            cust_name = customer_name.strip()
            norm_name = cust_name.lower()
            cust_res = await db.execute(
                select(Customer)
                .where(
                    Customer.business_id == business_id,
                    Customer.normalized_name == norm_name,
                )
                .limit(1)
            )
            customer = cust_res.scalar_one_or_none()
            if hasattr(customer, "__await__"):
                customer = await customer
            if not isinstance(customer, Customer):
                customer = Customer(
                    id=uuid4(),
                    business_id=business_id,
                    created_by_member_id=created_by_member_id,
                    name=cust_name,
                    normalized_name=norm_name,
                )
                db.add(customer)
                await db.flush()
            customer_id = customer.id

        tx = Transaction(
            business_id=business_id,
            created_by_member_id=created_by_member_id,
            source_wamid=source_wamid,
            transaction_type=record_type,
            item_name=item_name,
            description=description,
            quantity=qty_dec,
            unit=unit,
            unit_price=unit_price_dec,
            amount=amount_dec,
            currency="NGN",
            status=status,
            is_credit=is_credit,
            customer_id=customer_id,
            occurred_at=datetime.now(UTC),
            confirmed_at=datetime.now(UTC) if status == "confirmed" else None,
        )
        db.add(tx)
        await db.flush()

        debt = None
        if is_credit and customer_id:
            due_dt = None
            if due_date:
                if isinstance(due_date, datetime):
                    due_dt = due_date if due_date.tzinfo else due_date.replace(tzinfo=UTC)
                else:
                    try:
                        due_dt = datetime.fromisoformat(str(due_date).replace("Z", "+00:00"))
                        if due_dt.tzinfo is None:
                            due_dt = due_dt.replace(tzinfo=UTC)
                    except Exception:
                        pass

            debt = Debt(
                business_id=business_id,
                customer_id=customer_id,
                created_by_member_id=created_by_member_id,
                source_wamid=source_wamid,
                transaction_id=tx.id,
                amount=amount_dec,
                currency="NGN",
                status="outstanding",
                due_date=due_dt,
                notes=f"Credit sale for {item_name or 'goods'}",
            )
            db.add(debt)
            await db.flush()

        low_stock_alert = None
        if status == "confirmed" and record_type == "sale" and item_name and qty_dec:
            norm_item = item_name.strip().lower()
            inv_res = await db.execute(
                select(InventoryItem)
                .where(
                    InventoryItem.business_id == business_id,
                    InventoryItem.normalized_item_name == norm_item,
                )
                .limit(1)
            )
            item = inv_res.scalar_one_or_none()
            if item:
                item.quantity_on_hand -= qty_dec
                item.updated_at = datetime.now(UTC)
                threshold = item.low_stock_threshold
                if threshold is not None and item.quantity_on_hand <= threshold:
                    unit_str = f" {item.unit}" if item.unit else ""
                    low_stock_alert = (
                        f"⚠️ Low Stock Alert: {item.item_name} is down to {item.quantity_on_hand:g}{unit_str} "
                        f"(reorder threshold: {threshold:g}{unit_str})."
                    )
                await db.flush()

        return tx, debt, low_stock_alert

    async def record_audit_log(
        self,
        db: AsyncSession,
        business_id: UUID,
        action: str,
        member_id: UUID | None = None,
        actor_user_id: UUID | None = None,
        entity_type: str | None = None,
        entity_id: UUID | None = None,
        before_state: dict | None = None,
        after_state: dict | None = None,
        wamid: str | None = None,
        metadata: dict | None = None,
    ) -> AuditLog:
        audit = AuditLog(
            business_id=business_id,
            member_id=member_id,
            actor_user_id=actor_user_id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            before_state=before_state,
            after_state=after_state,
            wamid=wamid,
            extra_metadata=metadata or {},
        )
        db.add(audit)
        await db.flush()
        return audit

    async def void_transaction(
        self,
        db: AsyncSession,
        actor: ActorContext,
        business: Business | None = None,
        business_id: UUID | None = None,
        transaction_id: UUID | None = None,
        reason: str | None = None,
    ) -> dict[str, Any]:
        """
        Void or cancel a confirmed transaction with 15-minute grace window enforcement:
        - Staff can only void their own transactions (created_by_member_id == actor.member_id).
        - Staff must void within 15 minutes of occurrence (occurred_at).
        - Owners can void any transaction at any time.
        - Automatically restores deducted inventory and voids associated customer debts.
        """
        now = datetime.now(UTC)
        target_biz_id = business_id or (business.id if business else actor.business_id)

        if transaction_id:
            tx_stmt = select(Transaction).where(
                Transaction.id == transaction_id,
                Transaction.business_id == target_biz_id,
            )
        elif actor.role == "staff":
            tx_stmt = (
                select(Transaction)
                .where(
                    Transaction.business_id == target_biz_id,
                    Transaction.created_by_member_id == actor.member_id,
                    Transaction.status == "confirmed",
                )
                .order_by(Transaction.occurred_at.desc())
                .limit(1)
            )
        else:
            tx_stmt = (
                select(Transaction)
                .where(
                    Transaction.business_id == target_biz_id,
                    Transaction.status == "confirmed",
                )
                .order_by(Transaction.occurred_at.desc())
                .limit(1)
            )

        tx_res = await db.execute(tx_stmt)
        tx = tx_res.scalar_one_or_none()
        if not tx or tx.status != "confirmed":
            return {"success": False, "error": "No confirmed transaction found to void."}

        # Staff grace window & attribution check
        if actor.role == "staff":
            if tx.created_by_member_id and tx.created_by_member_id != actor.member_id:
                return {
                    "success": False,
                    "error": "Permission Denied: Staff members can only void transactions they recorded.",
                }

            tx_time = tx.occurred_at if tx.occurred_at.tzinfo else tx.occurred_at.replace(tzinfo=UTC)
            if (now - tx_time) > timedelta(minutes=15):
                return {
                    "success": False,
                    "error": (
                        "Correction window closed: Staff can only cancel or void entries within 15 minutes of recording. "
                        "Please ask your business owner to adjust or void this transaction."
                    ),
                }

        # Void transaction
        tx.status = "void"
        tx.updated_at = now

        # Restore inventory if sale with item and quantity
        inventory_restored_msg = ""
        if tx.transaction_type == "sale" and tx.item_name and tx.quantity:
            norm_item = tx.item_name.strip().lower()
            inv_res = await db.execute(
                select(InventoryItem).where(
                    InventoryItem.business_id == target_biz_id,
                    InventoryItem.normalized_item_name == norm_item,
                ).limit(1)
            )
            item = inv_res.scalar_one_or_none()
            if item:
                item.quantity_on_hand += tx.quantity
                item.updated_at = now
                inventory_restored_msg = f" Restored {tx.quantity:g} unit(s) of '{item.item_name}' to inventory."

        # Void credit debt if present
        if tx.is_credit:
            d_res = await db.execute(
                select(Debt).where(
                    Debt.transaction_id == tx.id,
                    Debt.business_id == target_biz_id,
                ).limit(1)
            )
            debt = d_res.scalar_one_or_none()
            if debt:
                debt.status = "cancelled"

        # Record audit log
        await self.record_audit_log(
            db=db,
            business_id=target_biz_id,
            action="transaction_voided",
            member_id=actor.member_id,
            entity_type="transaction",
            entity_id=tx.id,
            metadata={
                "transaction_id": str(tx.id),
                "amount": float(tx.amount),
                "record_type": tx.transaction_type,
                "item_name": tx.item_name,
                "reason": reason,
                "role": actor.role,
            },
        )
        await db.flush()

        owner_alert = None
        if actor.role == "staff":
            owner_alert = (
                f"ℹ️ *Transaction Voided*: Staff member *{actor.display_name or 'Staff'}* "
                f"voided a {tx.transaction_type} of ₦{tx.amount:,.2f} for '{tx.item_name or tx.transaction_type}' "
                f"(within 15-min grace window). Reason: {reason or 'Not specified'}."
            )

        return {
            "success": True,
            "transaction_id": str(tx.id),
            "amount": float(tx.amount),
            "record_type": tx.transaction_type,
            "message": f"Success: {tx.transaction_type.capitalize()} of ₦{tx.amount:,.2f} for '{tx.item_name or 'entry'}' has been voided.{inventory_restored_msg}",
            "owner_alert": owner_alert,
            "owner_alert_message": owner_alert,
            "notify_owner": bool(owner_alert),
        }

    async def set_expense_approval_threshold(
        self,
        db: AsyncSession,
        actor: ActorContext,
        business: Business | None = None,
        business_id: UUID | None = None,
        threshold_amount: Decimal | float = 0,
    ) -> dict[str, Any]:
        """Set expense alert threshold for staff members (owner-only)."""
        if actor.role != "owner":
            return {
                "success": False,
                "error": "Permission Denied: Only the business owner can configure expense approval thresholds.",
            }

        target_biz = business
        if target_biz is None and business_id is not None:
            target_biz = await db.get(Business, business_id)
        if target_biz is None and actor.business_id is not None:
            target_biz = await db.get(Business, actor.business_id)

        if not target_biz:
            return {"success": False, "error": "Business not found."}

        thresh_float = float(threshold_amount)
        current_settings = dict(target_biz.settings or {})
        if thresh_float <= 0:
            current_settings.pop("expense_approval_threshold", None)
            msg = "Expense approval threshold has been removed."
        else:
            current_settings["expense_approval_threshold"] = thresh_float
            msg = f"Expense approval threshold set to ₦{thresh_float:,.2f}. You will be alerted whenever staff records an expense at or above this amount."

        target_biz.settings = current_settings
        target_biz.updated_at = datetime.now(UTC)
        await db.flush()
        return {"success": True, "threshold": thresh_float, "message": msg}

