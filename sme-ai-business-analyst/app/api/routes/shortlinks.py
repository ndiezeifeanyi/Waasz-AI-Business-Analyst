"""
Shortlink & Digital IOU Redirection Service.
Provides short branded URLs (e.g., https://waaszai.com/i/WZ-1A2B3C) that resolve
credit transactions, format concise WhatsApp dispatch links, and display verified web cards.
"""

from datetime import datetime
from decimal import Decimal
import logging
import urllib.parse
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import cast, select, String
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db_session
from app.models.business import Business
from app.models.customer import Customer
from app.models.debt import Debt
from app.models.transaction import Transaction
from app.utils.currency import format_naira
from app.utils.phone import normalize_phone

logger = logging.getLogger(__name__)

router = APIRouter(tags=["shortlinks"])


def _extract_merchant_bank_info(biz: Business | None) -> str:
    """Extract clean bank settlement details from business settings."""
    if not biz or not biz.settings or not isinstance(biz.settings, dict):
        return f"Account: {biz.name if biz else 'Merchant'}"
    b_name = biz.settings.get("bank_name", "")
    b_acct = biz.settings.get("account_number", "")
    b_owner = biz.settings.get("account_name", "")
    if b_acct:
        parts = [p for p in [b_name, b_acct, f"({b_owner})" if b_owner else ""] if p]
        return " • ".join(parts)
    return f"Account: {biz.name}"


@router.get("/i/{code}", response_class=HTMLResponse)
@router.get("/iou/{code}", response_class=HTMLResponse)
async def resolve_iou_shortlink(
    code: str,
    request: Request,
    direct: bool = Query(default=False),
    db: AsyncSession = Depends(get_db_session),
):
    """
    Resolves an IOU reference code (e.g. WZ-9EEDB3FF or 9eedb3ff).
    - If direct=True (or ?redirect=1): Immediately 302-redirects to wa.me.
    - Default: Displays a verified responsive digital IOU card with auto-forward to WhatsApp.
    """
    clean_code = code.strip().upper()
    hex_prefix = clean_code.replace("WZ-", "").replace("WZ", "").strip().lower()

    if len(hex_prefix) < 4:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid IOU reference code format.",
        )

    # 1. Search Transaction table first
    tx_stmt = (
        select(Transaction)
        .where(cast(Transaction.id, String).ilike(f"{hex_prefix}%"))
        .order_by(Transaction.occurred_at.desc())
        .limit(1)
    )
    tx = (await db.execute(tx_stmt)).scalar_one_or_none()

    # 2. Search Debt table
    debt = None
    if tx:
        debt_stmt = select(Debt).where(Debt.transaction_id == tx.id).limit(1)
        debt = (await db.execute(debt_stmt)).scalar_one_or_none()
    else:
        debt_stmt = (
            select(Debt)
            .where(cast(Debt.id, String).ilike(f"{hex_prefix}%"))
            .order_by(Debt.created_at.desc())
            .limit(1)
        )
        debt = (await db.execute(debt_stmt)).scalar_one_or_none()
        if debt and debt.transaction_id:
            tx = await db.get(Transaction, debt.transaction_id)

    if not tx and not debt:
        return HTMLResponse(
            status_code=404,
            content=_render_not_found_page(clean_code),
        )

    # 3. Resolve Business & Customer
    biz_id = debt.business_id if debt else tx.business_id
    biz = await db.get(Business, biz_id) if biz_id else None

    cust_id = debt.customer_id if debt else (tx.customer_id if tx else None)
    cust = await db.get(Customer, cust_id) if cust_id else None

    # 4. Extract metrics
    amount = debt.amount if debt else (tx.amount if tx else Decimal("0"))
    amount_fmt = format_naira(amount)
    cust_name = cust.name if cust else "Valued Customer"
    cust_phone = cust.phone_number if cust else ""
    norm_phone = normalize_phone(cust_phone) if cust_phone else ""
    biz_name = biz.name if biz else "Waasz Merchant"
    item_desc = tx.item_name if tx and tx.item_name else "Goods & Services"
    bank_info = _extract_merchant_bank_info(biz)

    if debt and debt.due_date:
        due_date_str = debt.due_date.strftime("%d %b %Y")
    elif tx and tx.occurred_at:
        from datetime import timedelta
        due_date_str = (tx.occurred_at + timedelta(days=14)).strftime("%d %b %Y")
    else:
        due_date_str = "Immediate / Net-14"

    # 5. Build concise pre-filled WhatsApp message
    display_ref = f"WZ-{hex_prefix[:8].upper()}"
    notice_text = (
        f"🏛️ *WAASZ VERIFIED MERCHANT REGISTRY*\n"
        f"IOU Ref: #{display_ref}\n\n"
        f"Dear {cust_name},\n"
        f"This confirms your credit purchase of {item_desc} ({amount_fmt}) from {biz_name}.\n\n"
        f"📅 *Due Date:* {due_date_str}\n"
        f"💳 *Settlement Details:* {bank_info}\n\n"
        f"Please quote Ref #{display_ref} as transfer remark upon payment."
    )

    if norm_phone:
        wa_link = f"https://wa.me/{norm_phone}?text={urllib.parse.quote(notice_text)}"
    else:
        wa_link = f"https://wa.me/?text={urllib.parse.quote(notice_text)}"

    # 6. Direct 302 Redirect if requested
    is_direct = direct or request.query_params.get("redirect") in ("1", "true")
    if is_direct:
        return RedirectResponse(url=wa_link, status_code=302)

    # 7. Render branded Web Card with auto-forward script
    html_content = _render_iou_card(
        ref_code=display_ref,
        cust_name=cust_name,
        cust_phone=cust_phone or "On File",
        biz_name=biz_name,
        item_desc=item_desc,
        amount_fmt=amount_fmt,
        due_date_str=due_date_str,
        bank_info=bank_info,
        wa_link=wa_link,
    )
    return HTMLResponse(content=html_content, status_code=200)


