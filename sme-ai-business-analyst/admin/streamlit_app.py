import os
import sys
from pathlib import Path

# Ensure root application directory is in sys.path so 'app' can be imported anywhere
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import pandas as pd
import psycopg
import streamlit as st
from dotenv import load_dotenv

st.set_page_config(page_title="SME AI Founder Dashboard", layout="wide")
load_dotenv(ROOT_DIR / ".env", override=True)

from admin.auth_manager import (
    require_admin_auth,
    format_duration,
    auth_manager,
    get_live_base_url,
    set_live_base_url,
)

# Security Gatekeeper: Enforce cryptographic login, weekly expiration, and lockout shield
admin_session = require_admin_auth()


def database_url() -> str | None:
    url = os.getenv("SYNC_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not url:
        return None
    return url.replace("postgresql+asyncpg://", "postgresql://")


@st.cache_data(ttl=5)
def query_df(sql: str, params: tuple = ()) -> pd.DataFrame:
    url = database_url()
    if not url:
        return pd.DataFrame()
    try:
        with psycopg.connect(url) as conn:
            return pd.read_sql(sql, conn, params=params)
    except Exception:
        return pd.DataFrame()


def metric_value(df: pd.DataFrame, column: str, default: str = "0") -> str:
    if df.empty or column not in df:
        return default
    value = df.iloc[0][column]
    if pd.isna(value):
        return default
    return str(value)


st.title("SME AI Founder Dashboard")

metrics = query_df("select * from v_founder_dashboard_metrics")
col1, col2, col3, col4, col5, col6 = st.columns(6)
col1.metric("Active Businesses", metric_value(metrics, "active_businesses"), help="Total live SME businesses registered and active")
col2.metric("Inbound (24h)", metric_value(metrics, "inbound_messages_24h"), help="WhatsApp messages received from customers/testers in the last 24h")
col3.metric("Outbound (24h)", metric_value(metrics, "outbound_messages_24h"), help="Bot replies, reports, and reminders sent in the last 24h")
col4.metric("Pending Confirmations", metric_value(metrics, "pending_confirmations"), help="Transaction drafts currently awaiting user confirmation reply")
col5.metric("Confirmed Tx (24h)", metric_value(metrics, "confirmed_records_24h"), help="Sales and expenses officially written to ledger in the last 24h")
try:
    spend_disp = f"${float(metric_value(metrics, 'ai_spend_today_usd', '0')):.4f}"
except (ValueError, TypeError):
    spend_disp = "$0.0000"
col6.metric("AI Spend Today", spend_disp, help="Accurate AI model API cost calculated from today's token usage")

businesses = query_df(
    """
    select id::text, name, phone_number, business_type, subscription_plan, created_at
    from businesses
    where deleted_at is null and (is_provisional is false or id in (select business_id from users where business_id is not null))
    order by created_at desc
    limit 200
    """
)

selected_business_id = None
with st.sidebar:
    logo_file = Path(__file__).resolve().parent / "waasz_logo.jpg"
    if logo_file.exists():
        st.image(str(logo_file), width=90)
    st.header("⚡ System Health Pulse")
    
    # 1. DB Ping
    import time
    t0 = time.perf_counter()
    db_check = query_df("SELECT 1 as alive")
    db_latency = (time.perf_counter() - t0) * 1000
    if not db_check.empty:
        st.success(f"🟢 Database Connected ({db_latency:.1f}ms)")
    else:
        st.error("🔴 Database Offline")

    # 2. WhatsApp Meta API Status
    from app.core.config import settings
    if settings.whatsapp_access_token and not settings.whatsapp_access_token.startswith("replace-"):
        st.success("🟢 Meta WhatsApp API: Configured")
    else:
        st.warning("🟡 Meta WhatsApp API: Unconfigured")

    # 3. Live AI Engine
    try:
        from app.core.model_resolver import model_resolver
        active_model = model_resolver.get_active_model("gemini", "chat") or model_resolver.get_active_model("groq", "chat") or getattr(settings, "gemini_model", "gemini-3.8-flash")
    except Exception:
        active_model = getattr(settings, "gemini_model", "gemini-3.8-flash")
    st.info(f"🤖 Live AI Model: `{active_model}`")

    # 4. Live Webhook Endpoint (Dynamically synced with system_settings)
    from admin.auth_manager import get_live_base_url, set_live_base_url
    live_base = get_live_base_url()
    live_webhook = f"{live_base}/webhooks/whatsapp"
    st.markdown("**🌐 Active Webhook URL:**")
    st.code(live_webhook, language="text")

    with st.expander("⚡ Sync / Change Webhook URL"):
        new_endpoint = st.text_input("Active Base Endpoint", value=live_base, key="input_live_base_url")
        if st.button("Save & Sync Everywhere", key="btn_save_endpoint", use_container_width=True):
            if new_endpoint.strip():
                set_live_base_url(new_endpoint)
                st.success(f"Synced! Active endpoint set to {new_endpoint}")
                st.cache_data.clear()
                st.rerun()

    st.write("---")
    st.header("Filters & Controls")
    if not businesses.empty:
        labels = {
            row["id"]: f"{row['name']} ({row['phone_number']})"
            for _, row in businesses.iterrows()
        }
        selected_business_id = st.selectbox(
            "Business",
            options=["All", *labels.keys()],
            format_func=lambda value: labels.get(value, value),
        )
        if selected_business_id == "All":
            selected_business_id = None

    col_btn1, col_btn2 = st.columns(2)
    with col_btn1:
        if st.button("🔄 Sync Now", use_container_width=True):
            st.cache_data.clear()
            st.rerun()
    with col_btn2:
        auto_sync = st.checkbox("Live 10s Poll", value=False, help="Automatically poll and update live messages every 10 seconds")

    if auto_sync:
        time.sleep(10)
        st.cache_data.clear()
        st.rerun()

tab_overview, tab_confirmations, tab_messages, tab_simulator, tab_broadcast, tab_export, tab_costs, tab_tasks, tab_knowledge, tab_invites, tab_recovery, tab_security = st.tabs(
    ["📊 Overview", "⏳ Confirmations", "💬 Messages", "🤖 Live Bot Console", "📢 Broadcast", "📥 Data Export", "💰 Costs", "⏰ Tasks & Reminders", "🧠 Knowledge & Embeddings", "🛡️ Users & Access", "🔑 Account Recovery", "⚙️ Security & Sync"]
)

business_filter = ""
params: tuple = ()
if selected_business_id:
    business_filter = "where business_id = %s"
    params = (selected_business_id,)

with tab_overview:
    # 1. Live Onboarded Businesses & 14-Day Free Trial Tracker
    st.subheader("👥 Live Onboarded Businesses & 14-Day Free Trial Tracker")
    st.caption("Live sync of all businesses interacting with Waasz on WhatsApp: onboarding progress, trial days remaining, transactions, and reviews.")

    trial_tracker_raw = query_df(
        """
        select
            b.id::text as business_id,
            b.name as business_name,
            coalesce(u.phone_number, b.phone_number) as phone_number,
            b.created_at,
            u.last_inbound_at,
            coalesce(b.settings->>'onboarding_stage', 'completed') as onboarding_stage,
            b.settings->>'trial_started_at' as trial_started_at,
            b.settings->>'trial_expires_at' as trial_expires_at,
            b.settings->>'trial_status' as trial_status,
            b.settings->>'review_rating' as review_rating,
            b.settings->>'review_feedback' as review_feedback,
            count(t.id) as total_tx_count,
            coalesce(sum(case when t.transaction_type = 'sale' and t.status = 'confirmed' then t.amount else 0 end), 0) as total_sales_ngn
        from businesses b
        left join users u on u.business_id = b.id
        left join transactions t on t.business_id = b.id
        where b.deleted_at is null
        group by b.id, b.name, b.phone_number, u.phone_number, b.created_at, u.last_inbound_at, b.settings
        order by b.created_at desc
        """
    )

    if not trial_tracker_raw.empty:
        from datetime import datetime, timezone

        now_utc = datetime.now(timezone.utc)
        
        # Calculate summary KPI cards
        total_biz = len(trial_tracker_raw)
        active_trials_count = 0
        total_all_sales = float(trial_tracker_raw["total_sales_ngn"].sum())
        total_all_tx = int(trial_tracker_raw["total_tx_count"].sum())
        
        ratings = []
        status_list = []
        stage_list = []
        sales_disp_list = []
        joined_list = []
        last_seen_list = []
        rating_disp_list = []
        feedback_list = []

        for _, row in trial_tracker_raw.iterrows():
            # Trial calculation
            exp_str = row.get("trial_expires_at")
            if exp_str and pd.notna(exp_str):
                try:
                    exp_dt = datetime.fromisoformat(str(exp_str))
                    if exp_dt.tzinfo is None:
                        exp_dt = exp_dt.replace(tzinfo=timezone.utc)
                    days_left = (exp_dt - now_utc).days
                    if now_utc > exp_dt:
                        status_list.append("⚠️ Expired (Day 14+)")
                    else:
                        active_trials_count += 1
                        days_elapsed = max(1, 14 - max(0, days_left))
                        status_list.append(f"🟢 Day {days_elapsed} of 14 ({days_left}d left)")
                except Exception:
                    status_list.append("🟢 Active Trial")
                    active_trials_count += 1
            else:
                status_list.append("🟢 14-Day Trial")
                active_trials_count += 1

            # Stage formatting
            stg = str(row.get("onboarding_stage", "completed"))
            if stg == "awaiting_business_name":
                stage_list.append("📝 Awaiting Shop Name")
            elif stg == "awaiting_receipt_details":
                stage_list.append("🧾 Awaiting Receipt Setup")
            else:
                stage_list.append("✅ Active / Ready")

            # Sales formatting
            sales_disp_list.append(f"₦{float(row.get('total_sales_ngn', 0)):,.2f}")

            # Joined date
            c_at = row.get("created_at")
            if pd.notna(c_at):
                joined_list.append(str(c_at)[:16])
            else:
                joined_list.append("-")

            # Last seen
            l_in = row.get("last_inbound_at")
            if pd.notna(l_in):
                last_seen_list.append(str(l_in)[:16])
            else:
                last_seen_list.append("-")

            # Review & Feedback
            r_val = row.get("review_rating")
            if pd.notna(r_val) and str(r_val).isdigit():
                r_num = int(r_val)
                ratings.append(r_num)
                rating_disp_list.append(f"{'⭐' * r_num} ({r_num}/5)")
            else:
                rating_disp_list.append("Pending")

            f_val = row.get("review_feedback")
            if pd.notna(f_val) and str(f_val).strip():
                feedback_list.append(str(f_val).strip())
            else:
                feedback_list.append("-")

        # Top Trial KPIs
        kpi_c1, kpi_c2, kpi_c3, kpi_c4, kpi_c5 = st.columns(5)
        kpi_c1.metric("Total Onboarded", total_biz, help="All businesses registered via WhatsApp")
        kpi_c2.metric("Active Free Trials", active_trials_count, help="Businesses currently within their 14-day trial")
        kpi_c3.metric("Total Sales Tracked", f"₦{total_all_sales:,.0f}", help="Total sales revenue logged during trials")
        kpi_c4.metric("Transactions Logged", total_all_tx, help="Total transaction records created")
        avg_rating_str = f"⭐ {sum(ratings)/len(ratings):.1f}/5" if ratings else "No reviews yet"
        kpi_c5.metric("Avg User Rating", avg_rating_str, help="Average Week 1 review rating")

        # Create structured display DataFrame
        disp_df = pd.DataFrame({
            "Business Name": trial_tracker_raw["business_name"],
            "WhatsApp Phone": trial_tracker_raw["phone_number"],
            "Joined": joined_list,
            "Trial Status": status_list,
            "Onboarding Stage": stage_list,
            "Total Sales": sales_disp_list,
            "Transactions": trial_tracker_raw["total_tx_count"],
            "Week 1 Review": rating_disp_list,
            "Customer Feedback": feedback_list,
            "Last Active": last_seen_list,
        })

        # Search filter
        search_kw = st.text_input("🔍 Search Onboarded Businesses", placeholder="Filter by business name or phone...", key="search_tracker_input")
        if search_kw.strip():
            kw = search_kw.strip().lower()
            disp_df = disp_df[
                disp_df["Business Name"].str.lower().str.contains(kw, na=False)
                | disp_df["WhatsApp Phone"].str.lower().str.contains(kw, na=False)
            ]

        st.dataframe(disp_df, use_container_width=True, hide_index=True)

        # Highlight reviews if present
        reviews_exist = [f for f in feedback_list if f != "-"]
        if reviews_exist:
            with st.expander(f"💬 Live Customer Feedback & Reviews ({len(reviews_exist)} submitted)"):
                for idx, r_row in trial_tracker_raw[trial_tracker_raw["review_feedback"].notna()].iterrows():
                    st.markdown(f"**{r_row['business_name']}** ({r_row['phone_number']}) — {r_row.get('review_rating', 'N/A')}⭐")
                    st.caption(f"\"{r_row['review_feedback']}\"")
                    st.divider()
    else:
        st.info("No businesses onboarded yet. New WhatsApp signups will appear here live.")

    st.write("---")
    st.subheader("📊 Business Performance & Daily Rollups")
    rollups = query_df(
        f"""
        select business_date, total_sales, total_expenses, estimated_profit, confirmed_records
        from v_business_daily_rollups
        {business_filter}
        order by business_date desc
        limit 30
        """,
        params,
    )
    if rollups.empty:
        st.info("No confirmed records yet.")
    else:
        chart_df = rollups.sort_values("business_date").set_index("business_date")
        st.line_chart(chart_df[["total_sales", "total_expenses", "estimated_profit"]])
        st.dataframe(rollups, use_container_width=True, hide_index=True)

with tab_confirmations:
    st.subheader("⏳ Confirmations & Draft Operations")
    pending_df = query_df(
        f"""
        select
            c.id::text as confirmation_id,
            c.created_at,
            b.name as business,
            b.phone_number,
            c.confirmation_text,
            c.expires_at,
            case when c.expires_at < now() then '⚠️ Expired' else '⏳ Awaiting Reply' end as expiry_status
        from confirmations c
        join businesses b on b.id = c.business_id
        where c.status = 'pending'
        {"and c.business_id = %s" if selected_business_id else ""}
        order by c.created_at desc
        """,
        params,
    )

    col_p_title, col_p_clean = st.columns([3, 1])
    with col_p_title:
        st.write(f"### ⏳ Active Pending Confirmations ({len(pending_df)})")
    with col_p_clean:
        if st.button("🧹 Clear Expired Drafts", help="Marks all overdue pending confirmations as expired so 'Pending' drops to 0"):
            import psycopg

            def _clean_expired():
                url = database_url()
                if url:
                    try:
                        with psycopg.connect(url) as conn:
                            with conn.cursor() as cur:
                                cur.execute("UPDATE confirmations SET status = 'expired' WHERE status = 'pending' AND expires_at < NOW()")
                            conn.commit()
                    except Exception as e:
                        st.error(f"Failed to clear expired drafts: {e}")

            _clean_expired()
            st.success("Cleaned up expired drafts!")
            st.cache_data.clear()
            st.rerun()

    if not pending_df.empty:
        st.dataframe(pending_df, use_container_width=True, hide_index=True)

        col_act_sel, col_act_btn1, col_act_btn2 = st.columns([2, 1, 1])
        with col_act_sel:
            target_conf_id = st.selectbox(
                "Select Pending Draft for Admin Override",
                options=pending_df["confirmation_id"].tolist(),
                format_func=lambda cid: (
                    f"{pending_df.loc[pending_df['confirmation_id'] == cid, 'business'].values[0] if cid in pending_df['confirmation_id'].values else 'Draft'}: "
                    f"{str(pending_df.loc[pending_df['confirmation_id'] == cid, 'confirmation_text'].values[0])[:50] if cid in pending_df['confirmation_id'].values else ''}..."
                ),
                key="select_pending_draft_override",
            )
        with col_act_btn1:
            if target_conf_id and st.button("✅ Force Confirm Draft", key="btn_force_confirm"):
                import psycopg
                url = database_url()
                if url:
                    try:
                        with psycopg.connect(url) as conn:
                            with conn.cursor() as cur:
                                cur.execute("UPDATE confirmations SET status = 'confirmed', confirmed_at = NOW() WHERE id = %s", (target_conf_id,))
                            conn.commit()
                        st.success("Draft force-confirmed!")
                        st.cache_data.clear()
                        st.rerun()
                    except Exception as e:
                        st.error(f"Failed to force confirm draft: {e}")
        with col_act_btn2:
            if target_conf_id and st.button("❌ Reject / Cancel Draft", key="btn_reject_draft"):
                import psycopg
                url = database_url()
                if url:
                    try:
                        with psycopg.connect(url) as conn:
                            with conn.cursor() as cur:
                                cur.execute("UPDATE confirmations SET status = 'rejected' WHERE id = %s", (target_conf_id,))
                            conn.commit()
                        st.success("Draft rejected.")
                        st.cache_data.clear()
                        st.rerun()
                    except Exception as e:
                        st.error(f"Failed to reject draft: {e}")
    else:
        st.success("🎉 No pending confirmations waiting for reply! All records are confirmed or resolved.")

    st.write("---")
    st.write("### 📜 All Confirmation History")
    confirmations = query_df(
        f"""
        select
            c.created_at,
            b.name as business,
            c.status,
            c.confirmation_text,
            c.correction_text,
            c.expires_at,
            c.confirmed_at
        from confirmations c
        join businesses b on b.id = c.business_id
        {"where c.business_id = %s" if selected_business_id else ""}
        order by c.created_at desc
        limit 100
        """,
        params,
    )
    st.dataframe(confirmations, use_container_width=True, hide_index=True)

with tab_messages:
    messages = query_df(
        f"""
        select
            m.created_at,
            b.name as business,
            m.direction,
            m.message_type,
            m.status,
            coalesce(m.body, m.interactive_reply_id, m.media_id) as content
        from whatsapp_messages m
        left join businesses b on b.id = m.business_id
        {"where m.business_id = %s" if selected_business_id else ""}
        order by m.created_at desc
        limit 100
        """,
        params,
    )
    st.dataframe(messages, use_container_width=True, hide_index=True)

with tab_simulator:
    st.subheader("🤖 Interactive WhatsApp Bot Simulator")
    st.caption("Test user transactions, queries, reminders, and confirmations directly through the AI engine without needing WhatsApp.")

    users_sim_df = query_df("select u.phone_number, coalesce(u.display_name, b.name, 'Tester') as name from users u left join businesses b on b.id = u.business_id order by u.created_at desc")
    phone_options = users_sim_df["phone_number"].tolist() if not users_sim_df.empty else ["2349155716041"]

    sim_col1, sim_col2 = st.columns([1, 2])
    with sim_col1:
        st.write("#### 👤 Sender Identity")
        sim_phone = st.selectbox("Select Active User", options=phone_options, key="sim_select_user")
        custom_sim_phone = st.text_input("Or Custom Simulation Phone", placeholder="+2348012345678", key="sim_custom_phone")
        active_sim_phone = custom_sim_phone.strip() if custom_sim_phone.strip() else sim_phone

        st.info(f"Simulating inbound message from: `{active_sim_phone}`")

    with sim_col2:
        st.write("#### 💬 Message Prompt")
        quick_prompts = [
            "Sold 3 cartons of noodles for ₦21,000",
            "Bought diesel for generator ₦15,000",
            "Remind me to call distributor at 4pm today",
            "How much profit did I make this week?",
            "What is my current inventory of rice?",
            "1",
        ]
        chosen_prompt = st.selectbox("Quick Templates", ["Custom Message..."] + quick_prompts, key="sim_template_select")
        default_text = "" if chosen_prompt == "Custom Message..." else chosen_prompt
        sim_input = st.text_area("Your WhatsApp Message", value=default_text, height=100, placeholder="Type any business update or question...", key="sim_input_area")
        send_sim = st.button("🚀 Send to AI Assistant", type="primary", key="sim_send_btn")

        if send_sim and sim_input.strip():
            import asyncio
            from app.core.database import async_session_factory
            from app.schemas.whatsapp import ParsedWhatsAppMessage
            from app.services.webhook_processor import WhatsAppWebhookProcessor
            from datetime import UTC, datetime

            class CapturedWhatsApp:
                def __init__(self):
                    self.sent_messages = []
                async def send_text(self, to_phone: str, body: str, **kwargs):
                    self.sent_messages.append({"to": to_phone, "type": "text", "body": body})
                    from app.schemas.whatsapp import WhatsAppSendResult
                    return WhatsAppSendResult(message_id="sim_msg")
                async def send_interactive_buttons(self, to_phone: str, body: str, buttons, **kwargs):
                    btn_text = "\n".join([f"🔘 [{b.get('title')}] (ID: {b.get('id')})" for b in buttons])
                    self.sent_messages.append({"to": to_phone, "type": "buttons", "body": f"{body}\n\n{btn_text}"})
                    from app.schemas.whatsapp import WhatsAppSendResult
                    return WhatsAppSendResult(message_id="sim_msg")
                async def send_document_bytes(self, *args, **kwargs):
                    self.sent_messages.append({"type": "document", "body": "📄 [PDF Document / Receipt Generated]"})
                    from app.schemas.whatsapp import WhatsAppSendResult
                    return WhatsAppSendResult(message_id="sim_msg")

            mock_wa = CapturedWhatsApp()
            proc = WhatsAppWebhookProcessor(whatsapp=mock_wa)

            async def _run_sim():
                async with async_session_factory() as session:
                    parsed_msg = ParsedWhatsAppMessage(
                        message_id=f"sim_{int(datetime.now().timestamp())}",
                        from_phone=active_sim_phone,
                        message_type="text",
                        body=sim_input.strip(),
                        timestamp=datetime.now(UTC),
                    )
                    await proc.process_message(session, parsed_msg, event=None)
                    await session.commit()
                return mock_wa.sent_messages

            with st.spinner("Processing message through AI business analyst..."):
                try:
                    replies = asyncio.run(_run_sim())
                    st.success("✅ Processed successfully!")
                    st.write("### 🤖 Bot Response:")
                    for r in replies:
                        st.chat_message("assistant").markdown(r["body"])
                    st.cache_data.clear()
                except Exception as e:
                    st.error(f"Simulation failed: {e}")

with tab_broadcast:
    st.subheader("📢 WhatsApp Broadcast & Direct Announcer")
    st.caption("Send official updates, feature announcements, or alert messages directly to testers' WhatsApp.")

    testers_for_bc = query_df("select phone_number, coalesce(label, phone_number) as label from approved_testers order by added_at desc")
    tester_choices = testers_for_bc["phone_number"].tolist() if not testers_for_bc.empty else []

    bc_col1, bc_col2 = st.columns([1, 2])
    with bc_col1:
        st.write("#### 🎯 Recipients")
        bc_target = st.radio(
            "Send To",
            options=["All Approved Testers", "Specific Phone Number"],
            key="bc_target_radio",
        )
        single_phone = ""
        if bc_target == "Specific Phone Number":
            if tester_choices:
                single_phone = st.selectbox("Select Approved Tester", options=tester_choices, key="bc_select_tester")
            custom_bc_phone = st.text_input("Or Custom Number", placeholder="+2348012345678", key="bc_custom_phone_input")
            if custom_bc_phone.strip():
                single_phone = custom_bc_phone.strip()

        target_count = len(tester_choices) if bc_target == "All Approved Testers" else (1 if single_phone else 0)
        st.info(f"Target count: {target_count}")

    with bc_col2:
        st.write("#### 📝 Announcement Content")
        bc_message = st.text_area(
            "Broadcast Message",
            height=120,
            placeholder="e.g. 📢 Waasz Update: Our new automated receipt generation feature is now live! Send any transaction to test it.",
            key="bc_message_content",
        )
        send_bc = st.button("📤 Send WhatsApp Broadcast", type="primary", key="bc_send_button")

        if send_bc:
            if not bc_message.strip():
                st.error("Please enter a message.")
            else:
                targets = tester_choices if bc_target == "All Approved Testers" else ([single_phone] if single_phone else [])
                if not targets:
                    st.error("No valid recipients selected.")
                else:
                    import asyncio
                    from app.services.whatsapp_client import WhatsAppClient

                    async def _send_all():
                        client = WhatsAppClient()
                        results = []
                        for phone in targets:
                            clean_phone = phone.strip().replace(" ", "").replace("-", "")
                            res = await client.send_text(to_phone=clean_phone, body=bc_message.strip())
                            msg_id = getattr(res, "message_id", None)
                            err_msg = getattr(res, "error_message", None)
                            is_skipped = getattr(res, "skipped", False)
                            is_ok = bool(msg_id and not err_msg and not is_skipped)
                            status_desc = "Delivered" if is_ok else (err_msg or ("Skipped" if is_skipped else "Failed"))
                            results.append({
                                "phone": clean_phone,
                                "success": is_ok,
                                "message_id": msg_id or "-",
                                "status": status_desc,
                            })
                        return results

                    with st.spinner(f"Sending broadcast to {len(targets)} recipient(s)..."):
                        res_list = asyncio.run(_send_all())
                        successes = sum(1 for r in res_list if r["success"])
                        if successes > 0:
                            st.success(f"✅ Successfully sent to {successes}/{len(targets)} recipient(s)!")
                        else:
                            err_msg = res_list[0].get("status") if res_list else "No responses received"
                            st.error(f"❌ Failed to deliver message: {err_msg}")
                        st.dataframe(pd.DataFrame(res_list), use_container_width=True, hide_index=True)

with tab_export:
    st.subheader("📥 One-Click Data & Ledger Exporter")
    st.caption("Download comprehensive business ledgers, customer records, debts, and message audits as CSV files.")

    c_exp1, c_exp2 = st.columns(2)
    with c_exp1:
        st.write("#### 📊 Financial & Ledger Data")
        tx_export_df = query_df("select * from transactions order by created_at desc")
        if not tx_export_df.empty:
            st.download_button(
                label="📄 Download All Transactions (CSV)",
                data=tx_export_df.to_csv(index=False),
                file_name="transactions_export.csv",
                mime="text/csv",
            )
        else:
            st.info("No transactions to export yet.")

        debts_export_df = query_df("select * from debts order by created_at desc")
        if not debts_export_df.empty:
            st.download_button(
                label="📑 Download Customer Debts & Credit (CSV)",
                data=debts_export_df.to_csv(index=False),
                file_name="debts_export.csv",
                mime="text/csv",
            )

        inv_export_df = query_df("select * from inventory_items order by created_at desc")
        if not inv_export_df.empty:
            st.download_button(
                label="📦 Download Inventory & Stock (CSV)",
                data=inv_export_df.to_csv(index=False),
                file_name="inventory_export.csv",
                mime="text/csv",
            )

    with c_exp2:
        st.write("#### 👥 Users & System Audits")
        users_exp_df = query_df("select id, phone_number, display_name, role, is_active, created_at from users order by created_at desc")
        if not users_exp_df.empty:
            st.download_button(
                label="👥 Download Registered Users Directory (CSV)",
                data=users_exp_df.to_csv(index=False),
                file_name="users_directory.csv",
                mime="text/csv",
            )

        ai_spend_exp_df = query_df("select * from ai_cost_events order by created_at desc limit 500")
        if not ai_spend_exp_df.empty:
            st.download_button(
                label="💰 Download AI Cost Events Audit (CSV)",
                data=ai_spend_exp_df.to_csv(index=False),
                file_name="ai_costs_audit.csv",
                mime="text/csv",
            )

with tab_costs:
    costs = query_df(
        f"""
        select spend_date, estimated_spend_usd, ai_calls
        from v_daily_ai_spend
        {business_filter}
        order by spend_date desc
        limit 30
        """,
        params,
    )
    if costs.empty:
        st.info("No AI spend recorded yet.")
    else:
        spend = costs.sort_values("spend_date").set_index("spend_date")["estimated_spend_usd"]
        st.bar_chart(spend)
        st.dataframe(costs, use_container_width=True, hide_index=True)

with tab_tasks:
    tasks_df = query_df(
        f"""
        select
            t.created_at,
            b.name as business,
            t.title,
            t.description,
            t.due_at,
            t.reminder_sent_at,
            t.completed_at
        from tasks t
        join businesses b on b.id = t.business_id
        {"where t.business_id = %s" if selected_business_id else ""}
        order by t.created_at desc
        limit 100
        """,
        params,
    )
    if tasks_df.empty:
        st.info("No tasks or reminders recorded yet.")
    else:
        st.dataframe(tasks_df, use_container_width=True, hide_index=True)

with tab_knowledge:
    st.subheader("User Knowledge & Vector Embeddings")

    try:
        pending_chunks = query_df(
            """
            select count(*) as pending_count
            from user_knowledge_chunks
            where needs_reembedding is true
            """
        )
        p_count = int(pending_chunks.iloc[0]["pending_count"]) if not pending_chunks.empty else 0
    except Exception:
        p_count = 0

    if p_count > 0:
        st.warning(
            f"⚠️ **{p_count} Knowledge Chunk(s) with Temporary Pseudo-Embeddings Detected**\n\n"
            "These chunks were stored while primary embedding providers (Gemini/OpenAI) were offline or unconfigured. "
            "They use local hash pseudo-vectors and will not match user semantic search queries accurately."
        )
        if st.button("🔄 Attempt Re-Embedding Flagged Chunks Now"):
            import asyncio
            from app.core.database import async_session_factory
            from app.services.knowledge_service import KnowledgeService

            async def _run_reembed():
                async with async_session_factory() as session:
                    ks = KnowledgeService()
                    return await ks.reembed_flagged_chunks(session)

            try:
                count_done = asyncio.run(_run_reembed())
                if count_done > 0:
                    st.success(f"Successfully re-embedded and updated {count_done} chunk(s) with active semantic embeddings!")
                    st.cache_data.clear()
                    st.rerun()
                else:
                    st.error("Re-embedding attempted, but primary embedding providers are still offline/exhausted. Verify API keys and quotas.")
            except Exception as e:
                st.error(f"Error during re-embedding: {e}")
    else:
        st.success("✅ All knowledge chunks have active semantic embeddings. No chunks flagged for re-embedding.")

    docs_df = query_df(
        f"""
        select
            d.created_at,
            b.name as business,
            d.title,
            d.source_type,
            d.visibility,
            count(c.id) as total_chunks,
            count(case when c.needs_reembedding is true then 1 end) as pending_reembed
        from user_knowledge_documents d
        left join businesses b on b.id = d.business_id
        left join user_knowledge_chunks c on c.document_id = d.id
        {"where d.business_id = %s" if selected_business_id else ""}
        group by d.id, d.created_at, b.name, d.title, d.source_type, d.visibility
        order by d.created_at desc
        limit 50
        """,
        params,
    )
    if docs_df.empty:
        st.info("No knowledge documents stored yet.")
    else:
        st.dataframe(docs_df, use_container_width=True, hide_index=True)

with tab_invites:
    st.subheader("🛡️ Pluggable Access Control & User Allowlist")
    st.markdown("Manage private access strategies: **allowlist** (testers & capacity cap), **invite_code** (self-service codes), or **open**.")

    from app.core.config import settings

    # 1. Live Status & Dynamic Settings from Database
    sys_settings_df = query_df("select key, value from system_settings where key in ('max_active_users', 'access_mode')")
    db_settings = dict(zip(sys_settings_df["key"], sys_settings_df["value"])) if not sys_settings_df.empty else {}
    
    current_max_users = int(db_settings.get("max_active_users", getattr(settings, "max_active_users", 20)))
    current_access_mode = db_settings.get("access_mode", getattr(settings, "access_mode", "allowlist")).lower().strip()

    total_users_df = query_df("select count(*) as count from users")
    current_active_users = int(total_users_df["count"].values[0]) if not total_users_df.empty else 0
    testers_df = query_df("select count(*) as count from approved_testers")
    current_testers = int(testers_df["count"].values[0]) if not testers_df.empty else 0

    is_cap_reached = current_active_users >= current_max_users

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Active Access Mode", current_access_mode.upper())
    m2.metric("Registered Users", f"{current_active_users} / {current_max_users}")
    m3.metric("Approved Testers", current_testers)
    m4.metric("Capacity Status", "FULL (Cap Reached)" if is_cap_reached else "OPEN")

    if is_cap_reached:
        st.warning(
            f"⚠️ User cap of {current_max_users} reached! Inbound messages from new numbers will be paused. "
            f"You can increase your limit below or purge automated test records to free up capacity."
        )

    st.write("---")

    # 2. Configurable Capacity Limit & Access Strategy
    st.write("### ⚙️ Tester Capacity & Strategy Settings")
    st.caption("Select and update the number of testers you want, or switch access strategies dynamically.")

    col_cap_cfg, col_mode_cfg = st.columns([1, 1])
    with col_cap_cfg:
        with st.form("capacity_limit_form"):
            new_capacity = st.number_input(
                "Tester / User Capacity Limit",
                min_value=1,
                max_value=50000,
                value=current_max_users,
                step=5,
                help="Set the maximum number of users/testers allowed on the platform before the gate closes.",
            )
            cap_submit = st.form_submit_button("💾 Save Capacity Limit")
            if cap_submit:
                import psycopg
                from app.core.config import settings
                url = database_url()
                if url:
                    try:
                        with psycopg.connect(url) as conn:
                            with conn.cursor() as cur:
                                cur.execute("""
                                    INSERT INTO system_settings (key, value, updated_at)
                                    VALUES ('max_active_users', %s, NOW())
                                    ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()
                                """, (str(int(new_capacity)),))
                            conn.commit()
                        settings.max_active_users = int(new_capacity)
                        st.success(f"✅ Capacity limit updated to {new_capacity}!")
                        st.cache_data.clear()
                        st.rerun()
                    except Exception as e:
                        st.error(f"Failed to update capacity limit: {e}")

    with col_mode_cfg:
        with st.form("access_mode_form"):
            modes = ["allowlist", "invite_code", "open"]
            mode_idx = modes.index(current_access_mode) if current_access_mode in modes else 0
            selected_mode = st.selectbox(
                "Access Control Strategy",
                options=modes,
                index=mode_idx,
                format_func=lambda m: {
                    "allowlist": "Allowlist (Strict Approved Testers Only)",
                    "invite_code": "Invite Code (Self-Service Redemptions)",
                    "open": "Open (Public Self-Registration)",
                }.get(m, m),
            )
            mode_submit = st.form_submit_button("🔄 Switch Access Strategy")
            if mode_submit:
                import psycopg
                from app.core.config import settings
                url = database_url()
                if url:
                    try:
                        with psycopg.connect(url) as conn:
                            with conn.cursor() as cur:
                                cur.execute("""
                                    INSERT INTO system_settings (key, value, updated_at)
                                    VALUES ('access_mode', %s, NOW())
                                    ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()
                                """, (selected_mode.lower().strip(),))
                            conn.commit()
                        settings.access_mode = selected_mode.lower().strip()
                        st.success(f"✅ Access strategy switched to {selected_mode.upper()}!")
                        st.cache_data.clear()
                        st.rerun()
                    except Exception as e:
                        st.error(f"Failed to switch access mode: {e}")

    st.write("---")

    # 3. Live Registered Users Directory & Account Management
    st.write("### 👥 Live Registered Users Directory")
    st.caption("Manage user accounts in the database. Delete accounts, change roles, or synchronize with the approved testers allowlist.")

    users_breakdown_df = query_df(
        """
        select
            u.id::text as user_id,
            u.phone_number,
            coalesce(u.display_name, '-') as display_name,
            u.role,
            u.is_active,
            case
                when a.id is not null then '✅ Approved Tester'
                else '🟢 Self-Registered'
            end as allowlist_status,
            u.created_at
        from users u
        left join approved_testers a on (
            u.phone_number = a.phone_number 
            or ('+' || u.phone_number) = a.phone_number 
            or u.phone_number = replace(a.phone_number, '+', '')
        )
        order by u.created_at desc
        """
    )

    col_users_info, col_sync_btn = st.columns([3, 1])
    with col_users_info:
        st.write(
            f"**Total Registered Users:** {len(users_breakdown_df)} | "
            f"**Active Testers:** {current_testers}"
        )
    with col_sync_btn:
        if st.button("🔄 Sync Users with Allowlist", help="Removes any registered user accounts that are not in the approved testers list", key="btn_sync_users_allowlist"):
            import psycopg
            from scripts.export_users_table import export as export_users

            url = database_url()
            if url:
                try:
                    with psycopg.connect(url) as conn:
                        with conn.cursor() as cur:
                            cur.execute("SELECT phone_number FROM approved_testers")
                            testers_rows = cur.fetchall()
                            allowed_phones = set()
                            for (p,) in testers_rows:
                                p_str = str(p).strip()
                                allowed_phones.add(p_str)
                                allowed_phones.add(p_str.lstrip("+"))
                                allowed_phones.add(f"+{p_str.lstrip('+')}")

                            cur.execute("SELECT id, phone_number FROM users")
                            users_rows = cur.fetchall()
                            deleted = 0
                            for uid, u_phone in users_rows:
                                up_str = str(u_phone).strip()
                                if up_str not in allowed_phones and up_str.lstrip("+") not in allowed_phones:
                                    cur.execute("DELETE FROM users WHERE id = %s", (uid,))
                                    deleted += 1
                        conn.commit()

                    try:
                        export_users()
                    except Exception:
                        pass
                    remaining = len(users_rows) - deleted
                    st.success(f"Synced! Cleaned up {deleted} orphan account(s). Total users: {remaining}.")
                    st.cache_data.clear()
                    st.rerun()
                except Exception as e:
                    st.error(f"Failed to sync users: {e}")

    if users_breakdown_df.empty:
        st.info("No registered users in the database yet.")
    else:
        st.dataframe(users_breakdown_df, use_container_width=True, hide_index=True)

        col_del_u, col_del_btn = st.columns([3, 1])
        with col_del_u:
            user_to_delete = st.selectbox(
                "Select User to Delete from Database",
                options=users_breakdown_df["phone_number"].tolist(),
                format_func=lambda p: (
                    f"{p} ({users_breakdown_df.loc[users_breakdown_df['phone_number'] == p, 'display_name'].values[0] if p in users_breakdown_df['phone_number'].values else ''} | "
                    f"{users_breakdown_df.loc[users_breakdown_df['phone_number'] == p, 'allowlist_status'].values[0] if p in users_breakdown_df['phone_number'].values else ''})"
                ),
                key="select_user_to_delete",
            )
        with col_del_btn:
            st.write("")
            st.write("")
            if user_to_delete and st.button("🗑️ Delete Selected User", key="btn_delete_user"):
                import psycopg
                from scripts.export_users_table import export as export_users

                url = database_url()
                if url:
                    try:
                        clean_p = user_to_delete.strip()
                        norm_p = clean_p.lstrip("+")
                        with psycopg.connect(url) as conn:
                            with conn.cursor() as cur:
                                # 1. Remove from approved_testers if present
                                cur.execute(
                                    "DELETE FROM approved_testers WHERE phone_number = %s OR phone_number = %s OR phone_number = %s",
                                    (clean_p, norm_p, f"+{norm_p}"),
                                )
                                # 2. Remove from users
                                cur.execute(
                                    "DELETE FROM users WHERE phone_number = %s OR phone_number = %s OR phone_number = %s",
                                    (clean_p, norm_p, f"+{norm_p}"),
                                )
                            conn.commit()

                        try:
                            export_users()
                        except Exception:
                            pass
                        st.success(f"Successfully deleted user `{user_to_delete}` from the database.")
                        st.cache_data.clear()
                        st.rerun()
                    except Exception as e:
                        st.error(f"Failed to delete user: {e}")

    st.write("---")

    # 4. Approved Testers Management
    st.write("### 👥 Approved Testers (Allowlist Mode)")
    col_add_t, col_list_t = st.columns([1, 2])
    with col_add_t:
        with st.form("add_tester_form"):
            tester_phone = st.text_input("Tester Phone Number", placeholder="+2348012345678")
            tester_label = st.text_input("Label / Name (Optional)", placeholder="e.g. Amaka - QA Tester")
            submit_tester = st.form_submit_button("Add Approved Tester")

            if submit_tester and tester_phone.strip():
                import psycopg
                url = database_url()
                if url:
                    try:
                        clean_p = tester_phone.strip()
                        norm_p = clean_p.lstrip("+")
                        label_val = tester_label.strip() or None
                        with psycopg.connect(url) as conn:
                            with conn.cursor() as cur:
                                cur.execute("""
                                    INSERT INTO approved_testers (phone_number, label, added_by, added_at)
                                    VALUES (%s, %s, 'admin', NOW())
                                    ON CONFLICT (phone_number) DO UPDATE SET label = COALESCE(EXCLUDED.label, approved_testers.label)
                                """, (norm_p, label_val))
                            conn.commit()
                        st.success(f"Added {tester_phone} to approved testers allowlist!")
                        st.cache_data.clear()
                        st.rerun()
                    except Exception as e:
                        st.error(f"Failed to add tester: {e}")

    with col_list_t:
        all_testers_df = query_df(
            """
            select
                id::text as id,
                phone_number,
                label,
                added_at,
                added_by
            from approved_testers
            order by added_at desc
            limit 100
            """
        )
        if all_testers_df.empty:
            st.info("No approved testers registered yet.")
        else:
            st.dataframe(all_testers_df, use_container_width=True, hide_index=True)
            phone_to_remove = st.selectbox(
                "Remove Tester from Allowlist",
                options=all_testers_df["phone_number"].tolist(),
                key="select_remove_tester",
            )
            delete_acc = st.checkbox("Also delete user account from registered users table (Keep in Sync)", value=True, key="cb_delete_user_sync")
            if phone_to_remove and st.button("🗑️ Remove Selected Tester", key="btn_remove_tester"):
                import psycopg
                from scripts.export_users_table import export as export_users

                url = database_url()
                if url:
                    try:
                        clean_p = phone_to_remove.strip()
                        norm_p = clean_p.lstrip("+")
                        with psycopg.connect(url) as conn:
                            with conn.cursor() as cur:
                                # 1. Remove from approved_testers
                                cur.execute(
                                    "DELETE FROM approved_testers WHERE phone_number = %s OR phone_number = %s OR phone_number = %s",
                                    (clean_p, norm_p, f"+{norm_p}"),
                                )
                                # 2. If delete_acc: remove from users
                                if delete_acc:
                                    cur.execute(
                                        "DELETE FROM users WHERE phone_number = %s OR phone_number = %s OR phone_number = %s",
                                        (clean_p, norm_p, f"+{norm_p}"),
                                    )
                            conn.commit()

                        try:
                            export_users()
                        except Exception:
                            pass
                        msg = f"Removed {phone_to_remove} from allowlist"
                        if delete_acc:
                            msg += " and deleted user account"
                        st.success(f"{msg}.")
                        st.cache_data.clear()
                        st.rerun()
                    except Exception as e:
                        st.error(f"Failed to remove tester: {e}")

    st.write("---")

    # 3. Invite Codes Management
    st.write("### 🔑 Invite Codes (Invite-Code Mode)")
    col_gen, col_stats = st.columns([1, 2])
    with col_gen:
        st.write("#### ➕ Generate New Invite Code")
        with st.form("create_invite_form"):
            custom_code = st.text_input("Custom Code (Optional)", placeholder="e.g. VIP2026")
            max_uses = st.number_input("Max Redemptions", min_value=1, max_value=1000, value=1, step=1)
            expiry_days = st.number_input("Expires in Days (0 = never)", min_value=0, max_value=365, value=0, step=1)
            submit_create = st.form_submit_button("Generate Code")

            if submit_create:
                import psycopg
                import secrets
                import string
                from datetime import datetime, timezone, timedelta

                url = database_url()
                if url:
                    try:
                        c_val = custom_code.strip() if custom_code.strip() else "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(8))
                        exp = datetime.now(timezone.utc) + timedelta(days=expiry_days) if expiry_days > 0 else None
                        with psycopg.connect(url) as conn:
                            with conn.cursor() as cur:
                                cur.execute("""
                                    INSERT INTO invite_codes (code, max_uses, use_count, is_active, expires_at, created_at)
                                    VALUES (%s, %s, 0, true, %s, NOW())
                                """, (c_val, int(max_uses), exp))
                            conn.commit()
                        st.success(f"Generated invite code: **{c_val}** (Max uses: {max_uses})")
                        st.cache_data.clear()
                        st.rerun()
                    except Exception as e:
                        st.error(f"Failed to generate code: {e}")

    with col_stats:
        st.write("#### 📋 Active & Historical Codes")
        codes_df = query_df(
            """
            select
                id::text as id,
                code,
                max_uses,
                use_count,
                is_active,
                created_at,
                expires_at
            from invite_codes
            order by created_at desc
            limit 100
            """
        )
        if codes_df.empty:
            st.info("No invite codes created yet.")
        else:
            st.dataframe(codes_df, use_container_width=True, hide_index=True)

            active_codes_df = codes_df[codes_df["is_active"] == True] if not codes_df.empty else pd.DataFrame()
            if not active_codes_df.empty:
                code_to_deact = st.selectbox(
                    "Select Code to Deactivate",
                    options=active_codes_df["id"].tolist(),
                    format_func=lambda cid: (
                        f"{codes_df.loc[codes_df['id'] == cid, 'code'].values[0] if cid in codes_df['id'].values else cid} ({cid[:8]})"
                    ),
                    key="select_code_to_deact",
                )

                if code_to_deact and st.button("🚫 Deactivate Selected Code", key="btn_deact_code"):
                    import psycopg

                    url = database_url()
                    if url:
                        try:
                            with psycopg.connect(url) as conn:
                                with conn.cursor() as cur:
                                    cur.execute("UPDATE invite_codes SET is_active = false WHERE id = %s", (code_to_deact,))
                                conn.commit()
                            st.success("Invite code deactivated.")
                            st.cache_data.clear()
                            st.rerun()
                        except Exception as e:
                            st.error(f"Failed to deactivate code: {e}")

    st.write("---")
    st.subheader("📝 Access Waitlist")
    waitlist_df = query_df(
        """
        select phone_number, requested_at
        from waitlist
        order by requested_at desc
        limit 100
        """
    )
    if waitlist_df.empty:
        st.info("Waitlist is currently empty.")
    else:
        st.dataframe(waitlist_df, use_container_width=True, hide_index=True)

with tab_recovery:
    st.subheader("🔑 Admin-Assisted Account Recovery & Phone Relinking")
    st.write(
        "Reclaim an existing business account and relink its entire ledger, message history, "
        "and activities to a **NEW phone number** (e.g. lost phone, stolen SIM, or new device). "
        "All historical transactions are permanently preserved."
    )

    businesses_df = query_df(
        """
        select
            b.id::text as id,
            b.name,
            b.phone_number,
            count(t.id) as transaction_count
        from businesses b
        left join transactions t on t.business_id = b.id
        where b.deleted_at is null
        group by b.id, b.name, b.phone_number
        order by count(t.id) desc, b.created_at desc
        """
    )

    if businesses_df.empty:
        st.info("No businesses found.")
    else:
        business_options = businesses_df["id"].tolist()
        selected_rec_b_id = st.selectbox(
            "Select Account to Recover",
            options=business_options,
            format_func=lambda bid: (
                f"{businesses_df.loc[businesses_df['id'] == bid, 'name'].values[0] if bid in businesses_df['id'].values else bid} "
                f"({businesses_df.loc[businesses_df['id'] == bid, 'phone_number'].values[0] if bid in businesses_df['id'].values else ''}) "
                f"[{businesses_df.loc[businesses_df['id'] == bid, 'transaction_count'].values[0] if bid in businesses_df['id'].values else 0} txs]"
            ),
            help="Select the business account whose owner needs their number updated.",
            key="select_account_to_recover",
        )

        matching_rec_b = businesses_df.loc[businesses_df["id"] == selected_rec_b_id]
        if not matching_rec_b.empty:
            curr_phone = matching_rec_b["phone_number"].values[0]
            curr_txs = matching_rec_b["transaction_count"].values[0]
        else:
            curr_phone = "-"
            curr_txs = 0

        st.info(f"**Current Account Details:** Phone: `{curr_phone}` | Confirmed Transactions: `{curr_txs}`")

        with st.form("account_recovery_form"):
            new_target_phone = st.text_input(
                "New Phone Number",
                placeholder="+2347012345678",
                help="The user's new active WhatsApp phone number.",
            )
            v_method = st.selectbox(
                "Verification Method",
                options=[
                    "Admin Verified (In-person / Call)",
                    "CAC Registration Document",
                    "Email Domain Confirmation",
                    "Historical Transaction Reconciliation",
                    "Government Issued ID",
                ],
            )
            v_notes = st.text_area(
                "Verification Notes",
                placeholder="e.g. Owner verified via phone call from registered business line. Confirmed last 3 transaction amounts.",
            )

            submit_recovery = st.form_submit_button("⚡ Relink Phone & Restore Account")

            if submit_recovery:
                if not new_target_phone.strip():
                    st.error("Please enter a valid new phone number.")
                else:
                    import asyncio
                    from uuid import UUID
                    from app.core.database import async_session_factory
                    from app.services.account_recovery_service import AccountRecoveryError, AccountRecoveryService

                    async def _run_recovery():
                        async with async_session_factory() as session:
                            service = AccountRecoveryService()
                            return await service.relink_phone_number(
                                db=session,
                                business_id=UUID(selected_rec_b_id),
                                new_phone_number=new_target_phone.strip(),
                                verification_method=v_method,
                                notes=v_notes,
                                operator="streamlit_admin",
                            )

                    try:
                        res = asyncio.run(_run_recovery())
                        st.success(
                            f"✅ Account successfully relinked to `{res['new_phone']}`! "
                            f"All {res['transactions_preserved']} historical transactions preserved."
                        )
                        st.cache_data.clear()
                        st.rerun()
                    except AccountRecoveryError as are:
                        st.error(f"Recovery failed: {str(are)}")
                    except Exception as exc:
                        st.error(f"Unexpected error: {str(exc)}")

with tab_security:
    st.subheader("⚙️ Security, Webhook Endpoints & Live Sync")
    st.caption("Zero-downtime configuration management. All updates persist to PostgreSQL and reflect live across the application.")

    sec_col1, sec_col2 = st.columns(2)

    with sec_col1:
        st.markdown("#### 🌐 Live Webhook & Domain Sync")
        st.markdown(
            "Update your active public domain whenever your server address changes (e.g. AWS, DuckDNS, or local tunnels). "
            "This ensures client magic links and WhatsApp webhook documentation immediately reflect the live URL."
        )

        curr_base = get_live_base_url()
        st.info(f"**Current Base URL:** `{curr_base}`\n\n**WhatsApp Webhook URL:** `{curr_base}/webhooks/whatsapp`")

        with st.form("form_sync_webhook_url"):
            new_url_input = st.text_input(
                "New Public Base URL",
                value=curr_base,
                placeholder="https://waasz-sme-api.duckdns.org",
                help="Enter full URL with https:// and no trailing slash",
            )
            submit_url_sync = st.form_submit_button("⚡ Save & Sync Live Endpoint", type="primary")

            if submit_url_sync:
                if not new_url_input.strip() or not new_url_input.strip().startswith("http"):
                    st.error("Please enter a valid URL starting with http:// or https://")
                else:
                    saved_url = set_live_base_url(new_url_input)
                    st.success(f"✅ Successfully updated live base URL to `{saved_url}`! All webhook handlers synchronized.")
                    st.cache_data.clear()
                    st.rerun()

    with sec_col2:
        st.markdown("#### 🔐 Change Admin Credentials")
        st.markdown(
            "Update your private administrator account name or master password. "
            "Credentials are encrypted with Argon2id and stored securely in the database with zero hardcoding."
        )

        with st.form("form_change_admin_creds"):
            update_username = st.text_input("Account Identifier / Username", value=admin_session.get("sub", ""))
            current_pass = st.text_input("Current Master Password", type="password")
            new_pass = st.text_input("New Master Password (min 8 chars)", type="password")
            confirm_new_pass = st.text_input("Confirm New Master Password", type="password")

            submit_creds = st.form_submit_button("🔒 Update & Encrypt Credentials")

            if submit_creds:
                if not current_pass:
                    st.error("Please enter your current master password to authorize this change.")
                elif not auth_manager.verify_credentials(admin_session.get("sub", ""), current_pass):
                    st.error("Current password verification failed.")
                elif not update_username.strip() or len(update_username.strip()) < 3:
                    st.error("Account name must be at least 3 characters.")
                elif not new_pass or len(new_pass) < 8:
                    st.error("New master password must be at least 8 characters.")
                elif new_pass != confirm_new_pass:
                    st.error("New passwords do not match.")
                else:
                    try:
                        auth_manager.save_admin_credentials(update_username.strip(), new_pass)
                        # Re-issue active session token
                        new_tok = auth_manager.create_session_token(update_username.strip())
                        st.session_state["admin_session_token"] = new_tok
                        st.query_params["_sess"] = new_tok
                        st.success("✅ Credentials successfully updated and re-encrypted with Argon2id!")
                        time.sleep(0.5)
                        st.rerun()
                    except Exception as e:
                        st.error(f"Error saving credentials: {e}")

    st.write("---")
    st.markdown("#### 🛡️ Active Administrative Session Status")
    st.markdown(
        f"- **Authenticated Identity:** `{admin_session.get('sub', 'admin')}`\n"
        f"- **Session Policy:** 7-Day Cryptographic Window\n"
        f"- **Time Remaining in Session:** `{format_duration(admin_session.get('remaining_seconds', 0))}`\n"
        f"- **Signature Algorithm:** `HMAC-SHA256 (JWT)`\n"
        f"- **Anti-Brute-Force Rate Limiting:** Active (Max 5 attempts / 60s cooldown)"
    )


