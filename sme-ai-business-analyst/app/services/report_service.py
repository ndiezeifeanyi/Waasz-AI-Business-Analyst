from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from uuid import UUID

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.inventory import InventoryMovement
from app.models.report import BusinessReport
from app.models.transaction import Transaction
from app.utils.currency import format_naira


class ReportService:
    async def generate_daily_report(
        self, db: AsyncSession, business_id: UUID, report_date: date | None = None
    ) -> BusinessReport:
        report_date = report_date or datetime.now(UTC).date()
        period_start = report_date
        period_end = report_date
        body, totals = await self._build_report_body(
            db, business_id, period_start, period_end, "daily"
        )
        report = await self._upsert_report(
            db, business_id, "daily", period_start, period_end, body, totals
        )
        await db.flush()
        return report

    async def generate_weekly_report(
        self, db: AsyncSession, business_id: UUID, week_ending: date | None = None
    ) -> BusinessReport:
        week_ending = week_ending or datetime.now(UTC).date()
        period_start = week_ending - timedelta(days=6)
        body, totals = await self._build_report_body(
            db, business_id, period_start, week_ending, "weekly"
        )
        report = await self._upsert_report(
            db, business_id, "weekly", period_start, week_ending, body, totals
        )
        await db.flush()
        return report

    async def _upsert_report(
        self,
        db: AsyncSession,
        business_id: UUID,
        report_type: str,
        period_start: date,
        period_end: date,
        body: str,
        totals: dict[str, Decimal | int],
    ) -> BusinessReport:
        result = await db.execute(
            select(BusinessReport)
            .where(
                BusinessReport.business_id == business_id,
                BusinessReport.report_type == report_type,
                BusinessReport.period_start == period_start,
                BusinessReport.period_end == period_end,
            )
            .limit(1)
        )
        report = result.scalar_one_or_none()
        if not report:
            report = BusinessReport(
                business_id=business_id,
                report_type=report_type,
                period_start=period_start,
                period_end=period_end,
            )
            db.add(report)
        report.total_sales = totals["sales"]
        report.total_expenses = totals["expenses"]
        report.estimated_profit = totals["profit"]
        report.records_count = totals["records_count"]
        report.inventory_changes_count = totals["inventory_changes_count"]
        report.body = body
        report.status = "generated"
        return report

    async def _build_report_body(
        self,
        db: AsyncSession,
        business_id: UUID,
        period_start: date,
        period_end: date,
        report_type: str,
    ) -> tuple[str, dict[str, Decimal | int]]:
        start_dt = datetime.combine(period_start, time.min, tzinfo=UTC)
        end_dt = datetime.combine(period_end + timedelta(days=1), time.min, tzinfo=UTC)
        totals = await self._transaction_totals(db, business_id, start_dt, end_dt)
        inventory_changes_count = await self._inventory_changes_count(
            db, business_id, start_dt, end_dt
        )
        totals["inventory_changes_count"] = inventory_changes_count

        title = "Today Summary" if report_type == "daily" else "This Week Summary"
        body = (
            f"{title}\n"
            f"Sales: {format_naira(totals['sales'])}\n"
            f"Expenses: {format_naira(totals['expenses'])}\n"
            f"Estimated profit: {format_naira(totals['profit'])}\n"
            f"Confirmed records: {totals['records_count']}\n"
            f"Stock updates: {inventory_changes_count}"
        )
        return body, totals

    async def _transaction_totals(
        self, db: AsyncSession, business_id: UUID, start_dt: datetime, end_dt: datetime
    ) -> dict[str, Decimal | int]:
        statement: Select = select(
            func.coalesce(
                func.sum(Transaction.amount).filter(Transaction.transaction_type == "sale"),
                0,
            ),
            func.coalesce(
                func.sum(Transaction.amount).filter(Transaction.transaction_type == "expense"),
                0,
            ),
            func.count(Transaction.id),
        ).where(
            Transaction.business_id == business_id,
            Transaction.status == "confirmed",
            Transaction.occurred_at >= start_dt,
            Transaction.occurred_at < end_dt,
        )
        row = (await db.execute(statement)).one()
        sales = Decimal(row[0] or 0)
        expenses = Decimal(row[1] or 0)
        return {
            "sales": sales,
            "expenses": expenses,
            "profit": sales - expenses,
            "records_count": int(row[2] or 0),
        }

    async def _inventory_changes_count(
        self, db: AsyncSession, business_id: UUID, start_dt: datetime, end_dt: datetime
    ) -> int:
        statement = select(func.count(InventoryMovement.id)).where(
            InventoryMovement.business_id == business_id,
            InventoryMovement.status == "confirmed",
            InventoryMovement.occurred_at >= start_dt,
            InventoryMovement.occurred_at < end_dt,
        )
        return int((await db.execute(statement)).scalar_one() or 0)
