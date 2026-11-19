from collections import defaultdict
from datetime import UTC, datetime
from decimal import Decimal
import logging
import time
from uuid import UUID

from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db_session
from app.models.business import Business
from app.models.customer import Customer
from app.models.debt import Debt
from app.models.debt_recovery import DebtEvent, DebtorConsent, ReminderState
from app.models.transaction import Transaction
from app.services.debt_recovery_service import DebtRecoveryService
from app.utils.currency import format_naira
from app.utils.tokens import (
    generate_action_csrf_token,
    mask_phone_number,
    verify_action_csrf_token,
    verify_debt_token,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["debt_details"])

# In-memory IP and Token rate limiters for /r/ endpoints
_IP_REQUEST_LOG: dict[str, list[float]] = defaultdict(list)
_TOKEN_REQUEST_LOG: dict[str, list[float]] = defaultdict(list)
_IP_FAILURE_LOG: dict[str, list[float]] = defaultdict(list)

RATE_LIMIT_IP_MAX = 30           # 30 req / min per IP
RATE_LIMIT_TOKEN_MAX = 20        # 20 req / min per token
RATE_LIMIT_FAILURE_MAX = 5       # 5 failures / min per IP
RATE_LIMIT_WINDOW = 60.0

STANDARD_R_HEADERS = {
    "Cache-Control": "no-store",
    "X-Robots-Tag": "noindex, nofollow",
    "Referrer-Policy": "no-referrer",
}


def _apply_r_headers(response: Response) -> Response:
    """Attaches required privacy and anti-indexing headers to every /r/ response."""
    for k, v in STANDARD_R_HEADERS.items():
        response.headers[k] = v
    return response


def _raise_r_error(status_code: int, detail: str) -> None:
    """Raises an HTTPException equipped with required /r/ headers."""
    raise HTTPException(
        status_code=status_code,
        detail=detail,
        headers=STANDARD_R_HEADERS,
    )


def _extract_real_client_ip(request: Request) -> str:
    """
    Extracts the true client IP exclusively for /r/ routes.
    When behind a proxy (like Vercel), takes the first IP in X-Forwarded-For.
    Does NOT trust this header for authorization on internal routes.
    """
    xff = request.headers.get("x-forwarded-for")
    if xff:
        parts = [p.strip() for p in xff.split(",") if p.strip()]
        if parts:
            return parts[0]
    return request.client.host if request.client else "unknown"


def _check_r_rate_limits(request: Request, token: str | None = None) -> str:
    """Enforces IP rate limit, per-token limit, and repeated failure limit."""
    client_ip = _extract_real_client_ip(request)
    now = time.time()
    cutoff = now - RATE_LIMIT_WINDOW

    # 1. Repeated failures lockout
    failures = [t for t in _IP_FAILURE_LOG[client_ip] if t > cutoff]
    _IP_FAILURE_LOG[client_ip] = failures
    if len(failures) >= RATE_LIMIT_FAILURE_MAX:
        _raise_r_error(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Too many failed attempts. Access temporarily locked for 60 seconds.",
        )

    # 2. Client IP request limit
    ip_reqs = [t for t in _IP_REQUEST_LOG[client_ip] if t > cutoff]
    _IP_REQUEST_LOG[client_ip] = ip_reqs
    if len(ip_reqs) >= RATE_LIMIT_IP_MAX:
        _raise_r_error(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Too many requests. Please try again later.",
        )
    _IP_REQUEST_LOG[client_ip].append(now)

    # 3. Per-token request limit
    if token:
        token_reqs = [t for t in _TOKEN_REQUEST_LOG[token] if t > cutoff]
        _TOKEN_REQUEST_LOG[token] = token_reqs
        if len(token_reqs) >= RATE_LIMIT_TOKEN_MAX:
            _raise_r_error(
                status.HTTP_429_TOO_MANY_REQUESTS,
                "Too many requests for this document. Please try again later.",
            )
        _TOKEN_REQUEST_LOG[token].append(now)

    return client_ip


def _record_r_failure(client_ip: str) -> None:
    _IP_FAILURE_LOG[client_ip].append(time.time())


