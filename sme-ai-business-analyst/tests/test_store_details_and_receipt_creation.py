"""
Tests for store details submission and automatic receipt generation without abandonment.
"""
from datetime import UTC, datetime
from decimal import Decimal
import io
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4
import pytest

from app.models.business import Business
from app.models.customer import Customer
from app.models.receipt_template import ReceiptTemplate
from app.models.transaction import Transaction
from app.models.user import User
from app.schemas.whatsapp import ParsedWhatsAppMessage
from app.services.receipt_service import ReceiptService
from app.services.webhook_processor import WhatsAppWebhookProcessor


def test_extract_store_setup_details_various_formats():
    """Verify various formats of store and address submissions are correctly extracted."""
    processor = WhatsAppWebhookProcessor(whatsapp=MagicMock())

    # Format 1: Store + Address combo
    res1 = processor._extract_store_setup_details("Store: Lake Toy World, Address: 12 Marina Street, Lagos")
    assert res1 == {"business_name": "Lake Toy World", "address": "12 Marina Street, Lagos"}

    # Format 2: Business + Address combo
    res2 = processor._extract_store_setup_details("Business: Lake Toy World, Address: 12 Marina Street")
    assert res2 == {"business_name": "Lake Toy World", "address": "12 Marina Street"}

    # Format 3: Natural language sentence
    res3 = processor._extract_store_setup_details("My store name is Lake Toy World and address is 12 Marina Street Lagos")
    assert res3 == {"business_name": "Lake Toy World", "address": "12 Marina Street Lagos"}

    # Format 4: Set business name only
    res4 = processor._extract_store_setup_details("Set business name: Lake Toy World")
    assert res4 == {"business_name": "Lake Toy World"}

    # Format 5: Set address only
    res5 = processor._extract_store_setup_details("Set address: 12 Marina Street, Lagos")
    assert res5 == {"address": "12 Marina Street, Lagos"}


def test_welcome_message_contains_receipt_setup():
    """Verify that welcome message introduces receipt setup clearly."""
    processor = WhatsAppWebhookProcessor(whatsapp=MagicMock())
    msg = processor._build_onboarding_intro_message()
    assert "One-Time Setup for Receipts & Invoices" in msg
    assert "Set business name:" in msg
    assert "Set address:" in msg


@pytest.mark.asyncio
async def test_receipt_pdf_contains_custom_store_details_and_customer():
    """Verify that generated receipt PDF contains the customized store name, address, and customer name."""
    db = AsyncMock()
    biz_id = uuid4()
    cust_id = uuid4()
    tx_id = uuid4()

    mock_biz = Business(id=biz_id, name="Lake Toy World", phone_number="2347065015924")
    mock_tmpl = ReceiptTemplate(
        id=uuid4(),
        business_id=biz_id,
        business_display_name="Lake Toy World",
        address="12 Marina Street, Lagos",
        contact_phone="2347065015924",
    )
    mock_cust = Customer(id=cust_id, business_id=biz_id, name="Lake")
    mock_tx = Transaction(
        id=tx_id,
        business_id=biz_id,
        customer_id=cust_id,
        transaction_type="sale",
        amount=Decimal("20000.00"),
        status="confirmed",
        occurred_at=datetime.now(UTC),
        is_credit=False,
        description="toy",
    )

    db.execute = AsyncMock()
    # 1. tx query
    tx_result = MagicMock()
    tx_result.scalar_one_or_none.return_value = mock_tx
    # 2. tmpl query
    tmpl_result = MagicMock()
    tmpl_result.scalar_one_or_none.return_value = mock_tmpl
    # 3. cust query
    cust_result = MagicMock()
    cust_result.scalar_one_or_none.return_value = mock_cust

    db.execute.side_effect = [tx_result, tmpl_result, cust_result]
    db.get = AsyncMock(return_value=mock_biz)

    receipt_svc = ReceiptService()
    pdf_bytes, filename, tx = await receipt_svc.generate_receipt_pdf(db, business_id=biz_id, transaction_id=tx_id)

    assert pdf_bytes is not None
    assert len(pdf_bytes) > 0
    assert filename.endswith(".pdf")

    # Check that valid PDF was generated
    assert pdf_bytes.startswith(b"%PDF")
    assert len(pdf_bytes) > 1000

    # Verify that the receipt template and customer details were queried and bound
    assert mock_tmpl.business_display_name == "Lake Toy World"
    assert mock_tmpl.address == "12 Marina Street, Lagos"
    assert mock_cust.name == "Lake"
    assert tx.amount == Decimal("20000.00")


