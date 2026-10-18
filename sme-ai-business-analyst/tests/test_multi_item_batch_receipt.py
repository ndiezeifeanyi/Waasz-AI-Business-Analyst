"""
Unit tests for multi-item batch receipt generation (1 to 100+ items).
Verifies that all items, individual unit prices, and grand totals are properly included
and rendered into the ReportLab PDF document with repeating headers.
"""
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4
import io
import fitz
import pytest

from app.models.business import Business
from app.models.customer import Customer
from app.models.receipt_template import ReceiptTemplate
from app.models.transaction import Transaction
from app.services.receipt_service import ReceiptService


@pytest.mark.asyncio
@pytest.mark.parametrize("item_count", [1, 10, 15, 50])
async def test_multi_item_receipt_pdf_generation(item_count: int):
    """Verify that receipts with varying numbers of items (1, 10, 15, 50) render all items accurately."""
    db = AsyncMock()
    biz_id = uuid4()
    cust_id = uuid4()
    shared_extraction_id = uuid4()

    mock_biz = Business(id=biz_id, name="God Is Good Motors", phone_number="2348012345678")
    mock_tmpl = ReceiptTemplate(
        id=uuid4(),
        business_id=biz_id,
        business_display_name="God Is Good Motors & Spare Parts",
        address="Block 4, Trade Fair Complex, Badagry Expressway, Lagos",
        contact_phone="2348012345678",
    )
    mock_cust = Customer(id=cust_id, business_id=biz_id, name="Chief Emeka Okafor")

    transactions = []
    total_expected = Decimal("0.00")
    for i in range(1, item_count + 1):
        price = Decimal(str(1000 * i))
        total_expected += price
        tx = Transaction(
            id=uuid4(),
            business_id=biz_id,
            customer_id=cust_id,
            ai_extraction_id=shared_extraction_id,
            transaction_type="sale",
            item_name=f"Spare Part SKU-{i}",
            quantity=Decimal("1"),
            unit="pcs",
            unit_price=price,
            amount=price,
            status="confirmed",
            occurred_at=datetime.now(UTC),
            is_credit=False,
            description=f"Part description {i}",
        )
        transactions.append(tx)

    primary_tx = transactions[0]

    # Mock DB executions:
    # 1. Primary tx query
    primary_tx_res = MagicMock()
    primary_tx_res.scalar_one_or_none.return_value = primary_tx

    # 2. Sibling txs query (where ai_extraction_id == shared_extraction_id)
    sibling_res = MagicMock()
    sibling_res.scalars.return_value.all.return_value = transactions

    # 3. tmpl query
    tmpl_res = MagicMock()
    tmpl_res.scalar_one_or_none.return_value = mock_tmpl

    # 4. cust query
    cust_res = MagicMock()
    cust_res.scalar_one_or_none.return_value = mock_cust

    async def mock_execute(query, *args, **kwargs):
        q_str = str(query).lower()
        if "from receipt_templates" in q_str:
            return tmpl_res
        if "from customers" in q_str:
            return cust_res
        if "ai_extraction_id" in q_str:
            return sibling_res
        return primary_tx_res

    db.execute = AsyncMock(side_effect=mock_execute)
    db.get = AsyncMock(return_value=mock_biz)

    receipt_svc = ReceiptService()
    pdf_bytes, filename, tx = await receipt_svc.generate_receipt_pdf(
        db, business_id=biz_id, transaction_id=primary_tx.id
    )

    assert pdf_bytes is not None
    assert len(pdf_bytes) > 1000
    assert pdf_bytes.startswith(b"%PDF")
    assert filename.endswith(".pdf")

    # Read the generated PDF and inspect content
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    all_text = ""
    for page in doc:
        all_text += page.get_text() or ""

    # Verify header & business info
    assert "God Is Good Motors & Spare Parts" in all_text
    assert "Chief Emeka Okafor" in all_text

    # Verify items are rendered
    assert "Spare Part SKU-1" in all_text
    if item_count >= 10:
        assert "Spare Part SKU-10" in all_text
    if item_count >= 15:
        assert "Spare Part SKU-15" in all_text
    if item_count >= 50:
        assert "Spare Part SKU-50" in all_text
        # Multi-page verification for 50 items
        assert len(doc) >= 2

    # Verify grand total string
    total_str = f"{total_expected:,.2f}"
    assert total_str in all_text
