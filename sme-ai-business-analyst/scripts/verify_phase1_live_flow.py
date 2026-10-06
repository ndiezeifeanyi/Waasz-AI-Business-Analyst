#!/usr/bin/env python3
"""
End-to-End Live Verification for Phase 1: Multi-Staff Identity & Attribution.
Connects to the real live database and verifies:
1. ActorContext resolution for active owner.
2. Creation and ActorContext resolution for a staff member under the same business.
3. Transaction staging attributed to the staff member with source_wamid.
4. Isolation: Confirmation is actionable ONLY for the staff member, NOT the owner.
5. Confirmation execution: Transaction status='confirmed' and created_by_member_id persisted.
6. Idempotency: source_wamid prevents duplicate transactions.
7. Cleanup: Clean rollback/deletion of test artifacts.
"""
import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
import sys
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select, text
from app.core.database import async_session_factory
from app.models.confirmation import Confirmation
from app.models.member import Member
from app.models.transaction import Transaction
from app.schemas.extraction import ExtractedRecord
from app.services.confirmation_service import ConfirmationService
from app.services.ledger_service import LedgerService


async def main() -> None:
    print("=============================================================")
    print("PHASE 1 LIVE VERIFICATION: MULTI-STAFF IDENTITY & ATTRIBUTION")
    print("=============================================================")

    ledger = LedgerService()
    confirmations_svc = ConfirmationService()

    async with async_session_factory() as session:
        # Step 1: Verify owner ActorContext
        owner_phone = "2347065015924"
        print(f"\n[Step 1] Resolving ActorContext for live owner ({owner_phone})...")
        owner_actor = await ledger.resolve_actor_context(session, owner_phone)
        assert owner_actor is not None, f"Failed to resolve actor for owner {owner_phone}"
        assert owner_actor.role == "owner", f"Expected owner role, got {owner_actor.role}"
        print(f" -> Owner resolved: member_id={owner_actor.member_id}, business_id={owner_actor.business_id}, role={owner_actor.role}")

        business_id = owner_actor.business_id

        # Step 2: Create a live test staff member under the same business
        staff_phone = "2348000000099"
        print(f"\n[Step 2] Setting up test staff member ({staff_phone}) in same business...")
        
        # Clean up any leftover test member
        await session.execute(
            text("DELETE FROM members WHERE wa_id = :phone AND business_id = :biz_id"),
            {"phone": staff_phone, "biz_id": business_id},
        )
        await session.flush()

        test_staff_member = Member(
            business_id=business_id,
            wa_id=staff_phone,
            display_name="Live Test Staff",
            role="staff",
            status="active",
        )
        session.add(test_staff_member)
        await session.flush()
        print(f" -> Staff member created: id={test_staff_member.id}")

        # Step 3: Resolve staff ActorContext
        print(f"\n[Step 3] Resolving ActorContext for staff member ({staff_phone})...")
        staff_actor = await ledger.resolve_actor_context(session, staff_phone)
        assert staff_actor is not None, "Failed to resolve staff actor context"
        assert staff_actor.business_id == business_id, "Staff business_id mismatch"
        assert staff_actor.role == "staff", f"Expected staff role, got {staff_actor.role}"
        assert staff_actor.member_id == test_staff_member.id, "Staff member_id mismatch"
        print(f" -> Staff resolved: member_id={staff_actor.member_id}, business_id={staff_actor.business_id}, role={staff_actor.role}")

        # Step 4: Staff stages a transaction
        print(f"\n[Step 4] Staging a transaction attributed to staff member...")
        test_wamid = f"wamid.live_phase1_{uuid4().hex[:8]}"
        extraction_record = ExtractedRecord(
            record_type="sale",
            item_name="Carton of Milk",
            quantity=3,
            amount=15000,
            currency="NGN",
        )
        from app.models.message import WhatsAppMessage
        fake_msg_id = uuid4()
        inbound_msg = WhatsAppMessage(
            id=fake_msg_id,
            business_id=business_id,
            direction="inbound",
            whatsapp_message_id=test_wamid,
            from_phone=staff_phone,
            message_type="text",
            body="Sold 3 cartons of milk for 15000",
            status="received",
            received_at=datetime.now(UTC),
        )
        session.add(inbound_msg)
        await session.flush()

        extraction = await ledger.create_ai_extraction(
            session,
            business_id=business_id,
            message_id=fake_msg_id,
            source_text="Sold 3 cartons of milk for 15000",
            record=extraction_record,
            status="succeeded",
            provider="local_heuristic",
            model="phase1-test",
            input_tokens=0,
            output_tokens=0,
            estimated_cost_usd=Decimal("0.00"),
        )
        
        conf = await ledger.stage_record_for_confirmation(
            session,
            business_id=business_id,
            source_message_id=fake_msg_id,
            extraction=extraction,
            record=extraction_record,
            member_id=staff_actor.member_id,
            source_wamid=test_wamid,
        )
        assert conf.member_id == staff_actor.member_id, "Confirmation member_id not stamped"
        print(f" -> Staged confirmation: id={conf.id}, member_id={conf.member_id}")

        # Step 5: Test pending confirmation scoping
        print(f"\n[Step 5] Testing confirmation scoping and isolation...")
        owner_pending = await confirmations_svc.latest_actionable(session, business_id, member_id=owner_actor.member_id)
        staff_pending = await confirmations_svc.latest_actionable(session, business_id, member_id=staff_actor.member_id)

        assert owner_pending is None or owner_pending.id != conf.id, "LEAK: Owner saw staff's pending confirmation!"
        assert staff_pending is not None and staff_pending.id == conf.id, "Staff failed to find own pending confirmation!"
        print(" -> ISOLATION VERIFIED: Owner sees None; Staff sees their own pending confirmation.")

        # Step 6: Confirm transaction and verify member_id attribution
        print(f"\n[Step 6] Confirming staff transaction and checking attribution...")
        confirm_reply = await confirmations_svc.confirm(session, staff_pending)
        print(f" -> Confirmation reply: {confirm_reply.strip()[:60]}...")

        # Query transaction from live database to verify persistence of attribution
        tx_res = await session.execute(
            select(Transaction).where(Transaction.confirmation_id == conf.id).limit(1)
        )
        saved_tx = tx_res.scalar_one_or_none()
        assert saved_tx is not None, "Transaction not found"
        assert saved_tx.status == "confirmed", f"Expected confirmed status, got {saved_tx.status}"
        assert saved_tx.created_by_member_id == staff_actor.member_id, f"Attribution mismatch: {saved_tx.created_by_member_id} != {staff_actor.member_id}"
        print(f" -> PERSISTENCE VERIFIED: Transaction {saved_tx.id} confirmed with created_by_member_id={saved_tx.created_by_member_id}")

        # Step 7: Test idempotency with source_wamid
        print(f"\n[Step 7] Testing duplicate idempotency with source_wamid...")
        dup_tx, _, _ = await ledger.record_transaction(
            session,
            business_id=business_id,
            record_type="sale",
            amount=Decimal("15000"),
            source_wamid=test_wamid,
            created_by_member_id=staff_actor.member_id,
        )
        assert dup_tx.id == saved_tx.id, "Idempotency failed: duplicate created instead of returning existing transaction!"
        print(" -> IDEMPOTENCY VERIFIED: Duplicate call cleanly returned existing transaction.")

        # Step 8: Cleanup test artifacts
        print(f"\n[Step 8] Cleaning up test records...")
        await session.delete(saved_tx)
        await session.delete(conf)
        await session.delete(extraction)
        await session.delete(inbound_msg)
        await session.delete(test_staff_member)
        await session.commit()
        print(" -> Cleanup complete. Database is clean.")

    print("\n=============================================================")
    print("PHASE 1 ALL VERIFICATIONS PASSED SUCCESSFULLY!")
    print("=============================================================")


if __name__ == "__main__":
    asyncio.run(main())
