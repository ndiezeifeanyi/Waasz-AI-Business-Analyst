import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4
from decimal import Decimal
from datetime import datetime, UTC

from app.services.unified_report_service import sanitize_whatsapp_clean_text, UnifiedReportService
from app.services.agent_service import get_agent_tools, GenerateFinancialChartInput
from app.services.webhook_processor import WhatsAppWebhookProcessor
from app.services.magic_link_service import MagicLinkService
from app.schemas.whatsapp import ParsedWhatsAppMessage, WhatsAppSendResult
from app.models.user import User
from app.models.business import Business
from app.models.transaction import Transaction


def test_sanitize_whatsapp_clean_text():
    raw = "• *Rice* - ₦570,000 (≈ 69.5% of sales)\n• *Rice, Beans, Palm Oil* bundle - ₦250,000 (≈ 30.5% of sales)\nOverall gross margin: *20.7%*.\n'Review Rice pricing' \"Leverage bundled product\" isn't"
    cleaned = sanitize_whatsapp_clean_text(raw)
    assert "*" not in cleaned
    assert '"' not in cleaned
    assert "• Rice - ₦570,000" in cleaned
    assert "Overall gross margin: 20.7%." in cleaned
    assert "isn't" in cleaned  # preserves contraction


def test_agent_tools_include_financial_chart():
    tools = get_agent_tools()
    tool_names = [t.model_config.get("title") for t in tools]
    assert "generate_financial_chart" in tool_names


@pytest.mark.asyncio
async def test_webhook_receipt_quick_reply_not_dropped():
    processor = WhatsAppWebhookProcessor()
    dummy_result = WhatsAppSendResult(message_id="doc123", success=True)
    processor.whatsapp = AsyncMock()
    processor.whatsapp.send_document_bytes = AsyncMock(return_value=dummy_result)
    processor.ledger = AsyncMock()
    processor.confirmations = AsyncMock()
    processor.confirmations.get_latest_pending = AsyncMock(return_value=None)

    db = AsyncMock()
    db.commit = AsyncMock()
    db.flush = AsyncMock()

    test_tx_id = uuid4()
    biz_id = uuid4()
    mock_biz = Business(id=biz_id, name="Test Biz", phone_number="+2347065015924")
    mock_user = User(id=uuid4(), phone_number="+2347065015924", business_id=biz_id, is_active=True)

    db.get = AsyncMock(return_value=mock_user)
    db.execute = AsyncMock()
    scalar_mock = MagicMock()
    scalar_mock.scalar_one_or_none = MagicMock(return_value=mock_user)
    scalar_mock.scalars = MagicMock(return_value=MagicMock(all=MagicMock(return_value=[mock_biz])))
    db.execute.return_value = scalar_mock

    parsed = ParsedWhatsAppMessage(
        message_id="msg123",
        from_phone="+2347065015924",
        to_phone="123456789",
        timestamp=datetime.now(UTC),
        message_type="interactive",
        body="📄 Send receipt",
        interactive_reply_id=f"receipt_{test_tx_id}",
    )

    mock_inbound = MagicMock(id=uuid4(), status="received")
    processor.ledger.get_or_create_business_and_user = AsyncMock(return_value=(mock_biz, mock_user))
    processor.ledger.record_inbound_message = AsyncMock(return_value=mock_inbound)
    processor.ledger.record_outbound_message = AsyncMock()
    processor.access_control = AsyncMock()
    decision_mock = MagicMock(allowed=True, reply_text=None, invite_code=None, reason=None)
    processor.access_control.evaluate_access = AsyncMock(return_value=decision_mock)
    mock_event = MagicMock(id=uuid4(), status="received")

    with patch("app.services.receipt_service.ReceiptService.generate_receipt_pdf", new_callable=AsyncMock) as mock_gen:
        mock_tx = Transaction(id=test_tx_id, is_credit=False, amount=Decimal("250000"))
        mock_gen.return_value = (b"%PDF-1.4", f"receipt_{test_tx_id}.pdf", mock_tx)
        
        await processor.process_message(db, parsed, mock_event)

        # Confirm send_document_bytes was called with the receipt
        processor.whatsapp.send_document_bytes.assert_called_once()


@pytest.mark.asyncio
async def test_deliver_financial_chart_success():
    dummy_result = WhatsAppSendResult(message_id="img123", success=True)
    mock_whatsapp = AsyncMock()
    mock_whatsapp.upload_media = AsyncMock(return_value="media_999")
    mock_whatsapp.send_image = AsyncMock(return_value=dummy_result)

    mock_visual = AsyncMock()
    mock_visual.generate_trend_chart = AsyncMock(return_value=b"fake_png_bytes")

    mock_magic = AsyncMock()
    mock_magic.create_link = AsyncMock(return_value="tok_abc123")

    report_svc = UnifiedReportService(
        whatsapp=mock_whatsapp,
        visual_reports=mock_visual,
        magic_links=mock_magic,
    )

    db = AsyncMock()
    mock_user = User(id=uuid4(), phone_number="+2347065015924", business_id=uuid4())
    db.get = AsyncMock(return_value=mock_user)
    db.commit = AsyncMock()

    res = await report_svc.deliver_financial_chart(db, mock_user.id, period="monthly")
    assert res["status"] == "sent"
    assert res["media_id"] == "media_999"
    assert "/reports/dashboard/tok_abc123" in res["dashboard_url"]
    mock_whatsapp.send_image.assert_called_once()


@pytest.mark.asyncio
async def test_view_dashboard_html_rendering():
    from starlette.requests import Request
    from app.api.routes.client_dashboard import view_dashboard

    scope = {"type": "http", "method": "GET", "path": "/reports/dashboard/tok_test", "headers": []}
    req = Request(scope)
    mock_db = AsyncMock()
    mock_biz = Business(id=uuid4(), name="Alhaji Supermarket", phone_number="+2347065015924")
    mock_db.get = AsyncMock(return_value=mock_biz)

    mock_magic = AsyncMock()
    mock_magic.validate_token = AsyncMock(return_value=mock_biz.id)
    mock_ledger = AsyncMock()

    resp = await view_dashboard(request=req, token="tok_test", db=mock_db, magic_links=mock_magic, ledger=mock_ledger)
    assert resp.status_code == 200
    assert "text/html" in resp.media_type
    body = resp.body.decode("utf-8")
    assert "Financial Dashboard" in body
    assert "Chart.js" in body or "chart.js" in body.lower()
    assert "Alhaji Supermarket" in body


@pytest.mark.asyncio
async def test_view_dashboard_expired_rendering():
    from starlette.requests import Request
    from app.api.routes.client_dashboard import view_dashboard

    scope = {"type": "http", "method": "GET", "path": "/reports/dashboard/tok_expired", "headers": []}
    req = Request(scope)
    mock_db = AsyncMock()
    mock_magic = AsyncMock()
    mock_magic.validate_token = AsyncMock(return_value=None)
    mock_ledger = AsyncMock()

    resp = await view_dashboard(request=req, token="tok_expired", db=mock_db, magic_links=mock_magic, ledger=mock_ledger)
    assert resp.status_code == 403
    assert "text/html" in resp.media_type
    body = resp.body.decode("utf-8")
    assert "Link Expired" in body or "expired" in body.lower()

