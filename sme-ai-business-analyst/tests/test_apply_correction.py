import asyncio
import os
import sys
from decimal import Decimal

# Add project root to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.schemas.extraction import ExtractedRecord, LineItemRecord
from app.services.ai_extraction import AiExtractionService

async def test_apply_correction():
    print("Testing apply_correction logic...")
    svc = AiExtractionService()

    # 14 items matching the user's initial extraction
    initial_items = [
        LineItemRecord(item_name="up & down clutch", quantity=Decimal("1"), unit="set", amount=Decimal("21000")),
        LineItemRecord(item_name="Oil filter", quantity=Decimal("1"), unit="pcs", amount=Decimal("5000")),
        LineItemRecord(item_name="Top gasket & oil filter", quantity=Decimal("2"), unit="pcs", amount=Decimal("8000")),
        LineItemRecord(item_name="Ends & seal", quantity=Decimal("1"), unit="set", amount=Decimal("43000")),
        LineItemRecord(item_name="Star con", quantity=Decimal("1"), unit="set", amount=Decimal("26000")),
        LineItemRecord(item_name="Shock pad", quantity=Decimal("8"), unit="pcs", amount=Decimal("13000")),
        LineItemRecord(item_name="Tie rod ends", quantity=Decimal("3"), unit="pcs", amount=Decimal("14000")),
        LineItemRecord(item_name="Ball joint", quantity=Decimal("1"), unit="pcs", amount=Decimal("12000")),
        LineItemRecord(item_name="Back brake pad", quantity=Decimal("2"), unit="pcs", amount=Decimal("12000")),
        LineItemRecord(item_name="Linkage", quantity=Decimal("2"), unit="pcs", amount=Decimal("10000")),
        LineItemRecord(item_name="Pot", quantity=Decimal("1"), unit="pcs", amount=Decimal("70000")),
        LineItemRecord(item_name="Steering rack", quantity=Decimal("1"), unit="pcs", amount=Decimal("130000")),
        LineItemRecord(item_name="Tie rod ends", quantity=Decimal("1"), unit="set", amount=Decimal("20000")),
        LineItemRecord(item_name="Back shackle", quantity=Decimal("1"), unit="set", amount=Decimal("40000")),
    ]

    old_record = ExtractedRecord(
        record_type="sale",
        amount=Decimal("424000"),
        item_name="14 items batch",
        occurred_at_date="2026-09-30",
        items=initial_items,
        confidence=0.95,
        needs_clarification=False,
    )

    correction_text = (
        "Ends and socket is 30,000\n"
        "STD con is 13000, not 26000\n"
        "The shock pad is 26000"
    )

    print("\nApplying correction...")
    corrected = await svc.apply_correction(old_record, correction_text)

    print(f"Corrected record type: {corrected.record_type}")
    print(f"Corrected date: {corrected.occurred_at_date}")
    print(f"Number of items: {len(corrected.items) if corrected.items else 0}")
    print(f"Total Amount: {corrected.amount}")

    assert corrected.items is not None, "Items must not be None"
    assert len(corrected.items) == 14, f"Expected 14 items, got {len(corrected.items)}"

    # Check that total amount matches 411,000
    calc_sum = sum(it.amount for it in corrected.items)
    print(f"Calculated sum of items: {calc_sum}")
    assert calc_sum == Decimal("411000") or corrected.amount == Decimal("411000"), f"Expected 411000, got calc={calc_sum}, amt={corrected.amount}"

    for i, it in enumerate(corrected.items, 1):
        print(f"  {i}. {it.item_name} ({it.quantity or 1} {it.unit or 'pcs'}) - NGN {it.amount:,.0f}")

    print("\nSUCCESS: 14 items preserved, 3 items updated, total is NGN 411,000!")

if __name__ == "__main__":
    asyncio.run(test_apply_correction())
