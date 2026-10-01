from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.inventory import InventoryItem
from app.models.payable import Payable
from app.models.transaction import Transaction
from app.utils.currency import format_naira

AI_ADVISORY_DISCLAIMER = "📌 This is an AI-generated estimate to guide your decision, not financial or legal advice."


class AnalyticsService:
    """
    Business intelligence service delivering deterministic financial computation:
    - Gross margin auditing per-item and overall using unit_cost.
    - Product performance & 80/20 Pareto analysis + dead-stock detection.
    - Pricing simulation (discount impact & target margin calculation).
    """

    def _resolve_period_bounds(self, period: str | None) -> tuple[datetime, datetime, str]:
        """Convert conversational period strings into UTC datetime bounds."""
        now = datetime.now(UTC)
        p = (period or "this_month").strip().lower()

        if p in ("today", "daily"):
            start_date = now.date()
            end_date = now.date()
            label = f"Today ({start_date.isoformat()})"
        elif p in ("yesterday",):
            start_date = now.date() - timedelta(days=1)
            end_date = start_date
            label = f"Yesterday ({start_date.isoformat()})"
        elif p in ("this_week", "weekly", "week"):
            start_date = now.date() - timedelta(days=6)
            end_date = now.date()
            label = f"This Week ({start_date.isoformat()} to {end_date.isoformat()})"
        elif p in ("last_30_days", "30_days", "month_to_date"):
            start_date = now.date() - timedelta(days=30)
            end_date = now.date()
            label = f"Last 30 Days ({start_date.isoformat()} to {end_date.isoformat()})"
        elif p in ("this_month", "monthly", "month"):
            start_date = date(now.year, now.month, 1)
            end_date = now.date()
            label = f"This Month ({now.strftime('%B %Y')})"
        elif p in ("all", "all_time"):
            start_date = date(2020, 1, 1)
            end_date = now.date()
            label = "All Time"
        else:
            # Default to this month
            start_date = date(now.year, now.month, 1)
            end_date = now.date()
            label = f"This Month ({now.strftime('%B %Y')})"

        start_dt = datetime.combine(start_date, time.min, tzinfo=UTC)
        end_dt = datetime.combine(end_date + timedelta(days=1), time.min, tzinfo=UTC)
        return start_dt, end_dt, label

    async def get_margin_report(
        self,
        db: AsyncSession,
        business_id: UUID,
        period: str = "this_month",
        item_name: str | None = None,
    ) -> dict[str, Any]:
        """
        Compute per-item and overall gross margin using item unit_cost from inventory_items.
        Can be filtered to a single item on-demand (e.g. 'what is my margin on rice?').
        """
        start_dt, end_dt, period_label = self._resolve_period_bounds(period)

        # 1. Fetch confirmed sales in period
        stmt = (
            select(Transaction)
            .where(
                Transaction.business_id == business_id,
                Transaction.transaction_type == "sale",
                Transaction.status == "confirmed",
                Transaction.occurred_at >= start_dt,
                Transaction.occurred_at < end_dt,
            )
            .order_by(Transaction.occurred_at.asc())
        )
        sales = (await db.execute(stmt)).scalars().all()

        # 2. Fetch inventory items for this business to retrieve unit_cost
        inv_stmt = select(InventoryItem).where(InventoryItem.business_id == business_id)
        inventory_items = (await db.execute(inv_stmt)).scalars().all()
        cost_map: dict[str, tuple[Decimal | None, str | None]] = {
            item.normalized_item_name: (item.unit_cost, item.unit)
            for item in inventory_items
        }

        # 3. Aggregate sales by normalized item name
        item_sales_map: dict[str, dict[str, Any]] = {}
        for sale in sales:
            raw_name = sale.item_name or "Uncategorized Item"
            norm_name = raw_name.strip().lower()

            if norm_name not in item_sales_map:
                unit_cost, unit = cost_map.get(norm_name, (None, None))
                item_sales_map[norm_name] = {
                    "display_name": raw_name.title(),
                    "normalized_name": norm_name,
                    "revenue": Decimal("0"),
                    "quantity_sold": Decimal("0"),
                    "unit": unit or sale.unit,
                    "unit_cost": unit_cost,
                    "sales_count": 0,
                }

            item_sales_map[norm_name]["revenue"] += sale.amount
            if sale.quantity is not None:
                item_sales_map[norm_name]["quantity_sold"] += sale.quantity
            item_sales_map[norm_name]["sales_count"] += 1

        # 4. Compute margins per item
        items_data = []
        total_revenue = Decimal("0")
        total_cogs = Decimal("0")
        has_at_least_one_cogs = False

        for norm_name, data in item_sales_map.items():
            revenue = data["revenue"]
            quantity = data["quantity_sold"]
            unit_cost = data["unit_cost"]

            total_revenue += revenue

            cogs: Decimal | None = None
            gross_profit: Decimal | None = None
            margin_pct: Decimal | None = None

            if unit_cost is not None and quantity > Decimal("0"):
                cogs = (unit_cost * quantity).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                gross_profit = (revenue - cogs).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                if revenue > Decimal("0"):
                    margin_pct = ((gross_profit / revenue) * Decimal("100")).quantize(
                        Decimal("0.1"), rounding=ROUND_HALF_UP
                    )
                else:
                    margin_pct = Decimal("0.0")
                total_cogs += cogs
                has_at_least_one_cogs = True

            data["cogs"] = cogs
            data["gross_profit"] = gross_profit
            data["margin_pct"] = margin_pct
            items_data.append(data)

        # Sort items by revenue descending
        items_data.sort(key=lambda x: x["revenue"], reverse=True)

        overall_gross_profit = total_revenue - total_cogs if has_at_least_one_cogs else None
        overall_margin_pct = (
            ((overall_gross_profit / total_revenue) * Decimal("100")).quantize(
                Decimal("0.1"), rounding=ROUND_HALF_UP
            )
            if (overall_gross_profit is not None and total_revenue > Decimal("0"))
            else None
        )

        # 5. Filter for single item if requested
        if item_name:
            query_norm = item_name.strip().lower()
            matching_items = [
                it for it in items_data
                if query_norm in it["normalized_name"] or it["normalized_name"] in query_norm
            ]

            if not matching_items:
                # Check if item exists in inventory with unit cost even if not sold in this period
                direct_inv = next(
                    (it for it in inventory_items if query_norm in it.normalized_item_name),
                    None
                )
                if direct_inv:
                    cost_info = (
                        f"Unit cost is configured as {format_naira(direct_inv.unit_cost)}."
                        if direct_inv.unit_cost is not None
                        else "Unit cost is not set yet. Set it using 'set cost of <item>'."
                    )
                    single_summary = (
                        f"🔍 Margin Report: {direct_inv.item_name}\n"
                        f"Period: {period_label}\n"
                        f"No sales recorded for '{direct_inv.item_name}' during this period.\n"
                        f"{cost_info}"
                    )
                else:
                    single_summary = (
                        f"🔍 Margin Report: '{item_name}'\n"
                        f"Period: {period_label}\n"
                        f"No sales or inventory record found matching '{item_name}'."
                    )
                return {
                    "period": period_label,
                    "items": [],
                    "summary_text": single_summary,
                }

            target = matching_items[0]
            lines = [f"📊 Margin Analysis: {target['display_name']} ({period_label})"]
            lines.append(f"• Revenue: {format_naira(target['revenue'])}")
            if target['quantity_sold'] > Decimal("0"):
                u_str = f" {target['unit']}" if target['unit'] else ""
                lines.append(f"• Volume Sold: {target['quantity_sold']}{u_str}")

            if target["unit_cost"] is not None:
                lines.append(f"• Cost Price (COGS): {format_naira(target['unit_cost'])} per unit")
                if target["cogs"] is not None:
                    lines.append(f"• Total COGS: {format_naira(target['cogs'])}")
                    lines.append(f"• Gross Profit: {format_naira(target['gross_profit'])}")
                    lines.append(f"• Gross Margin: {target['margin_pct']}%")
            else:
                lines.append("• Cost Price: Not set (Reply 'set cost of [item] to [price]' to track margins)")

            lines.append(f"\n{AI_ADVISORY_DISCLAIMER}")
            return {
                "period": period_label,
                "item": target,
                "items": [target],
                "summary_text": "\n".join(lines),
            }

        # 6. Overall Multi-Item Margin Summary
        lines = [f"📊 Gross Margin Audit ({period_label})"]
        lines.append(f"Total Revenue: {format_naira(total_revenue)}")
        if has_at_least_one_cogs and overall_gross_profit is not None:
            lines.append(f"Total COGS: {format_naira(total_cogs)}")
            lines.append(f"Gross Profit: {format_naira(overall_gross_profit)}")
            lines.append(f"Overall Gross Margin: {overall_margin_pct}%")
        else:
            lines.append("COGS: Not fully configured. Set item costs to view overall profit margins.")

        if items_data:
            lines.append("\nBreakdown by Product:")
            for it in items_data[:8]:
                margin_str = f" | Margin: {it['margin_pct']}%" if it['margin_pct'] is not None else " | Cost not set"
                lines.append(f"• {it['display_name']}: {format_naira(it['revenue'])}{margin_str}")
            if len(items_data) > 8:
                lines.append(f"• ... and {len(items_data) - 8} more products")

        lines.append(f"\n{AI_ADVISORY_DISCLAIMER}")
        return {
            "period": period_label,
            "total_revenue": total_revenue,
            "total_cogs": total_cogs if has_at_least_one_cogs else None,
            "gross_profit": overall_gross_profit,
            "overall_margin_pct": overall_margin_pct,
            "items": items_data,
            "summary_text": "\n".join(lines),
        }

    async def get_product_performance_analysis(
        self,
        db: AsyncSession,
        business_id: UUID,
        period: str = "this_month",
    ) -> dict[str, Any]:
        """
        Rank items by revenue, calculate cumulative revenue percentages to identify
        the Pareto 80/20 cutoff, and flag dead stock (zero-velocity inventory tying up capital).
        """
        start_dt, end_dt, period_label = self._resolve_period_bounds(period)

        # 1. Fetch confirmed sales in period
        stmt = (
            select(Transaction)
            .where(
                Transaction.business_id == business_id,
                Transaction.transaction_type == "sale",
                Transaction.status == "confirmed",
                Transaction.occurred_at >= start_dt,
                Transaction.occurred_at < end_dt,
            )
        )
        sales = (await db.execute(stmt)).scalars().all()

        # 2. Fetch inventory items to determine dead stock and COGS
        inv_stmt = select(InventoryItem).where(InventoryItem.business_id == business_id)
        all_inventory = (await db.execute(inv_stmt)).scalars().all()
        inv_dict = {item.normalized_item_name: item for item in all_inventory}

        # 3. Aggregate product sales
        revenue_map: dict[str, dict[str, Any]] = {}
        total_revenue = Decimal("0")

        for sale in sales:
            raw_name = sale.item_name or "Uncategorized Item"
            norm_name = raw_name.strip().lower()

            if norm_name not in revenue_map:
                inv_item = inv_dict.get(norm_name)
                revenue_map[norm_name] = {
                    "display_name": raw_name.title(),
                    "normalized_name": norm_name,
                    "revenue": Decimal("0"),
                    "quantity_sold": Decimal("0"),
                    "unit": inv_item.unit if inv_item else sale.unit,
                    "unit_cost": inv_item.unit_cost if inv_item else None,
                }

            revenue_map[norm_name]["revenue"] += sale.amount
            if sale.quantity is not None:
                revenue_map[norm_name]["quantity_sold"] += sale.quantity
            total_revenue += sale.amount

        # 4. Sort products descending by revenue
        ranked_items = sorted(revenue_map.values(), key=lambda x: x["revenue"], reverse=True)

        # 5. Compute Cumulative % and 80/20 Cutoff
        running_revenue = Decimal("0")
        pareto_drivers = []
        long_tail_items = []

        for item in ranked_items:
            running_revenue += item["revenue"]
            share_pct = (
                ((item["revenue"] / total_revenue) * Decimal("100")).quantize(
                    Decimal("0.1"), rounding=ROUND_HALF_UP
                )
                if total_revenue > Decimal("0")
                else Decimal("0.0")
            )
            cumulative_pct = (
                ((running_revenue / total_revenue) * Decimal("100")).quantize(
                    Decimal("0.1"), rounding=ROUND_HALF_UP
                )
                if total_revenue > Decimal("0")
                else Decimal("0.0")
            )

            item["share_pct"] = share_pct
            item["cumulative_pct"] = cumulative_pct

            # An item is classified as a top driver if prior cumulative percentage was under 80%
            if (running_revenue - item["revenue"]) / total_revenue < Decimal("0.80") if total_revenue > Decimal("0") else False:
                item["is_top_80"] = True
                pareto_drivers.append(item)
            else:
                item["is_top_80"] = False
                long_tail_items.append(item)

        # 6. Dead Stock Identification
        # Any tracked item in inventory with quantity_on_hand > 0 that had zero sales in this period
        dead_stock_items = []
        total_tied_up_capital = Decimal("0")

        sold_norms = set(revenue_map.keys())
        for inv in all_inventory:
            if inv.quantity_on_hand > Decimal("0") and inv.normalized_item_name not in sold_norms:
                tied_capital = (
                    (inv.quantity_on_hand * inv.unit_cost).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                    if inv.unit_cost is not None
                    else Decimal("0")
                )
                total_tied_up_capital += tied_capital
                dead_stock_items.append({
                    "display_name": inv.item_name,
                    "normalized_name": inv.normalized_item_name,
                    "quantity_on_hand": inv.quantity_on_hand,
                    "unit": inv.unit,
                    "unit_cost": inv.unit_cost,
                    "tied_up_capital": tied_capital,
                })

        dead_stock_items.sort(key=lambda x: x["tied_up_capital"], reverse=True)

        # 7. Build Formatted WhatsApp Output
        lines = [f"📈 Product Performance & 80/20 Analysis ({period_label})"]
        lines.append(f"Total Sales: {format_naira(total_revenue)}")

        if pareto_drivers:
            driver_count = len(pareto_drivers)
            total_prod_count = len(ranked_items)
            pct_drivers = round((driver_count / total_prod_count) * 100) if total_prod_count > 0 else 0
            lines.append(
                f"\n🎯 80/20 Core Drivers ({driver_count} of {total_prod_count} products generate ~80% of revenue):"
            )
            for d in pareto_drivers:
                lines.append(f"• {d['display_name']}: {format_naira(d['revenue'])} ({d['share_pct']}% of sales)")

        if long_tail_items:
            lines.append(f"\n📦 Long-Tail / Secondary Products ({len(long_tail_items)} products):")
            for lt in long_tail_items[:5]:
                lines.append(f"• {lt['display_name']}: {format_naira(lt['revenue'])} ({lt['share_pct']}%)")
            if len(long_tail_items) > 5:
                lines.append(f"• ... and {len(long_tail_items) - 5} more")

        if dead_stock_items:
            lines.append(
                f"\n⚠️ Dead Stock Warning (Zero sales in period, {format_naira(total_tied_up_capital)} capital tied up):"
            )
            for ds in dead_stock_items[:4]:
                u_str = f" {ds['unit']}" if ds['unit'] else ""
                val_str = f" (~{format_naira(ds['tied_up_capital'])})" if ds['tied_up_capital'] > Decimal("0") else ""
                lines.append(f"• {ds['display_name']}: {ds['quantity_on_hand']}{u_str} unsold{val_str}")
            if len(dead_stock_items) > 4:
                lines.append(f"• ... and {len(dead_stock_items) - 4} more unsold items")
            lines.append("Tip: Consider discounting or bundling slow-moving stock to free up working cash.")

        lines.append(f"\n{AI_ADVISORY_DISCLAIMER}")
        return {
            "period": period_label,
            "total_revenue": total_revenue,
            "pareto_drivers": pareto_drivers,
            "long_tail_items": long_tail_items,
            "dead_stock_items": dead_stock_items,
            "total_tied_up_capital": total_tied_up_capital,
            "summary_text": "\n".join(lines),
        }

    async def simulate_pricing(
        self,
        db: AsyncSession,
        business_id: UUID,
        item_name: str,
        discount_pct: float | None = None,
        target_margin_pct: float | None = None,
        current_price: float | None = None,
    ) -> dict[str, Any]:
        """
        Pure deterministic pricing and margin calculation.
        Computes the resulting profit and margin under a discount, or the selling price needed
        to hit a desired profit margin. Never uses LLM estimations.
        """
        norm_name = item_name.strip().lower()

        # 1. Fetch item from inventory
        stmt = select(InventoryItem).where(
            InventoryItem.business_id == business_id,
            InventoryItem.normalized_item_name == norm_name,
        )
        item = (await db.execute(stmt)).scalar_one_or_none()

        unit_cost = item.unit_cost if item else None

        # 2. Determine baseline selling price
        price_dec: Decimal | None = None
        if current_price is not None and current_price > 0:
            price_dec = Decimal(str(current_price)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        else:
            # Look up recent confirmed sale
            sale_stmt = (
                select(Transaction)
                .where(
                    Transaction.business_id == business_id,
                    Transaction.transaction_type == "sale",
                    Transaction.status == "confirmed",
                    func.lower(Transaction.item_name) == norm_name,
                )
                .order_by(Transaction.occurred_at.desc())
                .limit(1)
            )
            last_sale = (await db.execute(sale_stmt)).scalar_one_or_none()
            if last_sale:
                if last_sale.unit_price is not None and last_sale.unit_price > Decimal("0"):
                    price_dec = last_sale.unit_price
                elif last_sale.quantity is not None and last_sale.quantity > Decimal("0"):
                    price_dec = (last_sale.amount / last_sale.quantity).quantize(
                        Decimal("0.01"), rounding=ROUND_HALF_UP
                    )
                else:
                    price_dec = last_sale.amount

        # Validation: Unit cost is strictly necessary to compute margins
        if unit_cost is None:
            return {
                "success": False,
                "error": "unit_cost_missing",
                "summary_text": (
                    f"⚠️ Cannot simulate pricing for '{item_name}' because its cost price (COGS) is not set.\n"
                    f"Please set it first by saying e.g. 'Set cost of {item_name} to ₦30,000'."
                ),
            }

        display_name = item.item_name if item else item_name.title()

        # Scenario A: Discount Simulation
        if discount_pct is not None:
            if price_dec is None:
                return {
                    "success": False,
                    "error": "current_price_missing",
                    "summary_text": (
                        f"⚠️ To simulate a {discount_pct}% discount on '{display_name}', please provide the current selling price "
                        f"(e.g. 'simulate 10% discount on {display_name} selling at ₦50,000')."
                    ),
                }

            disc_dec = Decimal(str(discount_pct))
            discount_amount = (price_dec * (disc_dec / Decimal("100"))).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
            discounted_price = price_dec - discount_amount
            new_profit = discounted_price - unit_cost
            new_margin_pct = (
                ((new_profit / discounted_price) * Decimal("100")).quantize(
                    Decimal("0.1"), rounding=ROUND_HALF_UP
                )
                if discounted_price > Decimal("0")
                else Decimal("0.0")
            )

            orig_profit = price_dec - unit_cost
            orig_margin_pct = (
                ((orig_profit / price_dec) * Decimal("100")).quantize(
                    Decimal("0.1"), rounding=ROUND_HALF_UP
                )
                if price_dec > Decimal("0")
                else Decimal("0.0")
            )
            margin_diff = (new_margin_pct - orig_margin_pct).quantize(Decimal("0.1"))

            is_loss = new_profit < Decimal("0")
            warning = " ⚠️ Caution: This price sells at a loss below cost!" if is_loss else ""

            summary = (
                f"🏷️ Pricing Simulation: {display_name} ({discount_pct}% Discount)\n"
                f"• Current Price: {format_naira(price_dec)} (Margin: {orig_margin_pct}% | Profit: {format_naira(orig_profit)})\n"
                f"• Unit Cost (COGS): {format_naira(unit_cost)}\n"
                f"• Discount Amount: {format_naira(discount_amount)}\n"
                f"• New Selling Price: {format_naira(discounted_price)}\n"
                f"• New Profit per Unit: {format_naira(new_profit)}{warning}\n"
                f"• New Gross Margin: {new_margin_pct}% ({margin_diff}% pts change)\n\n"
                f"{AI_ADVISORY_DISCLAIMER}"
            )

            return {
                "success": True,
                "simulation_type": "discount",
                "item_name": display_name,
                "current_price": price_dec,
                "unit_cost": unit_cost,
                "discount_pct": disc_dec,
                "discount_amount": discount_amount,
                "new_price": discounted_price,
                "profit_per_unit": new_profit,
                "new_margin_pct": new_margin_pct,
                "is_loss": is_loss,
                "summary_text": summary,
            }

        # Scenario B: Target Margin Simulation
        if target_margin_pct is not None:
            tgt_dec = Decimal(str(target_margin_pct))
            if tgt_dec >= Decimal("100"):
                return {
                    "success": False,
                    "error": "invalid_target_margin",
                    "summary_text": (
                        f"⚠️ A target margin of {target_margin_pct}% is mathematically impossible "
                        f"when your cost price is {format_naira(unit_cost)}."
                    ),
                }

            # Formula: Margin = (Price - Cost) / Price => Price = Cost / (1 - Margin/100)
            multiplier = Decimal("1") - (tgt_dec / Decimal("100"))
            required_price = (unit_cost / multiplier).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            profit_per_unit = (required_price - unit_cost).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

            current_comparison = ""
            if price_dec is not None:
                orig_margin = (
                    (((price_dec - unit_cost) / price_dec) * Decimal("100")).quantize(
                        Decimal("0.1"), rounding=ROUND_HALF_UP
                    )
                    if price_dec > Decimal("0")
                    else Decimal("0.0")
                )
                price_diff = required_price - price_dec
                diff_sign = "+" if price_diff >= Decimal("0") else ""
                current_comparison = (
                    f"• Current Price: {format_naira(price_dec)} (Current Margin: {orig_margin}%)\n"
                    f"• Adjustment Needed: {diff_sign}{format_naira(price_diff)}\n"
                )

            summary = (
                f"🎯 Target Margin Simulation: {display_name} ({target_margin_pct}% Margin)\n"
                f"• Unit Cost (COGS): {format_naira(unit_cost)}\n"
                f"{current_comparison}"
                f"• Required Selling Price: {format_naira(required_price)}\n"
                f"• Profit per Unit: {format_naira(profit_per_unit)}\n"
                f"• Resulting Gross Margin: {tgt_dec}%\n\n"
                f"{AI_ADVISORY_DISCLAIMER}"
            )

            return {
                "success": True,
                "simulation_type": "target_margin",
                "item_name": display_name,
                "unit_cost": unit_cost,
                "current_price": price_dec,
                "target_margin_pct": tgt_dec,
                "required_price": required_price,
                "profit_per_unit": profit_per_unit,
                "summary_text": summary,
            }

        # Neither discount nor target margin provided
        return {
            "success": False,
            "error": "missing_parameters",
            "summary_text": (
                f"⚠️ Please specify either a discount percentage (e.g. 'simulate 10% discount on {display_name}') "
                f"or a target margin (e.g. 'what price do I need for a 25% margin on {display_name}?')."
            ),
        }

    async def log_upcoming_payable(
        self,
        db: AsyncSession,
        business_id: UUID,
        amount: Decimal | float,
        due_date: date | str,
        description: str,
        vendor_name: str | None = None,
    ) -> tuple[Payable, str]:
        """
        Record an upcoming vendor payable, supplier bill, or fixed overhead due date.
        Feeds directly into cash flow shortfall forecasting.
        """
        amt_dec = Decimal(str(amount)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        if amt_dec <= Decimal("0"):
            raise ValueError("Payable amount must be greater than zero.")

        if isinstance(due_date, str):
            clean_str = due_date.strip().split("T")[0]
            due_date_obj = date.fromisoformat(clean_str)
        else:
            due_date_obj = due_date

        payable = Payable(
            business_id=business_id,
            amount=amt_dec,
            due_date=due_date_obj,
            description=description.strip(),
            vendor_name=vendor_name.strip() if vendor_name else None,
            status="outstanding",
        )
        db.add(payable)
        await db.flush()

        vendor_line = f"• Vendor: {payable.vendor_name}\n" if payable.vendor_name else ""
        msg = (
            f"✅ Logged upcoming payable:\n"
            f"• Amount: {format_naira(amt_dec)}\n"
            f"• Due Date: {due_date_obj.strftime('%B %d, %Y')}\n"
            f"• Description: {payable.description}\n"
            f"{vendor_line}"
            f"This has been scheduled into your cash flow forecast."
        )
        return payable, msg

    async def list_upcoming_payables(
        self,
        db: AsyncSession,
        business_id: UUID,
        horizon_days: int = 30,
    ) -> list[Payable]:
        """Fetch outstanding payables due within horizon_days."""
        today = datetime.now(UTC).date()
        cutoff = today + timedelta(days=horizon_days)

        stmt = (
            select(Payable)
            .where(
                Payable.business_id == business_id,
                Payable.status == "outstanding",
                Payable.due_date <= cutoff,
            )
            .order_by(Payable.due_date.asc())
        )
        return list((await db.execute(stmt)).scalars().all())

    async def get_cash_flow_forecast(
        self,
        db: AsyncSession,
        business_id: UUID,
        trailing_days: int = 30,
        horizon_days: int = 14,
    ) -> dict[str, Any]:
        """
        Simplified v1 Cash Flow Forecast:
        Compares trailing-N-day average daily sales against upcoming payables
        due within the next M days, flagging potential cash flow shortfalls.
        """
        now = datetime.now(UTC)
        trailing_start = now - timedelta(days=trailing_days)

        # 1. Trailing sales volume
        sales_stmt = select(
            func.coalesce(func.sum(Transaction.amount), Decimal("0"))
        ).where(
            Transaction.business_id == business_id,
            Transaction.transaction_type == "sale",
            Transaction.status == "confirmed",
            Transaction.occurred_at >= trailing_start,
            Transaction.occurred_at <= now,
        )
        trailing_sales_dec = Decimal(str((await db.execute(sales_stmt)).scalar_one() or 0))

        # 2. Average daily revenue & projected inflow
        trailing_days_dec = Decimal(str(trailing_days))
        avg_daily_rev = (trailing_sales_dec / trailing_days_dec).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        horizon_days_dec = Decimal(str(horizon_days))
        projected_inflow = (avg_daily_rev * horizon_days_dec).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        # 3. Upcoming payables in horizon
        payables = await self.list_upcoming_payables(db, business_id, horizon_days=horizon_days)
        total_payables = sum((p.amount for p in payables), Decimal("0")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        # 4. Net forecast and shortfall calculation
        projected_net = projected_inflow - total_payables
        is_shortfall = total_payables > projected_inflow
        shortfall_amount = (total_payables - projected_inflow) if is_shortfall else Decimal("0")

        # 5. Format conversational summary
        lines = [
            f"📉 Cash Flow Forecast (Simplified v1 — Next {horizon_days} Days)",
            f"• Trailing {trailing_days}-Day Sales: {format_naira(trailing_sales_dec)} (~{format_naira(avg_daily_rev)}/day)",
            f"• Projected Cash Inflow: {format_naira(projected_inflow)}",
            f"• Upcoming Payables / Bills: {format_naira(total_payables)} ({len(payables)} scheduled)",
        ]

        if is_shortfall:
            lines.append(
                f"\n⚠️ Cash Flow Shortfall Alert: -{format_naira(shortfall_amount)}\n"
                f"Your upcoming obligations exceed expected sales over the next {horizon_days} days. "
                f"Consider following up on unpaid customer debts or delaying non-essential purchases.\n\n"
                f"{AI_ADVISORY_DISCLAIMER}"
            )
        else:
            lines.append(
                f"\n✅ Healthy Cash Flow: +{format_naira(projected_net)} projected surplus."
            )

        if payables:
            lines.append("\nUpcoming Obligations:")
            for p in payables[:5]:
                v_str = f" to {p.vendor_name}" if p.vendor_name else ""
                lines.append(f"• {p.due_date.strftime('%b %d')}: {format_naira(p.amount)} ({p.description}{v_str})")
            if len(payables) > 5:
                lines.append(f"• ... and {len(payables) - 5} more")

        return {
            "trailing_days": trailing_days,
            "horizon_days": horizon_days,
            "trailing_sales": trailing_sales_dec,
            "avg_daily_revenue": avg_daily_rev,
            "projected_inflow": projected_inflow,
            "total_payables": total_payables,
            "projected_net": projected_net,
            "is_shortfall": is_shortfall,
            "shortfall_amount": shortfall_amount,
            "payables_count": len(payables),
            "summary_text": "\n".join(lines),
        }

