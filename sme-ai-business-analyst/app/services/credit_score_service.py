from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.debt import Debt
from app.models.transaction import Transaction


class CreditScoreService:
    """
    Calculates the live Waasz Business Credit Score (scale 300 - 850)
    for Nigerian retail MSMEs based on verified operational cash flows:
    1. Consistency Streak (35%): Number of active transaction days in last 30 days.
    2. Debt Settlement Ratio (25%): Percentage of credit collected on time.
    3. Profit Margin Health (20%): Healthy net take-home margin (>15%).
    4. Expense Discipline (20%): Regular logging of operating expenses alongside sales.
    """

    async def calculate_score(self, db: AsyncSession, business_id: UUID) -> dict:
        now = datetime.now(UTC)
        thirty_days_ago = now - timedelta(days=30)

        # 1. Consistency: Active logging days in the past 30 days
        active_days_stmt = select(
            func.count(func.distinct(func.date(Transaction.occurred_at)))
        ).where(
            Transaction.business_id == business_id,
            Transaction.occurred_at >= thirty_days_ago,
            Transaction.status == "confirmed",
        )
        active_days = (await db.execute(active_days_stmt)).scalar() or 0
        consistency_ratio = min(1.0, active_days / 20.0)  # 20 active days/month = max points
        consistency_pts = consistency_ratio * 192.5  # 35% of 550 scale

        # 2. Debt Settlement & Receivables Recovery Ratio
        # CRITICAL REASONING: An unmatured receivable (due date in future) is an active trade asset, NOT a default.
        # Only matured debts past their due date that remain unpaid should penalize the settlement score.
        debts_stmt = select(Debt).where(Debt.business_id == business_id)
        all_debts = (await db.execute(debts_stmt)).scalars().all()
        if all_debts:
            total_debt_vol = sum((d.amount for d in all_debts), Decimal("0"))
            paid_debt_vol = sum((d.amount for d in all_debts if d.status == "paid"), Decimal("0"))
            overdue_debt_vol = sum(
                (d.amount for d in all_debts if d.status != "paid" and d.due_date and d.due_date < now),
                Decimal("0"),
            )
            matured_debt_vol = paid_debt_vol + overdue_debt_vol

            if matured_debt_vol > 0:
                debt_ratio = float(paid_debt_vol / matured_debt_vol)
            else:
                # All active debts are current/unmatured, or zero overdue debt: 100% healthy
                debt_ratio = 1.0
        else:
            debt_ratio = 1.0  # Zero bad debt default
        debt_pts = debt_ratio * 137.5  # 25% of 550 scale

        # 3. Margin Health: Sales vs Expenses in last 30 days
        sales_stmt = select(func.coalesce(func.sum(Transaction.amount), 0)).where(
            Transaction.business_id == business_id,
            Transaction.occurred_at >= thirty_days_ago,
            Transaction.transaction_type == "sale",
            Transaction.status == "confirmed",
        )
        sales_vol = float((await db.execute(sales_stmt)).scalar() or 0)

        exp_stmt = select(func.coalesce(func.sum(Transaction.amount), 0)).where(
            Transaction.business_id == business_id,
            Transaction.occurred_at >= thirty_days_ago,
            Transaction.transaction_type == "expense",
            Transaction.status == "confirmed",
        )
        exp_vol = float((await db.execute(exp_stmt)).scalar() or 0)

        if sales_vol > 0:
            net_profit = sales_vol - exp_vol
            margin = net_profit / sales_vol
            margin_ratio = min(1.0, max(0.0, margin / 0.25))  # 25% margin = 100%
        else:
            margin_ratio = 0.5
        margin_pts = margin_ratio * 110.0  # 20% of 550 scale

        # 4. Expense Capture Discipline
        has_expenses = exp_vol > 0
        expense_pts = 110.0 if has_expenses else 30.0  # 20% of 550 scale

        # Total Score: Base 300 + up to 550 earned points = 300 to 850
        raw_score = 300 + int(consistency_pts + debt_pts + margin_pts + expense_pts)
        score = max(300, min(850, raw_score))

        # Determine Tier
        if score >= 680:
            tier = "Tier 1: High Liquidity & Consistent 🟢"
            badge = "Consistent trade records & healthy receivables recovery"
        elif score >= 540:
            tier = "Tier 2: Developing Activity 🟡"
            badge = "Active trade history (maintaining daily record logging)"
        else:
            tier = "Tier 3: Early / Minimal Records 🔴"
            badge = "Building record consistency across daily sales"

        days_streak = min(30, active_days)
        target_days = 30
        days_left = max(0, target_days - days_streak)

        return {
            "score": score,
            "tier": tier,
            "badge": badge,
            "active_days_last_30": active_days,
            "days_streak": days_streak,
            "days_to_unlock": days_left,
            "sales_30d": sales_vol,
            "expenses_30d": exp_vol,
            "debt_settlement_ratio": round(debt_ratio * 100, 1),
            "unmatured_receivables": float(sum((d.amount for d in all_debts if d.status != "paid" and (not d.due_date or d.due_date >= now)), Decimal("0"))),
            "base_points": 300,
            "consistency_pts": round(consistency_pts),
            "debt_pts": round(debt_pts),
            "margin_pts": round(margin_pts),
            "expense_pts": round(expense_pts),
            "consistency_ratio": round(consistency_ratio * 100, 1),
            "debt_ratio": round(debt_ratio * 100, 1),
            "margin_ratio": round(margin_ratio * 100, 1),
            "expense_ratio": 100.0 if has_expenses else 27.3,
        }
