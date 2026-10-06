#!/usr/bin/env python3
"""
End-to-End Live Verification for Phase 4: Owner Reports & Team Analytics.
Connects to the real live Supabase database and executes live verification of:
1. Team Sales Breakdown Analytics:
   - Aggregates sales by staff member with top contributor identification.
2. Staff Personal Performance Isolation:
   - Staff personal summary includes strictly their own entries and improvement tips.
   - Zero leak of business margins, other staff entries, or store expenses.
3. Unified Report Delivery & Chart Gating:
   - Staff report delivery sends personal summary only (no visual charts or magic links).
   - Staff attempting to generate visual financial chart is blocked with clear role restriction.
4. Agent Financial Analysis RBAC:
   - Staff is blocked from get_team_performance, get_margin_report, and get_cash_flow_forecast.
   - Owner executing get_team_performance receives full team breakdown.
5. Historical Summary Scoping:
   - Staff historical summary isolates to own sales and omits business net profit.
   - Owner historical summary includes business-wide totals and net profit.
6. Database Cleanup:
   - Complete, safe teardown of all test artifacts from Supabase.
"""
import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
import sys
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from sqlalchemy import select, text
from app.core.database import async_session_factory
from app.models.business import Business
from app.models.member import Member
from app.models.user import User
from app.models.transaction import Transaction
from app.schemas.actor import ActorContext
from app.schemas.whatsapp import WhatsAppSendResult
from app.services.analytics_service import AnalyticsService
from app.services.agent_service import AgentService
from app.services.unified_report_service import UnifiedReportService


class MockLiveWhatsApp:
    def __init__(self):
        self.sent_messages: list[tuple[str, str]] = []

    async def send_text(self, to_phone: str, text: str) -> WhatsAppSendResult:
        self.sent_messages.append((to_phone, text))
        return WhatsAppSendResult(message_id="wamid.mock_text_123")

    async def send_template(self, to_phone: str, template_name: str, language_code: str, components: list) -> WhatsAppSendResult:
        self.sent_messages.append((to_phone, f"TEMPLATE:{template_name}"))
        return WhatsAppSendResult(message_id="wamid.mock_tpl_123")

    async def send_image(self, to_phone: str, media_id: str, caption: str) -> WhatsAppSendResult:
        self.sent_messages.append((to_phone, f"IMAGE:{caption}"))
        return WhatsAppSendResult(message_id="wamid.mock_img_123")

    async def upload_media(self, media_bytes: bytes, mime_type: str) -> str:
        return "media_id_live_test_123"


