import io
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from uuid import UUID

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
import seaborn as sns
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.transaction import Transaction
from app.utils.currency import format_naira


class VisualReportService:
    async def generate_trend_chart(
        self,
        db: AsyncSession,
        business_id: UUID,
        days: int = 7,
        title: str | None = None,
    ) -> bytes:
        """Generate a sales vs expenses trend chart for arbitrary days (weekly, monthly, quarterly, yearly)."""
        data = await self._get_timeseries_data(db, business_id, days=days)
        if not data:
            return b""

        # Filter out rows if there are zero total sales and expenses
        total_activity = sum(d["sales"] + d["expenses"] for d in data)
        if total_activity == Decimal("0"):
            # Still generate a clean chart showing baseline or return empty
            pass

        df = pd.DataFrame(data)
        if days <= 14:
            df["date_label"] = pd.to_datetime(df["date"]).dt.strftime("%a %d")
        elif days <= 35:
            df["date_label"] = pd.to_datetime(df["date"]).dt.strftime("%b %d")
        elif days <= 100:
            df["date_label"] = pd.to_datetime(df["date"]).dt.strftime("%b %d")
        else:
            df["date_label"] = pd.to_datetime(df["date"]).dt.strftime("%b '%y")

        plt.figure(figsize=(10, 5.5))
        sns.set_theme(style="whitegrid")

        # Plot Sales and Expenses
        plt.plot(df["date_label"], df["sales"], marker="o" if days <= 35 else None, label="Sales", color="#2ecc71", linewidth=2.5)
        plt.plot(
            df["date_label"], df["expenses"], marker="o" if days <= 35 else None, label="Expenses", color="#e74c3c", linewidth=2.5
        )

        chart_title = title
        if not chart_title:
            if days <= 7:
                chart_title = "Weekly Performance (Last 7 Days)"
            elif days <= 31:
                chart_title = "Monthly Performance (Last 30 Days)"
            elif days <= 95:
                chart_title = "Quarterly Performance (Last 90 Days)"
            else:
                chart_title = "Yearly Performance (Last 365 Days)"

        plt.title(chart_title, fontsize=15, pad=18, fontweight="bold")
        plt.xlabel("Period", fontsize=11)
        plt.ylabel("Amount (NGN)", fontsize=11)
        plt.legend(frameon=True)

        # Thin out x-ticks if many days
        if days > 14:
            step = max(1, len(df) // 8)
            plt.xticks(range(0, len(df), step), df["date_label"].iloc[::step], rotation=30)
        else:
            plt.xticks(rotation=0)

        plt.tight_layout()

        buf = io.BytesIO()
        plt.savefig(buf, format="png", dpi=150)
        buf.seek(0)
        plt.close()
        return buf.getvalue()

    async def generate_weekly_chart(self, db: AsyncSession, business_id: UUID) -> bytes:
        """Generate a sales vs expenses chart for the last 7 days."""
        return await self.generate_trend_chart(db, business_id, days=7, title="Weekly Business Performance")

    async def generate_pdf_report(
        self, db: AsyncSession, business_id: UUID, business_name: str
    ) -> bytes:
        """Generate a comprehensive PDF report."""
        data = await self._get_timeseries_data(db, business_id, days=7)
        
        buf = io.BytesIO()
        doc = SimpleDocTemplate(buf, pagesize=letter)
        styles = getSampleStyleSheet()
        elements = []

        # Title
        elements.append(Paragraph(f"Business Performance Report: {business_name}", styles["Title"]))
        elements.append(Spacer(1, 12))
        elements.append(Paragraph(f"Generated on: {date.today().strftime('%B %d, %Y')}", styles["Normal"]))
        elements.append(Spacer(1, 24))

        # Summary Table
        table_data = [["Date", "Sales", "Expenses", "Profit"]]
        total_sales = Decimal("0")
        total_expenses = Decimal("0")

        for d in data:
            table_data.append(
                [
                    d["date"].strftime("%Y-%m-%d"),
                    format_naira(d["sales"]),
                    format_naira(d["expenses"]),
                    format_naira(d["sales"] - d["expenses"]),
                ]
            )
            total_sales += d["sales"]
            total_expenses += d["expenses"]

        table_data.append(
            ["TOTAL", format_naira(total_sales), format_naira(total_expenses), format_naira(total_sales - total_expenses)]
        )

        t = Table(table_data)
        t.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.grey),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
                    ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("FONTSIZE", (0, 0), (-1, 0), 12),
                    ("BOTTOMPADDING", (0, 0), (-1, 0), 12),
                    ("BACKGROUND", (0, -1), (-1, -1), colors.beige),
                    ("GRID", (0, 0), (-1, -1), 1, colors.black),
                ]
            )
        )
        elements.append(t)
        
        doc.build(elements)
        buf.seek(0)
        return buf.getvalue()

    async def _get_timeseries_data(self, db: AsyncSession, business_id: UUID, days: int = 7) -> list[dict]:
        end_date = date.today()
        start_date = end_date - timedelta(days=days - 1)
        
        start_dt = datetime.combine(start_date, time.min, tzinfo=UTC)
        end_dt = datetime.combine(end_date + timedelta(days=1), time.min, tzinfo=UTC)

        statement = select(
            func.date(Transaction.occurred_at).label("day"),
            func.coalesce(func.sum(Transaction.amount).filter(Transaction.transaction_type == "sale"), 0).label("sales"),
            func.coalesce(func.sum(Transaction.amount).filter(Transaction.transaction_type == "expense"), 0).label("expenses")
        ).where(
            Transaction.business_id == business_id,
            Transaction.status == "confirmed",
            Transaction.occurred_at >= start_dt,
            Transaction.occurred_at < end_dt
        ).group_by(func.date(Transaction.occurred_at)).order_by(func.date(Transaction.occurred_at))

        result = await db.execute(statement)
        rows = result.all()
        
        # Fill in missing dates
        date_range = [start_date + timedelta(days=i) for i in range(days)]
        data_map = {row.day: {"sales": Decimal(row.sales), "expenses": Decimal(row.expenses)} for row in rows}
        
        return [
            {
                "date": d,
                "sales": data_map.get(d, {}).get("sales", Decimal("0")),
                "expenses": data_map.get(d, {}).get("expenses", Decimal("0"))
            }
            for d in date_range
        ]
