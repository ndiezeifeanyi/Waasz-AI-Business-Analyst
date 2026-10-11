import asyncio
from datetime import UTC, datetime
import sys
from decimal import Decimal
from uuid import uuid4

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import pytest
from app.core.database import async_session_factory
from app.models.business import Business
from app.models.user import User
from app.models.transaction import Transaction
from app.schemas.extraction import ExtractedRecord, LineItemRecord
from app.services.confirmation_service import build_confirmation_text, ConfirmationService
from app.services.ledger_service import LedgerService, parse_occurred_at_date


def test_parse_occurred_at_date():
    # Test handwritten dates with quirks
    d1 = parse_occurred_at_date("30/9/026")
    assert d1.year == 2026
    assert d1.month == 9
    assert d1.day == 30

    d2 = parse_occurred_at_date("2026-09-30")
    assert d2.year == 2026
    assert d2.month == 9
    assert d2.day == 30

    d3 = parse_occurred_at_date("30/09/2026")
    assert d3.year == 2026
    assert d3.month == 9
    assert d3.day == 30

    d4 = parse_occurred_at_date("15/10/26")
    assert d4.year == 2026
    assert d4.month == 10
    assert d4.day == 15
    print("test_parse_occurred_at_date PASSED!")


def test_build_confirmation_text_formatting():
    # 20 items list
    items = [
        LineItemRecord(item_name=f"Part #{i}", quantity=Decimal(f"{i}"), unit="pcs", amount=Decimal(f"{i * 1000}"))
        for i in range(1, 21)
    ]
    record = ExtractedRecord(
        record_type="sale",
        amount=Decimal("210000"),
        item_name="20 items batch",
        items=items,
        occurred_at_date="2026-09-30",
        customer_name="Alhaji Danladi",
    )
    conf_text = build_confirmation_text(record)
    print("Formatted Confirmation Text:\n" + conf_text)
    assert "Please confirm 20 item sales:" in conf_text
    assert "📅 Date: 2026-09-30" in conf_text
    assert "👤 Customer: Alhaji Danladi" in conf_text
    assert "1. Part #1 (1 pcs) - ₦1,000" in conf_text
    assert "15. Part #15 (15 pcs) - ₦15,000" in conf_text
    assert "... and 5 more items" in conf_text
    assert "💰 Grand Total: ₦210,000" in conf_text
    assert "Reply 1=Yes, 2=Edit, 3=Cancel." in conf_text
    print("test_build_confirmation_text_formatting PASSED!")


