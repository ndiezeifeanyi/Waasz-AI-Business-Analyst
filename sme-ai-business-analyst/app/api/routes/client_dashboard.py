from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from pathlib import Path

from app.core.database import get_db_session
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
        raise HTTPException(status_code=403, detail="Invalid or expired link")
    
    # Fetch business info
    # ...
    return templates.TemplateResponse(
        "dashboard.html",
        {"request": request, "business_id": business_id, "token": token}
    )

@router.get("/data/{token}")
async def get_dashboard_data(
    token: str,
    db: AsyncSession = Depends(get_db_session),
    magic_links: MagicLinkService = Depends(),
    visual_reports: VisualReportService = Depends(),
):
    business_id = await magic_links.validate_token(db, token)
    if not business_id:
        raise HTTPException(status_code=403, detail="Invalid or expired link")
    
    data = await visual_reports._get_timeseries_data(db, business_id, days=7)
    return {"data": data}