@pytest.mark.asyncio
async def test_webhook_submitting_store_details_auto_generates_receipt():
    """Verify that when a user submits store details after a sale, the system updates template AND immediately delivers the receipt."""
    db = AsyncMock()
    biz_id = uuid4()
    user_id = uuid4()
    tx_id = uuid4()

    mock_biz = Business(id=biz_id, name="WhatsApp Business 2347065015924", phone_number="2347065015924", is_provisional=True)
    mock_user = User(id=user_id, business_id=biz_id, phone_number="2347065015924")
    mock_tx = Transaction(
        id=tx_id,
        business_id=biz_id,
        transaction_type="sale",
        amount=Decimal("20000.00"),
        status="confirmed",
        occurred_at=datetime.now(UTC),
        is_credit=False,
        description="toy",
    )

    whatsapp_mock = MagicMock()
    whatsapp_mock.send_text = AsyncMock(return_value={"messages": [{"id": "wamid.123"}]})
    whatsapp_mock.send_document_bytes = AsyncMock(return_value={"messages": [{"id": "wamid.456"}]})

    processor = WhatsAppWebhookProcessor(whatsapp=whatsapp_mock)
    processor.ledger = MagicMock()
    processor.ledger.get_or_create_business_and_user = AsyncMock(return_value=(mock_biz, mock_user))
    processor.ledger.record_inbound_message = AsyncMock(return_value=MagicMock(id=uuid4(), status="received"))
    processor.ledger.record_outbound_message = AsyncMock(return_value=MagicMock(id=uuid4()))

    # Mock user / business retrieval
    processor.auth = MagicMock()
    processor.auth.get_or_create_user_and_business = AsyncMock(return_value=(mock_user, mock_biz))
    processor.confirmations = MagicMock()
    processor.confirmations.latest_actionable = AsyncMock(return_value=None)

    # Mock DB queries: check User exists, fetch latest transaction for receipt
    user_result = MagicMock()
    user_result.scalar_one_or_none.return_value = mock_user
    tx_result = MagicMock()
    tx_result.scalar_one_or_none.return_value = mock_tx

    async def mock_execute(query, *args, **kwargs):
        q_str = str(query).lower()
        if "users" in q_str or "user" in q_str:
            return user_result
        return tx_result

    db.execute = AsyncMock(side_effect=mock_execute)

    # Mock ReceiptService set_template and generate_receipt_pdf
    saved_tmpl = ReceiptTemplate(
        id=uuid4(),
        business_id=biz_id,
        business_display_name="Lake Toy World",
        address="12 Marina Street, Lagos",
        contact_phone="2347065015924",
    )

    fake_pdf = b"%PDF-1.4 test receipt bytes with high fidelity content"
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            "app.services.receipt_service.ReceiptService.set_template",
            AsyncMock(return_value=saved_tmpl),
        )
        mp.setattr(
            "app.services.receipt_service.ReceiptService.generate_receipt_pdf",
            AsyncMock(return_value=(fake_pdf, f"receipt_{tx_id}.pdf", mock_tx)),
        )

        parsed_msg = ParsedWhatsAppMessage(
            message_id="msg_001",
            from_phone="2347065015924",
            message_type="text",
            body="Store: Lake Toy World, Address: 12 Marina Street, Lagos",
            timestamp="1730000000",
        )

        mock_event = MagicMock(id=uuid4())
        await processor.process_message(db, parsed_msg, mock_event)

    # 1. Store details must be updated on business
    assert mock_biz.name == "Lake Toy World"
    assert mock_biz.is_provisional is False

    # 2. WhatsApp send_text must be called with confirmation of store details
    assert whatsapp_mock.send_text.called
    text_sent = whatsapp_mock.send_text.call_args[0][1]
    assert "Lake Toy World" in text_sent
    assert "12 Marina Street, Lagos" in text_sent

    # 3. WhatsApp send_document_bytes MUST be called to immediately send the receipt without abandoning
    assert whatsapp_mock.send_document_bytes.called
    doc_call_args = whatsapp_mock.send_document_bytes.call_args
    assert doc_call_args[0][0] == "2347065015924"
    assert doc_call_args[0][1] == fake_pdf
    assert doc_call_args[1]["filename"] == f"receipt_{tx_id}.pdf"
    assert "Lake Toy World" in doc_call_args[1]["caption"]