def _render_details_html(
    token: str,
    biz_name: str,
    item_desc: str,
    amount_fmt: str,
    due_date_str: str,
    masked_phone: str,
    bank_info: str | None,
    status_str: str,
    csrf_token: str,
) -> str:
    bank_section = ""
    if bank_info:
        bank_section = f"""
        <div class="card settlement-card">
            <h3>Settlement Bank Details</h3>
            <p class="bank-details">{bank_info}</p>
        </div>
        """

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta name="robots" content="noindex, nofollow">
    <title>Purchase & Settlement Details | {biz_name}</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700&display=swap" rel="stylesheet">
    <style>
        :root {{
            --bg-color: #0f172a;
            --surface: #1e293b;
            --surface-border: #334155;
            --text-primary: #f8fafc;
            --text-secondary: #94a3b8;
            --primary: #3b82f6;
            --primary-hover: #2563eb;
            --success: #10b981;
            --warning: #f59e0b;
            --danger: #ef4444;
        }}
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{
            font-family: 'Plus Jakarta Sans', -apple-system, sans-serif;
            background-color: var(--bg-color);
            color: var(--text-primary);
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 1.25rem;
        }}
        .container {{
            width: 100%;
            max-width: 480px;
            background: var(--surface);
            border: 1px solid var(--surface-border);
            border-radius: 16px;
            padding: 2rem;
            box-shadow: 0 20px 25px -5px rgba(0, 0, 0, 0.4);
        }}
        .badge {{
            display: inline-block;
            font-size: 0.75rem;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            padding: 0.35rem 0.75rem;
            border-radius: 9999px;
            background: rgba(59, 130, 246, 0.15);
            color: #60a5fa;
            margin-bottom: 1rem;
        }}
        h1 {{ font-size: 1.5rem; font-weight: 700; margin-bottom: 0.25rem; }}
        .sub {{ color: var(--text-secondary); font-size: 0.875rem; margin-bottom: 1.5rem; }}
        .metrics-grid {{
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 1rem;
            background: rgba(15, 23, 42, 0.6);
            border: 1px solid var(--surface-border);
            border-radius: 12px;
            padding: 1rem;
            margin-bottom: 1.5rem;
        }}
        .metric-label {{ font-size: 0.75rem; color: var(--text-secondary); text-transform: uppercase; }}
        .metric-value {{ font-size: 1.125rem; font-weight: 700; color: var(--text-primary); margin-top: 0.25rem; }}
        .item-row {{ font-size: 0.95rem; color: var(--text-secondary); margin-bottom: 1.25rem; padding-bottom: 0.75rem; border-bottom: 1px solid var(--surface-border); }}
        .card {{ background: rgba(15, 23, 42, 0.5); border: 1px solid var(--surface-border); border-radius: 10px; padding: 1rem; margin-bottom: 1.25rem; }}
        .card h3 {{ font-size: 0.85rem; color: var(--text-secondary); text-transform: uppercase; margin-bottom: 0.5rem; }}
        .bank-details {{ font-size: 0.95rem; font-weight: 600; color: #38bdf8; word-break: break-all; }}
        .btn {{
            display: block;
            width: 100%;
            text-align: center;
            font-family: inherit;
            font-size: 0.95rem;
            font-weight: 600;
            padding: 0.875rem 1rem;
            border-radius: 10px;
            border: none;
            cursor: pointer;
            text-decoration: none;
            transition: all 0.2s ease;
            margin-bottom: 0.75rem;
        }}
        .btn-pdf {{ background: #2563eb; color: #fff; }}
        .btn-pdf:hover {{ background: #1d4ed8; }}
        .btn-paid {{ background: #059669; color: #fff; }}
        .btn-paid:hover {{ background: #047857; }}
        .btn-time {{ background: rgba(245, 158, 11, 0.15); color: #fbbf24; border: 1px solid rgba(245, 158, 11, 0.3); }}
        .btn-time:hover {{ background: rgba(245, 158, 11, 0.25); }}
        .btn-notme {{ background: transparent; color: #94a3b8; font-size: 0.85rem; padding: 0.5rem; }}
        .btn-notme:hover {{ color: #f87171; text-decoration: underline; }}
        .date-picker-box {{ display: none; margin-bottom: 0.75rem; }}
        .date-input {{ width: 100%; padding: 0.75rem; border-radius: 8px; border: 1px solid var(--surface-border); background: var(--bg-color); color: var(--text-primary); margin-bottom: 0.5rem; }}
        .footer-note {{ text-align: center; font-size: 0.75rem; color: var(--text-secondary); margin-top: 1.5rem; }}
        .alert {{ display: none; padding: 0.75rem; border-radius: 8px; font-size: 0.875rem; margin-bottom: 1rem; text-align: center; }}
        .alert-success {{ background: rgba(16, 185, 129, 0.15); color: #34d399; border: 1px solid rgba(16, 185, 129, 0.3); }}
    </style>
</head>
<body>
    <div class="container">
        <span class="badge">Purchase Record</span>
        <h1>{biz_name}</h1>
        <p class="sub">Customer phone: {masked_phone}</p>

        <div class="metrics-grid">
            <div>
                <div class="metric-label">Balance Due</div>
                <div class="metric-value">{amount_fmt}</div>
            </div>
            <div>
                <div class="metric-label">Due Date</div>
                <div class="metric-value">{due_date_str}</div>
            </div>
        </div>

        <div class="item-row">
            <strong>Item Description:</strong> {item_desc}
        </div>

        {bank_section}

        <div id="statusAlert" class="alert alert-success"></div>

        <a href="/r/{token}/pdf" class="btn btn-pdf" target="_blank" rel="noopener">
            📄 Download Invoice (PDF)
        </a>

        <button type="button" class="btn btn-paid" id="btnPaid" onclick="submitAction('paid')">
            ✓ I Have Paid
        </button>

        <button type="button" class="btn btn-time" id="btnTime" onclick="toggleDatePicker()">
            ⏳ Need More Time
        </button>

        <div id="dateBox" class="date-picker-box">
            <input type="date" id="proposedDate" class="date-input" min="{datetime.now(UTC).strftime('%Y-%m-%d')}">
            <button type="button" class="btn btn-paid" onclick="submitNeedTime()">Submit Proposed Date</button>
        </div>

        <button type="button" class="btn btn-notme" onclick="submitAction('not_me')">
            This is not me / Wrong number
        </button>

        <div class="footer-note">
            Compiled from the merchant's Waasz business records.
        </div>
    </div>

    <script>
        const ACTION_CSRF = "{csrf_token}";

        function toggleDatePicker() {{
            const box = document.getElementById('dateBox');
            box.style.display = box.style.display === 'block' ? 'none' : 'block';
        }}

        async function submitNeedTime() {{
            const dt = document.getElementById('proposedDate').value;
            if (!dt) {{
                alert('Please select a proposed payment date.');
                return;
            }}
            await submitAction('need_time', dt);
        }}

        async function submitAction(actionType, proposedDate = '') {{
            const alertBox = document.getElementById('statusAlert');
            try {{
                const formData = new FormData();
                formData.append('action', actionType);
                formData.append('action_token', ACTION_CSRF);
                if (proposedDate) formData.append('proposed_date', proposedDate);

                const res = await fetch('/r/{token}/action', {{
                    method: 'POST',
                    body: formData
                }});
                const data = await res.json();
                if (res.ok) {{
                    alertBox.textContent = data.message || 'Action received. Thank you.';
                    alertBox.style.display = 'block';
                    document.getElementById('dateBox').style.display = 'none';
                }} else {{
                    alert(data.detail || 'An error occurred. Please try again.');
                }}
            }} catch (err) {{
                alert('Could not submit action. Please check your internet connection.');
            }}
        }}
    </script>
</body>
</html>
"""


def _normalize_debt_token(token: str) -> str:
    """Strip accidental Meta URL-template prefix (`{{1}}` or `%7B%7B1%7D%7D`) if present."""
    for prefix in ("{{1}}", "%7B%7B1%7D%7D"):
        if token.startswith(prefix):
            return token[len(prefix):]
    return token


async def _resolve_and_validate_debt(
    db: AsyncSession,
    token: str,
    client_ip: str,
) -> tuple[Debt, Business, Customer | None]:
    """
    Validates token and returns (debt, business, customer).
    Enforces exact status codes:
    - 410 Gone if token expired
    - 404 Not Found if token invalid or debt not found
    - 403 Forbidden if debt settled or consent revoked
    """
    is_valid, debt_id, reason = verify_debt_token(token)
    if reason == "expired":
        _record_r_failure(client_ip)
        _raise_r_error(status.HTTP_410_GONE, "This details link has expired.")

    if not is_valid or not debt_id:
        _record_r_failure(client_ip)
        _raise_r_error(status.HTTP_404_NOT_FOUND, f"This details link is invalid ({reason}).")

    debt = await db.get(Debt, debt_id)
    if not debt:
        _record_r_failure(client_ip)
        _raise_r_error(status.HTTP_404_NOT_FOUND, "Debt record not found.")

    # Status check: 403 if settled
    if getattr(debt, "status", "") in ("paid", "settled"):
        _raise_r_error(status.HTTP_403_FORBIDDEN, "This debt record has already been settled.")

    # Consent check: 403 if revoked
    stmt_c = select(DebtorConsent).where(
        DebtorConsent.debt_id == debt.id,
        DebtorConsent.revoked_at.isnot(None),
    )
    rev_c = (await db.execute(stmt_c)).scalar_one_or_none()
    if rev_c:
        _raise_r_error(status.HTTP_403_FORBIDDEN, "Consent for this record has been revoked.")

    biz = await db.get(Business, debt.business_id)
    cust = await db.get(Customer, debt.customer_id) if debt.customer_id else None
    return debt, biz, cust


@router.get("/r/{token}", response_class=HTMLResponse)
@router.get("/r/{token}/", response_class=HTMLResponse)
async def view_debt_details(
    token: str,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
):
    """
    Renders the secure, privacy-preserving details page for an individual debt.
    Routes with and without trailing slash are supported without redirection.
    """
    token = _normalize_debt_token(token)
    client_ip = _check_r_rate_limits(request, token)
    debt, biz, cust = await _resolve_and_validate_debt(db, token, client_ip)

    # Resolve items and transaction
    tx = None
    if debt.transaction_id:
        tx = await db.get(Transaction, debt.transaction_id)

    biz_name = biz.name if biz else "Merchant"
    item_desc = tx.item_name if tx and tx.item_name else (debt.notes or "Credit purchase")
    amount_fmt = format_naira(debt.amount)
    due_date_str = debt.due_date.strftime("%d %b %Y") if debt.due_date else "Immediate / Net-14"
    masked_phone = mask_phone_number(cust.phone_number if cust else None)

    # Merchant bank settlement details (ONLY if enabled by merchant in settings)
    bank_info = None
    b_settings = dict(biz.settings or {}) if biz else {}
    if b_settings.get("enable_public_bank_details", True):
        b_name = b_settings.get("bank_name", "")
        b_acct = b_settings.get("account_number", "")
        b_owner = b_settings.get("account_name", "") or biz_name
        if b_acct:
            bank_info = f"{b_name} — {b_acct} ({b_owner})" if b_name else f"Account: {b_acct} ({b_owner})"

    csrf_token = generate_action_csrf_token(token, debt.id)

    html = _render_details_html(
        token=token,
        biz_name=biz_name,
        item_desc=item_desc,
        amount_fmt=amount_fmt,
        due_date_str=due_date_str,
        masked_phone=masked_phone,
        bank_info=bank_info,
        status_str=debt.status,
        csrf_token=csrf_token,
    )

    response = HTMLResponse(content=html, status_code=200)
    return _apply_r_headers(response)


@router.get("/r/{token}/pdf")
@router.get("/r/{token}/pdf/")
async def download_debt_pdf(
    token: str,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
):
    """
    Streams the commercial invoice PDF for the debt referenced by the signed token.
    Routes with and without trailing slash are supported without redirection.
    """
    token = _normalize_debt_token(token)
    client_ip = _check_r_rate_limits(request, token)
    debt, biz, cust = await _resolve_and_validate_debt(db, token, client_ip)

    from app.services.receipt_service import ReceiptService
    receipt_svc = ReceiptService()
    pdf_bytes, filename, _ = await receipt_svc.generate_receipt_pdf(
        db, business_id=debt.business_id, transaction_id=debt.transaction_id
    )

    response = Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )
    return _apply_r_headers(response)


@router.post("/r/{token}/action")
@router.post("/r/{token}/action/")
async def handle_debtor_web_action(
    token: str,
    request: Request,
    action: str = Form(...),
    action_token: str = Form(default=""),
    csrf_token: str = Form(default=""),
    proposed_date: str = Form(default=""),
    db: AsyncSession = Depends(get_db_session),
):
    """
    Handles interactive POST button taps from the debtor on the secure details page.
    A debtor action must never run from a simple link click (GET is forbidden).
    Validates token-bound CSRF protection against forged requests.
    """
    token = _normalize_debt_token(token)
    client_ip = _check_r_rate_limits(request, token)
    debt, biz, cust = await _resolve_and_validate_debt(db, token, client_ip)

    # Verify CSRF token bound to this debt and session
    token_to_verify = csrf_token or action_token
    if not verify_action_csrf_token(token, debt.id, token_to_verify):
        _record_r_failure(client_ip)
        _raise_r_error(status.HTTP_403_FORBIDDEN, "Invalid or expired action CSRF token.")

    recovery_svc = DebtRecoveryService()
    cust_name = cust.name if cust else "The customer"
    masked_phone = mask_phone_number(cust.phone_number if cust else None)
    amt_fmt = format_naira(debt.amount)

    if action == "paid":
        await recovery_svc.record_debt_event(
            db,
            business_id=debt.business_id,
            debt_id=debt.id,
            event_type="tap",
            payload={"action": "paid", "source": "web_page", "timestamp": datetime.now(UTC).isoformat()},
        )

        if biz and biz.phone_number:
            from app.services.whatsapp_client import WhatsAppClient
            wa = WhatsAppClient()
            merchant_msg = (
                f"🔔 *Customer Payment Notification:*\n"
                f"{cust_name} ({masked_phone}) indicated on their details card that they have paid {amt_fmt}.\n\n"
                f"Please check your bank account to confirm receipt. Reply *'paid {cust_name}'* to close this debt record."
            )
            await wa.send_text(biz.phone_number, merchant_msg)

        await db.commit()
        res = JSONResponse({"status": "ok", "message": "Thank you! We have notified the shop owner to confirm your payment."})
        return _apply_r_headers(res)

    elif action == "need_time":
        await recovery_svc.record_debt_event(
            db,
            business_id=debt.business_id,
            debt_id=debt.id,
            event_type="tap",
            payload={
                "action": "need_time",
                "proposed_date": proposed_date,
                "source": "web_page",
                "timestamp": datetime.now(UTC).isoformat(),
            },
        )

        # Do not auto-pause on debtor request - notify merchant to approve extension
        if biz and biz.phone_number:
            from app.services.whatsapp_client import WhatsAppClient
            wa = WhatsAppClient()
            date_display = proposed_date or "a later date"
            merchant_msg = (
                f"⏳ *Payment Extension Request:*\n"
                f"{cust_name} ({masked_phone}) requested more time to pay {amt_fmt} and proposed *{date_display}*.\n\n"
                f"To approve this extension, reply *'EXTEND {cust_name} 7 days'* (max 14 days)."
            )
            await wa.send_text(biz.phone_number, merchant_msg)

        await db.commit()
        res = JSONResponse({"status": "ok", "message": f"Thank you. Your request for {proposed_date or 'more time'} has been submitted to the merchant for approval."})
        return _apply_r_headers(res)

    elif action == "not_me":
        await recovery_svc.record_debt_event(
            db,
            business_id=debt.business_id,
            debt_id=debt.id,
            event_type="tap",
            payload={"action": "not_me", "source": "web_page", "timestamp": datetime.now(UTC).isoformat()},
        )

        rem_res = await db.execute(
            select(ReminderState).where(ReminderState.debt_id == debt.id)
        )
        rem_state = rem_res.scalar_one_or_none()
        if rem_state:
            from datetime import timedelta
            rem_state.paused_until = datetime.now(UTC) + timedelta(days=365)
            rem_state.paused_reason = "reported_not_me"

        if biz and biz.phone_number:
            from app.services.whatsapp_client import WhatsAppClient
            wa = WhatsAppClient()
            merchant_msg = (
                f"⚠️ *Identity / Wrong Number Report:*\n"
                f"The recipient of the purchase record for {cust_name} ({amt_fmt}) reported: *'This is not me'*.\n\n"
                f"Automatic reminders have been stopped for this number. Please verify the customer's phone number."
            )
            await wa.send_text(biz.phone_number, merchant_msg)

        await db.commit()
        res = JSONResponse({"status": "ok", "message": "Thank you. Automated messages for this number have been stopped, and the shop has been notified."})
        return _apply_r_headers(res)

    _record_r_failure(client_ip)
    _raise_r_error(status.HTTP_400_BAD_REQUEST, "Unknown action type.")