def _render_iou_card(
    ref_code: str,
    cust_name: str,
    cust_phone: str,
    biz_name: str,
    item_desc: str,
    amount_fmt: str,
    due_date_str: str,
    bank_info: str,
    wa_link: str,
) -> str:
    """Renders a responsive, modern mobile card for the Digital IOU."""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
  <title>Digital IOU #{ref_code} | Waasz Verified Registry</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&display=swap" rel="stylesheet">
  <style>
    * {{
      box-sizing: border-box;
      margin: 0;
      padding: 0;
      font-family: 'Plus Jakarta Sans', -apple-system, BlinkMacSystemFont, sans-serif;
    }}
    body {{
      background: #0b1120;
      color: #f1f5f9;
      min-height: 100vh;
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      padding: 16px;
    }}
    .container {{
      width: 100%;
      max-width: 480px;
      background: #131c31;
      border: 1px solid rgba(255, 255, 255, 0.08);
      border-radius: 20px;
      box-shadow: 0 20px 40px rgba(0, 0, 0, 0.4);
      overflow: hidden;
    }}
    .header {{
      background: linear-gradient(135deg, #064e3b 0%, #065f46 100%);
      padding: 24px 20px;
      text-align: center;
      border-bottom: 1px solid rgba(16, 185, 129, 0.2);
    }}
    .badge {{
      display: inline-flex;
      align-items: center;
      gap: 6px;
      background: rgba(16, 185, 129, 0.2);
      border: 1px solid rgba(16, 185, 129, 0.4);
      color: #34d399;
      padding: 4px 12px;
      border-radius: 9999px;
      font-size: 11px;
      font-weight: 700;
      letter-spacing: 0.05em;
      text-transform: uppercase;
      margin-bottom: 12px;
    }}
    .ref-title {{
      font-size: 14px;
      color: #94a3b8;
      font-weight: 500;
    }}
    .amount {{
      font-size: 32px;
      font-weight: 800;
      color: #ffffff;
      margin: 8px 0;
      letter-spacing: -0.02em;
    }}
    .card-body {{
      padding: 24px 20px;
    }}
    .info-group {{
      display: flex;
      flex-direction: column;
      gap: 14px;
      margin-bottom: 24px;
    }}
    .info-row {{
      display: flex;
      justify-content: space-between;
      align-items: flex-start;
      font-size: 13.5px;
      border-bottom: 1px dashed rgba(255, 255, 255, 0.06);
      padding-bottom: 10px;
    }}
    .info-label {{
      color: #94a3b8;
      font-weight: 500;
    }}
    .info-value {{
      color: #f8fafc;
      font-weight: 600;
      text-align: right;
      max-width: 60%;
    }}
    .bank-box {{
      background: rgba(15, 23, 42, 0.6);
      border: 1px solid rgba(255, 255, 255, 0.05);
      border-radius: 12px;
      padding: 14px;
      margin-bottom: 24px;
    }}
    .bank-box-title {{
      font-size: 11px;
      font-weight: 700;
      color: #38bdf8;
      text-transform: uppercase;
      letter-spacing: 0.05em;
      margin-bottom: 6px;
    }}
    .bank-box-content {{
      font-size: 13px;
      color: #e2e8f0;
      line-height: 1.4;
      font-weight: 500;
    }}
    .btn-whatsapp {{
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 10px;
      width: 100%;
      background: #25d366;
      color: #0b1120;
      text-decoration: none;
      padding: 16px 20px;
      border-radius: 14px;
      font-size: 15px;
      font-weight: 700;
      box-shadow: 0 10px 20px rgba(37, 211, 102, 0.25);
      transition: all 0.2s ease;
      text-align: center;
    }}
    .btn-whatsapp:active {{
      transform: scale(0.98);
      background: #20ba5a;
    }}
    .footer-note {{
      text-align: center;
      font-size: 11px;
      color: #64748b;
      margin-top: 20px;
      line-height: 1.5;
    }}
  </style>
</head>
<body>
  <div class="container">
    <div class="header">
      <div class="badge">🏛️ Waasz Verified Registry</div>
      <div class="ref-title">Commercial Debt Obligation</div>
      <div class="amount">{amount_fmt}</div>
      <div style="font-size: 12px; color: #6ee7b7; font-weight: 600;">Ref #{ref_code}</div>
    </div>
    <div class="card-body">
      <div class="info-group">
        <div class="info-row">
          <span class="info-label">Customer / Debtor</span>
          <span class="info-value">{cust_name} ({cust_phone})</span>
        </div>
        <div class="info-row">
          <span class="info-label">Creditor Business</span>
          <span class="info-value">{biz_name}</span>
        </div>
        <div class="info-row">
          <span class="info-label">Item / Service</span>
          <span class="info-value">{item_desc}</span>
        </div>
        <div class="info-row">
          <span class="info-label">Payment Due Date</span>
          <span class="info-value" style="color: #fbbf24;">{due_date_str}</span>
        </div>
      </div>

      <div class="bank-box">
        <div class="bank-box-title">💳 Official Settlement Account</div>
        <div class="bank-box-content">{bank_info}</div>
      </div>

      <a id="waBtn" href="{wa_link}" class="btn-whatsapp">
        <span>📲 Open in WhatsApp & Deliver IOU</span>
      </a>

      <div class="footer-note">
        Protected & timestamped by Waasz Enterprise Ledger.<br>
        Direct transfer to merchant account clears this entry.
      </div>
    </div>
  </div>

  <script>
    // Automatically trigger WhatsApp forward on mobile devices after short delay
    setTimeout(function() {{
      const isMobile = /Android|iPhone|iPad|iPod/i.test(navigator.userAgent);
      if (isMobile) {{
        window.location.href = "{wa_link}";
      }}
    }}, 600);
  </script>
</body>
</html>"""


def _render_not_found_page(ref_code: str) -> str:
    """Renders a friendly 404 page for missing or expired IOUs."""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>IOU Not Found | Waasz</title>
  <style>
    body {{
      background: #0b1120;
      color: #f1f5f9;
      font-family: sans-serif;
      display: flex;
      align-items: center;
      justify-content: center;
      min-height: 100vh;
      margin: 0;
      padding: 16px;
    }}
    .box {{
      background: #1e293b;
      padding: 32px 24px;
      border-radius: 16px;
      text-align: center;
      max-width: 400px;
    }}
    h2 {{ color: #f87171; margin-bottom: 12px; }}
    p {{ color: #94a3b8; font-size: 14px; line-height: 1.5; }}
  </style>
</head>
<body>
  <div class="box">
    <h2>Record Not Found</h2>
    <p>Digital IOU reference <strong>#{ref_code}</strong> could not be located or has been cleared.</p>
  </div>
</body>
</html>"""