async def main() -> None:
    print("=============================================================")
    print("  PHASE 4 LIVE E2E VERIFICATION: OWNER REPORTS & TEAM ANALYTICS")
    print("=============================================================")

    ts = int(datetime.now(UTC).timestamp())
    owner_phone = f"+234803{ts % 10000000:07d}"
    staff_phone = f"+234805{ts % 10000000:07d}"
    biz_name = f"Live Solar Hub {ts}"

    biz_id = uuid4()
    owner_user_id = uuid4()
    staff_user_id = uuid4()
    staff_member_id = uuid4()

    tx_ids: list[uuid4] = []

    async with async_session_factory() as db:
        try:
            print("\n[Setup] Creating temporary business, owner, and staff in Supabase...")
            biz = Business(
                id=biz_id,
                name=biz_name,
                owner_name="Alhaji Solar",
                phone_number=owner_phone,
                country="Nigeria",
                currency="NGN",
                timezone="Africa/Lagos",
                subscription_plan="pilot",
                onboarding_status="active",
            )
            owner_user = User(
                id=owner_user_id,
                phone_number=owner_phone,
                display_name="Alhaji Solar",
                business_id=biz_id,
                role="owner",
                niche="sme_owner",
                is_active=True,
                last_inbound_at=datetime.now(UTC),
            )
            staff_user = User(
                id=staff_user_id,
                phone_number=staff_phone,
                display_name="Blessing Staff",
                business_id=biz_id,
                role="staff",
                niche="sme_owner",
                is_active=True,
                last_inbound_at=datetime.now(UTC),
            )
            owner_member = Member(
                id=owner_user_id,
                business_id=biz_id,
                wa_id=owner_phone,
                display_name="Alhaji Solar",
                role="owner",
                status="active",
                consent_accepted_at=datetime.now(UTC),
            )
            staff_member = Member(
                id=staff_member_id,
                business_id=biz_id,
                wa_id=staff_phone,
                display_name="Blessing Staff",
                role="staff",
                status="active",
                consent_accepted_at=datetime.now(UTC),
            )

            db.add_all([biz, owner_user, staff_user, owner_member, staff_member])
            await db.commit()
            print("  [SUCCESS] Business & Members provisioned successfully.")

            owner_actor = ActorContext(
                business_id=biz_id,
                member_id=owner_user_id,
                role="owner",
                wa_id=owner_phone,
                display_name="Alhaji Solar",
            )
            staff_actor = ActorContext(
                business_id=biz_id,
                member_id=staff_member_id,
                role="staff",
                wa_id=staff_phone,
                display_name="Blessing Staff",
            )

            # -------------------------------------------------------------
            # STEP 1: Log Attributed Sales
            # -------------------------------------------------------------
            print("\n[Step 1] Recording attributed transactions on live database...")
            now = datetime.now(UTC)
            tx1 = Transaction(
                id=uuid4(),
                business_id=biz_id,
                created_by_member_id=staff_member_id,
                transaction_type="sale",
                amount=Decimal("45000"),
                item_name="Solar Inverter 1kVA",
                quantity=Decimal("1"),
                status="confirmed",
                occurred_at=now - timedelta(hours=2),
            )
            tx2 = Transaction(
                id=uuid4(),
                business_id=biz_id,
                created_by_member_id=staff_member_id,
                transaction_type="sale",
                amount=Decimal("55000"),
                item_name="Mono Solar Panel 300W",
                quantity=Decimal("2"),
                status="confirmed",
                occurred_at=now - timedelta(hours=1),
            )
            tx3 = Transaction(
                id=uuid4(),
                business_id=biz_id,
                created_by_member_id=owner_user_id,
                transaction_type="sale",
                amount=Decimal("80000"),
                item_name="Tubular Battery 200Ah",
                quantity=Decimal("1"),
                status="confirmed",
                occurred_at=now - timedelta(minutes=30),
            )

            db.add_all([tx1, tx2, tx3])
            await db.commit()
            tx_ids.extend([tx1.id, tx2.id, tx3.id])
            print("  [SUCCESS] 3 confirmed sales inserted (Blessing: 2 sales ₦100,000, Owner: 1 sale ₦80,000).")

            # -------------------------------------------------------------
            # STEP 2: Team Sales Breakdown Analytics
            # -------------------------------------------------------------
            print("\n[Step 2] Executing AnalyticsService.get_team_sales_breakdown...")
            analytics = AnalyticsService()
            team_res = await analytics.get_team_sales_breakdown(db, biz_id, period="this_week")

            assert team_res["total_sales"] == Decimal("180000.00"), f"Expected 180000, got {team_res['total_sales']}"
            assert team_res["total_count"] == 3, f"Expected 3 transactions, got {team_res['total_count']}"
            assert team_res["top_contributor"] == "Blessing Staff", f"Expected Blessing Staff, got {team_res['top_contributor']}"
            assert "Blessing Staff: ₦100,000" in team_res["summary_text"]
            assert "Alhaji Solar: ₦80,000" in team_res["summary_text"]
            print("  [SUCCESS] Team sales breakdown deterministic metrics verified:")
            print(f"    - Total Business Sales: ₦{team_res['total_sales']:,.2f}")
            print(f"    - Top Contributor: {team_res['top_contributor']}")

            # -------------------------------------------------------------
            # STEP 3: Isolated Staff Personal Summary
            # -------------------------------------------------------------
            print("\n[Step 3] Executing AnalyticsService.get_staff_personal_summary for Blessing...")
            staff_summary = await analytics.get_staff_personal_summary(
                db, biz_id, staff_member_id, "Blessing Staff", period="this_week"
            )

            assert staff_summary["total_sales"] == Decimal("100000.00"), f"Expected 100000, got {staff_summary['total_sales']}"
            assert staff_summary["total_count"] == 2, f"Expected 2, got {staff_summary['total_count']}"
            assert "₦80,000" not in staff_summary["summary_text"], "Owner transaction leaked to staff personal report!"
            assert "Alhaji Solar" not in staff_summary["summary_text"], "Owner identity leaked to staff personal report!"
            assert "Tips to Keep Improving" in staff_summary["summary_text"]
            print("  [SUCCESS] Staff personal report strictly isolated (₦100,000 sales across 2 items, 0 leakage of owner sales).")

            # -------------------------------------------------------------
            # STEP 4: Unified Report Delivery & Visual Chart Gating
            # -------------------------------------------------------------
            print("\n[Step 4] Verifying UnifiedReportService delivery & chart RBAC...")
            mock_wa = MockLiveWhatsApp()
            unified_reports = UnifiedReportService(whatsapp=mock_wa)

            # Staff report delivery
            staff_rep = await unified_reports.generate_and_deliver_report(
                db, user_id=staff_user_id, cadence="weekly", actor=staff_actor
            )
            assert staff_rep["status"] == "sent"
            assert "Personal Staff Performance Report - Blessing Staff" in staff_rep["report"]
            assert staff_rep["chart"] is None, "Visual chart leaked to staff member!"
            print("  [SUCCESS] Staff received personal summary report only, no visual chart delivered.")

            # Staff financial chart request
            chart_res = await unified_reports.deliver_financial_chart(
                db, user_id=staff_user_id, period="monthly", actor=staff_actor
            )
            assert chart_res["status"] == "error"
            assert "restricted to the business owner" in chart_res["message"]
            print("  [SUCCESS] Staff blocked from deliver_financial_chart with role restriction error.")

            # -------------------------------------------------------------
            # STEP 5: Agent Service Financial Tool RBAC
            # -------------------------------------------------------------
            print("\n[Step 5] Testing AgentService role restrictions on financial analysis tools...")
            agent = AgentService()

            # Staff attempts get_team_performance
            res_team, _ = await agent._execute_tool(
                db, biz, staff_user, "get_team_performance", {"period": "this_week"}, raw_user_text="", actor=staff_actor
            )
            assert "restricted to the business owner" in res_team
            print("  [SUCCESS] Staff blocked from get_team_performance tool.")

            # Staff attempts get_margin_report
            res_margin, _ = await agent._execute_tool(
                db, biz, staff_user, "get_margin_report", {"period": "this_month"}, raw_user_text="", actor=staff_actor
            )
            assert "restricted to the business owner" in res_margin
            print("  [SUCCESS] Staff blocked from get_margin_report tool.")

            # Staff attempts get_cash_flow_forecast
            res_cf, _ = await agent._execute_tool(
                db, biz, staff_user, "get_cash_flow_forecast", {}, raw_user_text="", actor=staff_actor
            )
            assert "restricted to the business owner" in res_cf
            print("  [SUCCESS] Staff blocked from get_cash_flow_forecast tool.")

            # Owner executes get_team_performance
            res_owner_team, _ = await agent._execute_tool(
                db, biz, owner_user, "get_team_performance", {"period": "this_week"}, raw_user_text="", actor=owner_actor
            )
            assert "Team Sales Breakdown" in res_owner_team
            assert "Blessing Staff: ₦100,000" in res_owner_team
            print("  [SUCCESS] Owner successfully accessed get_team_performance with team breakdown.")

            # -------------------------------------------------------------
            # STEP 6: Historical Summary Scoping
            # -------------------------------------------------------------
            print("\n[Step 6] Verifying get_historical_summary scoping...")
            today_str = now.strftime("%Y-%m-%d")

            # Staff historical summary
            staff_hist, _ = await agent._execute_tool(
                db, biz, staff_user, "get_historical_summary", {"start_date": today_str}, raw_user_text="", actor=staff_actor
            )
            assert "Personal Historical Summary" in staff_hist
            assert "Total Sales Logged: ₦100,000.00" in staff_hist
            assert "₦80,000" not in staff_hist
            assert "Net Profit" not in staff_hist
            print("  [SUCCESS] Staff historical summary isolated to own sales (₦100,000.00, no net profit).")

            # Owner historical summary
            owner_hist, _ = await agent._execute_tool(
                db, biz, owner_user, "get_historical_summary", {"start_date": today_str}, raw_user_text="", actor=owner_actor
            )
            assert "Historical Summary for period" in owner_hist
            assert "Total Sales: ₦180,000.00" in owner_hist
            assert "Net Profit / Balance: ₦180,000.00" in owner_hist
            print("  [SUCCESS] Owner historical summary displays business-wide totals and net profit (₦180,000.00).")

            print("\n=============================================================")
            print("  PHASE 4 LIVE VERIFICATION COMPLETED WITH 100% SUCCESS!")
            print("=============================================================")

        finally:
            print("\n[Teardown] Cleaning up live test artifacts from Supabase...")
            # Delete transactions
            if tx_ids:
                del_tx = text("DELETE FROM transactions WHERE business_id = :biz_id")
                await db.execute(del_tx, {"biz_id": biz_id})
            # Delete members
            del_members = text("DELETE FROM members WHERE business_id = :biz_id")
            await db.execute(del_members, {"biz_id": biz_id})
            # Delete users
            del_users = text("DELETE FROM users WHERE business_id = :biz_id OR id IN (:uid1, :uid2)")
            await db.execute(del_users, {"biz_id": biz_id, "uid1": owner_user_id, "uid2": staff_user_id})
            # Delete business
            del_biz = text("DELETE FROM businesses WHERE id = :biz_id")
            await db.execute(del_biz, {"biz_id": biz_id})

            await db.commit()
            print("  [SUCCESS] All test records safely purged from Supabase.")


if __name__ == "__main__":
    asyncio.run(main())