@pytest.mark.asyncio
async def test_end_to_end_batch_staging_and_confirmation():
    ledger = LedgerService()
    confirmations = ConfirmationService()

    async with async_session_factory() as db:
        # Create test business and user
        unique_suffix = uuid4().hex[:6]
        phone = f"+2348099{unique_suffix[:6]}"
        biz, user = await ledger.get_or_create_business_and_user(db, phone)

        # Build 18 batch items
        items = [
            LineItemRecord(
                item_name=f"Engine Oil Filter #{i}",
                quantity=Decimal("2"),
                unit="pcs",
                amount=Decimal(f"{i * 2500}"),
            )
            for i in range(1, 19)
        ]
        total_amount = sum(it.amount for it in items)

        record = ExtractedRecord(
            record_type="sale",
            amount=total_amount,
            item_name="18 items batch",
            items=items,
            occurred_at_date="30/9/026",
            customer_name="Chief Emeka",
        )

        # Create real inbound WhatsAppMessage to satisfy foreign key
        from app.schemas.whatsapp import ParsedWhatsAppMessage
        parsed_inbound = ParsedWhatsAppMessage(
            message_id=f"wamid.{uuid4().hex}",
            from_phone=phone,
            message_type="image",
            body="Handwritten paper with 18 items",
            timestamp=datetime.now(UTC),
        )
        inbound_msg = await ledger.record_inbound_message(db, parsed_inbound, biz, user)
        await db.flush()

        extraction = await ledger.create_ai_extraction(
            db,
            biz.id,
            inbound_msg.id,
            "Handwritten paper with 18 items",
            record,
            status="succeeded",
            provider="local_heuristic",
            model="test-batch",
        )

        # Stage record
        confirmation = await ledger.stage_record_for_confirmation(
            db,
            biz.id,
            inbound_msg.id,
            extraction,
            record,
            source_wamid=parsed_inbound.message_id,
        )
        await db.commit()

        assert confirmation.status == "pending"
        assert "... and 3 more items" in confirmation.confirmation_text

        # Verify staged transactions in database
        from sqlalchemy import select
        tx_res = await db.execute(
            select(Transaction).where(
                (Transaction.confirmation_id == confirmation.id)
                | (Transaction.ai_extraction_id == confirmation.ai_extraction_id)
            ).order_by(Transaction.created_at.asc())
        )
        txs = tx_res.scalars().all()
        assert len(txs) == 18

        # First transaction links confirmation_id and source_wamid, rest do not violate unique constraint
        assert txs[0].confirmation_id == confirmation.id
        assert txs[0].source_wamid == parsed_inbound.message_id
        for i in range(1, 18):
            assert txs[i].confirmation_id is None
            assert txs[i].source_wamid is None
            assert txs[i].ai_extraction_id == confirmation.ai_extraction_id

        # Check date parsed correctly
        for tx in txs:
            assert tx.occurred_at.year == 2026
            assert tx.occurred_at.month == 9
            assert tx.occurred_at.day == 30
            assert tx.status == "pending_confirmation"

        print(f"Staged {len(txs)} transactions successfully with occurred_at {txs[0].occurred_at}!")

        # Confirm the batch
        confirm_reply = await confirmations.confirm(db, confirmation)
        await db.commit()

        print(f"Confirmation reply: {confirm_reply}")
        assert "Done! I have confirmed and recorded all 18 sales" in confirm_reply

        # Check that all 18 transactions are now confirmed
        tx_res2 = await db.execute(
            select(Transaction).where(
                (Transaction.confirmation_id == confirmation.id)
                | (Transaction.ai_extraction_id == confirmation.ai_extraction_id)
            )
        )
        txs2 = tx_res2.scalars().all()
        assert len(txs2) == 18
        for tx in txs2:
            assert tx.status == "confirmed"
            assert tx.confirmed_at is not None

        print("All 18 transactions verified confirmed in DB!")

        # Now test cancellation with another batch
        items_cancel = [
            LineItemRecord(item_name="Brake Pad", amount=Decimal("15000")),
            LineItemRecord(item_name="Brake Fluid", amount=Decimal("5000")),
        ]
        cancel_record = ExtractedRecord(
            record_type="expense",
            amount=Decimal("20000"),
            items=items_cancel,
        )
        ext2 = await ledger.create_ai_extraction(
            db, biz.id, inbound_msg.id, "Expense note", cancel_record
        )
        conf2 = await ledger.stage_record_for_confirmation(
            db, biz.id, inbound_msg.id, ext2, cancel_record
        )
        await db.commit()

        cancel_reply = await confirmations.cancel(db, conf2)
        await db.commit()

        assert "Record discarded" in cancel_reply

        tx_res3 = await db.execute(
            select(Transaction).where(
                (Transaction.confirmation_id == conf2.id)
                | (Transaction.ai_extraction_id == conf2.ai_extraction_id)
            )
        )
        txs3 = tx_res3.scalars().all()
        assert len(txs3) == 2
        for tx in txs3:
            assert tx.status == "rejected"

        print("Batch cancellation verified in DB!")


if __name__ == "__main__":
    test_parse_occurred_at_date()
    test_build_confirmation_text_formatting()
    asyncio.run(test_end_to_end_batch_staging_and_confirmation())
    print("\nALL BATCH TRANSACTION TESTS PASSED SUCCESSFULLY! 🚀")
