from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from pathlib import Path

from app.core.database import get_db_session
from app.models.business import Business
from app.services.magic_link_service import MagicLinkService
from app.services.visual_reports import VisualReportService
from app.services.ledger_service import LedgerService

router = APIRouter(prefix="/reports", tags=["reports"])

# Set up templates
BASE_DIR = Path(__file__).resolve().parents[3]
templates = Jinja2Templates(directory=str(BASE_DIR / "app" / "templates"))

@router.get("/dashboard/{token}", response_class=HTMLResponse)
async def view_dashboard(
    request: Request,
    token: str,
    db: AsyncSession = Depends(get_db_session),
    magic_links: MagicLinkService = Depends(),
    ledger: LedgerService = Depends(),
):
    business_id = await magic_links.validate_token(db, token)
    if not business_id:
        return templates.TemplateResponse(
            request=request,
            name="dashboard_expired.html",
            context={},
            status_code=403,
        )

    # Fetch business info for branding
    business = await db.get(Business, business_id)
    business_name = business.name if business else "Business"

    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "business_id": str(business_id),
            "business_name": business_name,
            "token": token,
        },
    )

@router.get("/data/{token}")
async def get_dashboard_data(
    token: str,
    days: int = Query(default=30, ge=1, le=365),
    db: AsyncSession = Depends(get_db_session),
    magic_links: MagicLinkService = Depends(),
    visual_reports: VisualReportService = Depends(),
):
    business_id = await magic_links.validate_token(db, token)
    if not business_id:
        raise HTTPException(status_code=403, detail="Invalid or expired link")

    business = await db.get(Business, business_id)
    business_name = business.name if business else "Business"

    raw_data = await visual_reports._get_timeseries_data(db, business_id, days=days)

    total_sales = sum((d["sales"] for d in raw_data), Decimal("0"))
    total_expenses = sum((d["expenses"] for d in raw_data), Decimal("0"))
    net_profit = total_sales - total_expenses
    margin_pct = round(float((net_profit / total_sales) * 100), 1) if total_sales > Decimal("0") else 0.0

    return {
        "business_name": business_name,
        "currency": "NGN",
        "days": days,
        "summary": {
            "total_sales": float(total_sales),
            "total_expenses": float(total_expenses),
            "net_profit": float(net_profit),
            "margin_pct": margin_pct,
        },
        "data": [
            {
                "date": d["date"].strftime("%Y-%m-%d"),
                "sales": float(d["sales"]),
                "expenses": float(d["expenses"]),
                "profit": float(d["sales"] - d["expenses"]),
            }
            for d in raw_data
        ],
    }
