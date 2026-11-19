"""
Tests for Debtor Details Proxy, Security Headers, Client IP Rate Limiting,
and Token-bound CSRF Actions.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.deps import get_db_session
from app.core.config import settings
from app.main import app
from app.models.business import Business
from app.models.customer import Customer
from app.models.debt import Debt
from app.models.debt_recovery import DebtorConsent
from app.services.debt_monitoring_service import (
    DebtMonitoringService,
    clear_all_paused_templates,
    is_template_paused,
    pause_template,
)
from app.utils.tokens import (
    generate_action_csrf_token,
    generate_debt_token,
    verify_action_csrf_token,
)


@pytest.fixture(autouse=True)
def cleanup():
    clear_all_paused_templates()
    app.dependency_overrides.clear()
    yield
    clear_all_paused_templates()
    app.dependency_overrides.clear()


@pytest.fixture
def test_setup():
    biz_id = uuid4()
    cust_id = uuid4()
    debt_id = uuid4()

    biz = Business(
        id=biz_id,
        name="Amaka Stores",
        phone_number="2348011112222",
        settings={"bank_details": "GTBank 0123456789 - Amaka Stores"},
    )
    cust = Customer(
        id=cust_id,
        business_id=biz_id,
        name="Chidi Obi",
        phone_number="+2348039998877",
    )
    debt = Debt(
        id=debt_id,
        business_id=biz_id,
        customer_id=cust_id,
        amount=Decimal("25000.00"),
        currency="NGN",
        status="outstanding",
        due_date=datetime.now(UTC) + timedelta(days=14),
        notes="2 bags of wheat flour",
    )
    consent = DebtorConsent(
        id=uuid4(),
        business_id=biz_id,
        debt_id=debt_id,
        debtor_phone="+2348039998877",
        revoked_at=datetime.now(UTC),
    )
    token = generate_debt_token(debt_id)
    return {
        "biz": biz,
        "cust": cust,
        "debt": debt,
        "consent": consent,
        "token": token,
    }


@pytest.mark.asyncio
async def test_page_loads_through_proxy_with_required_headers(test_setup):
    """
    Verifies:
    - Page loads through simulated Vercel proxy headers.
    - Path works with and without trailing slash (/r/{token} and /r/{token}/) without redirect.
    - Security headers present: Cache-Control: no-store, X-Robots-Tag: noindex, nofollow, Referrer-Policy: no-referrer.
    - No duckdns URLs appear anywhere in the HTML.
    - Links stay on /r/ and are relative.
    """
    token = test_setup["token"]
    mock_db = AsyncMock()

    mock_db.get.side_effect = lambda model, obj_id: {
        Debt: test_setup["debt"],
        Customer: test_setup["cust"],
        Business: test_setup["biz"],
    }.get(model)

    # Consent not revoked
    c_res = MagicMock()
    c_res.scalar_one_or_none.return_value = None
    mock_db.execute.return_value = c_res

    async def _get_mock_db():
        yield mock_db

    app.dependency_overrides[get_db_session] = _get_mock_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # 1. Test without trailing slash
        resp1 = await ac.get(
            f"/r/{token}",
            headers={
                "X-Forwarded-For": "203.0.113.195, 76.76.21.21",
                "X-Forwarded-Host": "waaszai.com",
                "X-Forwarded-Proto": "https",
                "Host": "waasz-sme-api.duckdns.org",
            },
        )
        assert resp1.status_code == 200
        assert resp1.headers["cache-control"] == "no-store"
        assert resp1.headers["x-robots-tag"] == "noindex, nofollow"
        assert resp1.headers["referrer-policy"] == "no-referrer"
        assert "duckdns" not in resp1.text.lower()
        assert f"/r/{token}/pdf" in resp1.text
        assert f"/r/{token}/action" in resp1.text

        # 2. Test with trailing slash (must NOT redirect)
        resp2 = await ac.get(
            f"/r/{token}/",
            headers={
                "X-Forwarded-For": "203.0.113.195, 76.76.21.21",
                "X-Forwarded-Host": "waaszai.com",
                "X-Forwarded-Proto": "https",
            },
        )
        assert resp2.status_code == 200
        assert resp2.headers["cache-control"] == "no-store"


@pytest.mark.asyncio
async def test_pdf_endpoint_through_proxy_without_redirect(test_setup):
    """
    Verifies that /r/{token}/pdf works with and without trailing slash,
    contains required privacy headers, and produces a valid PDF.
    """
    token = test_setup["token"]
    mock_db = AsyncMock()
    mock_db.get.side_effect = lambda model, obj_id: {
        Debt: test_setup["debt"],
        Customer: test_setup["cust"],
        Business: test_setup["biz"],
    }.get(model)

    c_res = MagicMock()
    c_res.scalar_one_or_none.return_value = None
    mock_db.execute.return_value = c_res

    async def _get_mock_db():
        yield mock_db

    app.dependency_overrides[get_db_session] = _get_mock_db

    transport = ASGITransport(app=app)
    with patch("app.services.receipt_service.ReceiptService.generate_receipt_pdf", new_callable=AsyncMock) as mock_pdf:
        mock_pdf.return_value = (b"%PDF-1.4 simulated pdf stream", "receipt.pdf", "application/pdf")
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # Without trailing slash
            resp = await ac.get(
                f"/r/{token}/pdf",
                headers={"X-Forwarded-For": "203.0.113.55"},
            )
            assert resp.status_code == 200
            assert resp.headers["content-type"] == "application/pdf"
            assert resp.headers["cache-control"] == "no-store"
            assert resp.headers["x-robots-tag"] == "noindex, nofollow"
            assert resp.content.startswith(b"%PDF")

            # With trailing slash
            resp_slash = await ac.get(
                f"/r/{token}/pdf/",
                headers={"X-Forwarded-For": "203.0.113.55"},
            )
            assert resp_slash.status_code == 200
            assert resp_slash.headers["content-type"] == "application/pdf"


@pytest.mark.asyncio
async def test_expired_token_returns_410():
    """Verifies that an expired token returns 410 Gone with security headers."""
    expired_token = generate_debt_token(uuid4(), expires_in_seconds=-3600)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get(
            f"/r/{expired_token}",
            headers={"X-Forwarded-For": "198.51.100.1"},
        )
        assert resp.status_code == 410
        assert resp.headers["cache-control"] == "no-store"
        assert resp.headers["x-robots-tag"] == "noindex, nofollow"
        assert "expired" in resp.text.lower()


@pytest.mark.asyncio
async def test_settled_or_revoked_token_returns_403(test_setup):
    """Verifies that settled debts or revoked consents return 403 Forbidden."""
    token = test_setup["token"]

    # 1. Settled debt
    settled_debt = test_setup["debt"]
    settled_debt.status = "paid"

    mock_db = AsyncMock()
    mock_db.get.side_effect = lambda model, obj_id: {
        Debt: settled_debt,
        Customer: test_setup["cust"],
        Business: test_setup["biz"],
    }.get(model)

    c_res = MagicMock()
    c_res.scalar_one_or_none.return_value = None
    mock_db.execute.return_value = c_res

    async def _get_mock_db():
        yield mock_db

    app.dependency_overrides[get_db_session] = _get_mock_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get(f"/r/{token}")
        assert resp.status_code == 403
        assert resp.headers["cache-control"] == "no-store"
        assert "settled" in resp.text.lower()

        # 2. Revoked consent
        settled_debt.status = "outstanding"
        c_res.scalar_one_or_none.return_value = test_setup["consent"]  # active revoked consent

        resp_rev = await ac.get(f"/r/{token}")
        assert resp_rev.status_code == 403
        assert "revoked" in resp_rev.text.lower()


@pytest.mark.asyncio
async def test_action_requires_post_and_csrf_protection(test_setup):
    """
    Verifies:
    - GET request on action returns 405 Method Not Allowed (simple link click cannot trigger action).
    - POST with invalid CSRF token returns 403 Forbidden.
    - POST with valid CSRF token succeeds.
    """
    token = test_setup["token"]
    debt = test_setup["debt"]
    valid_csrf = generate_action_csrf_token(token, debt.id)

    mock_db = AsyncMock()
    mock_db.get.side_effect = lambda model, obj_id: {
        Debt: test_setup["debt"],
        Customer: test_setup["cust"],
        Business: test_setup["biz"],
    }.get(model)

    c_res = MagicMock()
    c_res.scalar_one_or_none.return_value = None
    mock_db.execute.return_value = c_res

    async def _get_mock_db():
        yield mock_db

    app.dependency_overrides[get_db_session] = _get_mock_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # 1. Simple link click (GET) returns 405
        get_resp = await ac.get(f"/r/{token}/action")
        assert get_resp.status_code == 405

        # 2. POST with missing/invalid CSRF returns 403
        bad_post = await ac.post(
            f"/r/{token}/action",
            data={"action": "paid", "csrf_token": "forged_csrf_xyz"},
            headers={"X-Forwarded-For": "203.0.113.12"},
        )
        assert bad_post.status_code == 403
        assert "csrf" in bad_post.text.lower()

        # 3. POST with valid CSRF succeeds
        with patch("app.services.whatsapp_client.WhatsAppClient.send_text", new_callable=AsyncMock) as mock_send, \
             patch("app.services.debt_recovery_service.DebtRecoveryService.record_debt_event", new_callable=AsyncMock):
            good_post = await ac.post(
                f"/r/{token}/action",
                data={"action": "paid", "csrf_token": valid_csrf},
                headers={"X-Forwarded-For": "203.0.113.12"},
            )
            assert good_post.status_code == 200
            assert good_post.headers["cache-control"] == "no-store"
            assert "notified the shop owner" in good_post.text


@pytest.mark.asyncio
async def test_rate_limiting_uses_forwarded_client_ip():
    """
    Verifies that rate limiting uses the first IP from X-Forwarded-For,
    and locks out after repeated failures on invalid tokens.
    """
    from app.api.routes.debt_details import _IP_FAILURE_LOG

    client_ip = f"198.51.100.{uuid4().hex[:4]}"
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # Send 5 invalid token requests from client_ip
        for _ in range(5):
            r = await ac.get(
                f"/r/invalid_token_{uuid4().hex[:6]}",
                headers={"X-Forwarded-For": f"{client_ip}, 76.76.21.21"},
            )
            assert r.status_code in (404, 429)

        # 6th request must be locked out with 429
        lock_resp = await ac.get(
            f"/r/any_token",
            headers={"X-Forwarded-For": f"{client_ip}, 76.76.21.21"},
        )
        assert lock_resp.status_code == 429
        assert lock_resp.headers["cache-control"] == "no-store"


@pytest.mark.asyncio
async def test_template_auto_pause_and_alert():
    """
    Verifies task (5):
    - DebtMonitoringService automatically pauses template when failure rate >= 8% (thresh 0.08)
      or STOP rate >= 3% (thresh 0.03).
    - Founder alert message is sent to founder_phone (2347065015924).
    - DebtorSendService blocks sends when template is paused.
    """
    mock_db = AsyncMock()
    mock_whatsapp = AsyncMock()

    monitoring = DebtMonitoringService(whatsapp=mock_whatsapp)

    # 1. Test failure rate >= 8% (e.g., 2 failures out of 10 attempts = 20%)
    mock_first = MagicMock(total_attempts=10, failed_count=2)
    mock_res1 = MagicMock()
    mock_res1.first.return_value = mock_first

    mock_res2 = MagicMock()
    mock_res2.scalar.return_value = 0  # 0 STOPs

    mock_db.execute.side_effect = [mock_res1, mock_res2]

    res = await monitoring.check_template_health_and_auto_pause(mock_db, "iou_due_soon_v2")
    assert res["auto_paused"] is True
    assert res["is_paused"] is True
    assert is_template_paused("iou_due_soon_v2") is True
    # Verify founder alert was sent
    mock_whatsapp.send_text.assert_awaited_once()
    alert_args = mock_whatsapp.send_text.await_args[0]
    assert alert_args[0] == settings.founder_phone
    assert "AUTOMATIC TEMPLATE PAUSE ALERT" in alert_args[1]
    assert "iou_due_soon_v2" in alert_args[1]

    # 2. Verify DebtorSendService blocks send of paused template
    from app.services.debtor_send_service import DebtorSendService
    send_svc = DebtorSendService(whatsapp=mock_whatsapp)
    ok, reason, msg = await send_svc.send_debtor_notice(
        mock_db,
        business_id=uuid4(),
        debt_id=uuid4(),
        stage="due_soon",
        template_name="iou_due_soon_v2",
    )
    assert ok is False
    assert reason == "template_paused"
