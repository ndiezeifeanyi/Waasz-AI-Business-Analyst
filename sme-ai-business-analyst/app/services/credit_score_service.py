from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.debt import Debt
from app.models.risk_assessment import TransactionRiskAssessment
from app.models.transaction import Transaction


class CreditScoreService:
    """
    Calculates the live Waasz Business Credit Score (scale 300 - 850)
    for Nigerian retail MSMEs based on verified operational cash flows:
    1. Consistency Streak (30%): Number of active transaction days in last 30 days.
    2. Debt Settlement Ratio (25%): Percentage of credit collected on time.
    3. Verified Cash Flow & Margin (25%): Operating margin on clean/verified sales (quarantined entries excluded).
    4. Auditability & Expense Discipline (20%): Clean verification ratio and physical restock logging.
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
        consistency_pts = consistency_ratio * 165.0  # 30% of 550 scale

        # 2. Debt Settlement & Receivables Recovery Ratio
        debts_stmt = select(Debt).where(Debt.business_id == business_id)
        all_debts = (await db.execute(debts_stmt)).scalars().all()
        if all_debts:
            paid_debt_vol = sum((d.amount for d in all_debts if d.status == "paid"), Decimal("0"))
            overdue_debt_vol = sum(
                (d.amount for d in all_debts if d.status != "paid" and d.due_date and d.due_date < now),
                Decimal("0"),
            )
            matured_debt_vol = paid_debt_vol + overdue_debt_vol

            if matured_debt_vol > 0:
                debt_ratio = float(paid_debt_vol / matured_debt_vol)
            else:
                debt_ratio = 1.0
        else:
            debt_ratio = 1.0
        debt_pts = debt_ratio * 137.5  # 25% of 550 scale

        # 3. Verified Cash Flow & Margin Health
        # Quarantined transactions (unverified orphan sales, pending step-ups) are excluded from underwriting revenue
        quarantine_subq = (
            select(TransactionRiskAssessment.transaction_id)
            .where(
                TransactionRiskAssessment.business_id == business_id,
                TransactionRiskAssessment.is_credit_eligible.is_(False),
            )
            .scalar_subquery()
        )

        verified_sales_stmt = select(func.coalesce(func.sum(Transaction.amount), 0)).where(
            Transaction.business_id == business_id,
            Transaction.occurred_at >= thirty_days_ago,
            Transaction.transaction_type == "sale",
            Transaction.status == "confirmed",
            Transaction.id.not_in(quarantine_subq),
        )
        verified_sales_vol = float((await db.execute(verified_sales_stmt)).scalar() or 0)

        # Raw total sales for comparison
        total_sales_stmt = select(func.coalesce(func.sum(Transaction.amount), 0)).where(
            Transaction.business_id == business_id,
            Transaction.occurred_at >= thirty_days_ago,
            Transaction.transaction_type == "sale",
            Transaction.status == "confirmed",
        )
        total_sales_vol = float((await db.execute(total_sales_stmt)).scalar() or 0)

        exp_stmt = select(func.coalesce(func.sum(Transaction.amount), 0)).where(
            Transaction.business_id == business_id,
            Transaction.occurred_at >= thirty_days_ago,
            Transaction.transaction_type == "expense",
            Transaction.status == "confirmed",
        )
        exp_vol = float((await db.execute(exp_stmt)).scalar() or 0)

        if verified_sales_vol > 0:
            net_profit = verified_sales_vol - exp_vol
            margin = net_profit / verified_sales_vol
            margin_ratio = min(1.0, max(0.0, margin / 0.25))
        else:
            margin_ratio = 0.3 if total_sales_vol > 0 else 0.5
        margin_pts = margin_ratio * 137.5  # 25% of 550 scale

        # 4. Auditability Index & Expense Capture Discipline
        total_sales_count_stmt = select(func.count(Transaction.id)).where(
            Transaction.business_id == business_id,
            Transaction.occurred_at >= thirty_days_ago,
            Transaction.transaction_type == "sale",
            Transaction.status == "confirmed",
        )
        total_sales_count = (await db.execute(total_sales_count_stmt)).scalar() or 0

        quarantined_count_stmt = select(func.count(TransactionRiskAssessment.id)).where(
            TransactionRiskAssessment.business_id == business_id,
            TransactionRiskAssessment.is_credit_eligible.is_(False),
            TransactionRiskAssessment.evaluated_at >= thirty_days_ago,
        )
        quarantined_count = (await db.execute(quarantined_count_stmt)).scalar() or 0

        if total_sales_count > 0:
            clean_count = max(0, total_sales_count - quarantined_count)
            auditability_ratio = clean_count / float(total_sales_count)
        else:
            auditability_ratio = 1.0

        has_expenses = exp_vol > 0
        expense_component = 0.5 if has_expenses else 0.15
        audit_component = 0.5 * auditability_ratio
        discipline_ratio = expense_component + audit_component
        discipline_pts = discipline_ratio * 110.0  # 20% of 550 scale

        # Total Score: Base 300 + up to 550 earned points = 300 to 850
        raw_score = 300 + int(consistency_pts + debt_pts + margin_pts + discipline_pts)
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
            "sales_30d": total_sales_vol,
            "verified_sales_30d": verified_sales_vol,
            "expenses_30d": exp_vol,
            "auditability_ratio": round(auditability_ratio * 100, 1),
            "quarantined_transactions_count": quarantined_count,
            "debt_settlement_ratio": round(debt_ratio * 100, 1),
            "unmatured_receivables": float(sum((d.amount for d in all_debts if d.status != "paid" and (not d.due_date or d.due_date >= now)), Decimal("0"))),
            "base_points": 300,
            "consistency_pts": round(consistency_pts),
            "debt_pts": round(debt_pts),
            "margin_pts": round(margin_pts),
            "expense_pts": round(discipline_pts),
            "consistency_ratio": round(consistency_ratio * 100, 1),
            "debt_ratio": round(debt_ratio * 100, 1),
            "margin_ratio": round(margin_ratio * 100, 1),
            "expense_ratio": round(discipline_ratio * 100, 1),
        }
