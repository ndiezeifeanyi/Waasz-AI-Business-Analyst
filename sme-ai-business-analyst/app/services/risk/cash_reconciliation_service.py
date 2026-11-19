from datetime import UTC, datetime, timedelta
from decimal import Decimal
import logging
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.cash_reconciliation import CashRestockReconciliation
from app.models.inventory import InventoryItem
from app.models.risk_assessment import TransactionRiskAssessment
from app.models.transaction import Transaction

logger = logging.getLogger(__name__)


class CashReconciliationEngine:
    """
    Enforces physical inventory boundaries on self-reported cash transactions.
    Detects fabricated numbers and calculates verifiable cash turnover.
    """

    # Industry Gross Margin Bounds for Nigerian Retail Trade
    MARGIN_BOUNDS = {
        "staples": (Decimal("0.02"), Decimal("0.18")),        # Rice, beans, sugar, oil, cement (2% - 18%)
        "general_merchandise": (Decimal("0.08"), Decimal("0.35")),  # Provisions, toiletries (8% - 35%)
        "electronics": (Decimal("0.05"), Decimal("0.25")),    # Phones, accessories (5% - 25%)
        "default": (Decimal("0.03"), Decimal("0.40")),        # Default safety ceiling (40% max margin)
    }

    async def record_restock_expense(
        self,
        db: AsyncSession,
        business_id: UUID,
        tx: Transaction,
    ) -> CashRestockReconciliation | None:
        """
        Creates an active physical restock batch when an inventory purchase expense is confirmed.
        Anchors future cash sales to this purchase outflow.
        """
        if tx.transaction_type != "expense":
            return None

        # Check description or item_name for restock signals
        item_title = (tx.item_name or tx.description or "").strip()
        if not item_title:
            return None

        norm_item = item_title.lower()
        qty = tx.quantity or Decimal("1")
        total_amt = tx.amount or Decimal("0")
        unit_cost = tx.unit_price or (total_amt / qty if qty > 0 else total_amt)

        reconciliation = CashRestockReconciliation(
            business_id=business_id,
            restock_expense_id=tx.id,
            item_name=item_title,
            normalized_item_name=norm_item,
            quantity_acquired=qty,
            quantity_sold_cash=Decimal("0"),
            unit_cost=unit_cost,
            total_cost=total_amt,
            total_cash_revenue_realized=Decimal("0"),
            status="active",
        )
        db.add(reconciliation)
        await db.flush()
        logger.info(
            "Anchored restock batch %s for %s (%s units @ %s) on business %s",
            reconciliation.id,
            item_title,
            qty,
            unit_cost,
            business_id,
        )
        return reconciliation

    async def evaluate_cash_sale(
        self,
        db: AsyncSession,
        business_id: UUID,
        tx: Transaction,
    ) -> TransactionRiskAssessment:
        """
        Evaluates a confirmed cash sale against physical restocks, margin bounds,
        and statistical velocity baselines.
        """
        flags: list[str] = []
        risk_score = Decimal("0.0")
        is_credit_eligible = True
        quarantine_reason = None
        step_up_triggered = False

        if tx.transaction_type != "sale" or tx.is_credit:
            assessment = TransactionRiskAssessment(
                transaction_id=tx.id,
                business_id=business_id,
                risk_score=0.0,
                risk_tier="tier_0_clean",
                is_credit_eligible=True,
            )
            db.add(assessment)
            await db.flush()
            return assessment

        norm_item = (tx.item_name or tx.description or "").strip().lower()

        # --- Rule 1: Check Restock Expense & Physical Inventory Depletion ---
        cutoff = datetime.now(UTC) - timedelta(days=45)
        batch_stmt = (
            select(CashRestockReconciliation)
            .where(
                CashRestockReconciliation.business_id == business_id,
                CashRestockReconciliation.normalized_item_name == norm_item,
                CashRestockReconciliation.status == "active",
                CashRestockReconciliation.created_at >= cutoff,
            )
            .order_by(CashRestockReconciliation.created_at.asc())
        )
        batch_res = await db.execute(batch_stmt)
        active_batches = batch_res.scalars().all()

        # Check inventory items as secondary anchor
        inv_stmt = select(InventoryItem).where(
            InventoryItem.business_id == business_id,
            InventoryItem.normalized_item_name == norm_item,
        )
        inv_res = await db.execute(inv_stmt)
        inv_item = inv_res.scalar_one_or_none()

        sale_qty = tx.quantity or Decimal("1")
        sale_amount = tx.amount or Decimal("0")
        unit_cost = None

        if active_batches:
            unit_cost = active_batches[0].unit_cost
            remaining_to_deduct = sale_qty
            for batch in active_batches:
                avail = batch.quantity_acquired - batch.quantity_sold_cash
                if avail > 0:
                    deduct = min(avail, remaining_to_deduct)
                    batch.quantity_sold_cash += deduct
                    portion_ratio = deduct / sale_qty if sale_qty > 0 else Decimal("1")
                    batch.total_cash_revenue_realized += sale_amount * portion_ratio
                    batch.last_sale_matched_at = datetime.now(UTC)
                    remaining_to_deduct -= deduct

                    if batch.quantity_sold_cash >= batch.quantity_acquired:
                        batch.status = "fully_depleted"
                    if remaining_to_deduct <= 0:
                        break
        elif inv_item and inv_item.quantity_on_hand >= sale_qty:
            unit_cost = inv_item.unit_cost
        else:
            # Orphan Sale: Merchant logged cash sales without corresponding restock or inventory
            flags.append("ORPHAN_CASH_SALE_NO_RESTOCK")
            risk_score += Decimal("0.35")

        # --- Rule 2: Gross Margin Bounds Check ---
        if unit_cost and tx.unit_price and tx.unit_price > 0:
            implied_margin = (tx.unit_price - unit_cost) / tx.unit_price
            min_margin, max_margin = self.MARGIN_BOUNDS.get("default")
            if implied_margin > max_margin:
                flags.append("ANOMALOUS_HIGH_MARGIN")
                risk_score += Decimal("0.30")
            elif implied_margin < Decimal("-0.10"):
                flags.append("SUSPICIOUS_LOSS_LEADER")
                risk_score += Decimal("0.15")

        # --- Rule 3: Velocity Spike (Statistical Outlier vs 30-Day Median) ---
        median_stmt = select(func.percentile_cont(0.5).within_group(Transaction.amount)).where(
            Transaction.business_id == business_id,
            Transaction.id != tx.id,
            Transaction.transaction_type == "sale",
            Transaction.status == "confirmed",
            Transaction.occurred_at >= datetime.now(UTC) - timedelta(days=30),
        )
        median_res = await db.execute(median_stmt)
        raw_median = median_res.scalar()
        median_val = Decimal(str(raw_median)) if raw_median is not None else Decimal("15000")

        # Flag large transactions that deviate by > 4x from typical daily median
        if sale_amount >= Decimal("500000") and sale_amount >= (median_val * Decimal("4.0")):
            flags.append("STATISTICAL_VELOCITY_SPIKE")
            risk_score += Decimal("0.40")
            step_up_triggered = True

        # Assign Risk Tier
        if risk_score >= Decimal("0.65") or step_up_triggered:
            risk_tier = "tier_1_step_up"
            is_credit_eligible = False
            quarantine_reason = f"Flagged by anomaly filters: {', '.join(flags)}. Awaiting step-up verification."
        elif risk_score >= Decimal("0.35"):
            risk_tier = "tier_2_shadow_quarantined"
            is_credit_eligible = False
            quarantine_reason = "Unanchored cash sale without restock proof. Excluded from credit evaluation."
        else:
            risk_tier = "tier_0_clean"

        assessment = TransactionRiskAssessment(
            transaction_id=tx.id,
            business_id=business_id,
            risk_score=float(risk_score),
            risk_tier=risk_tier,
            anomaly_flags=flags,
            step_up_triggered=step_up_triggered,
            step_up_status="pending_user_proof" if step_up_triggered else "not_required",
            is_credit_eligible=is_credit_eligible,
            quarantine_reason=quarantine_reason,
        )
        db.add(assessment)
        await db.flush()

        logger.info(
            "Transaction %s risk evaluated: score=%.3f, tier=%s, flags=%s",
            tx.id,
            risk_score,
            risk_tier,
            flags,
        )
        return assessment
