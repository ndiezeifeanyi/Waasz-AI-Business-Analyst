import os

import pandas as pd
import psycopg
import streamlit as st
from dotenv import load_dotenv

st.set_page_config(page_title="SME AI Founder Dashboard", layout="wide")
load_dotenv()


def database_url() -> str | None:
    url = os.getenv("SYNC_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not url:
        return None
    return url.replace("postgresql+asyncpg://", "postgresql://")


@st.cache_data(ttl=30)
def query_df(sql: str, params: tuple = ()) -> pd.DataFrame:
    url = database_url()
    if not url:
        return pd.DataFrame()
    with psycopg.connect(url) as conn:
        return pd.read_sql(sql, conn, params=params)


def metric_value(df: pd.DataFrame, column: str, default: str = "0") -> str:
    if df.empty or column not in df:
        return default
    value = df.iloc[0][column]
    if pd.isna(value):
        return default
    return str(value)


st.title("SME AI Founder Dashboard")

metrics = query_df("select * from v_founder_dashboard_metrics")
col1, col2, col3, col4, col5 = st.columns(5)
col1.metric("Businesses", metric_value(metrics, "active_businesses"))
col2.metric("Inbound 24h", metric_value(metrics, "inbound_messages_24h"))
col3.metric("Pending", metric_value(metrics, "pending_confirmations"))
col4.metric("Confirmed 24h", metric_value(metrics, "confirmed_records_24h"))
col5.metric("AI spend today", f"${metric_value(metrics, 'ai_spend_today_usd', '0')}")

businesses = query_df(
    """
    select id::text, name, phone_number, business_type, subscription_plan, created_at
    from businesses
    where deleted_at is null and is_provisional is false
    order by created_at desc
    limit 200
    """
)

selected_business_id = None
with st.sidebar:
    st.header("Filters")
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
    if st.button("Refresh"):
        st.cache_data.clear()
        st.rerun()

tab_overview, tab_confirmations, tab_messages, tab_costs, tab_tasks, tab_knowledge, tab_invites, tab_recovery = st.tabs(
    ["Overview", "Confirmations", "Messages", "Costs", "Tasks & Reminders", "Knowledge & Embeddings", "Invite Codes & Access", "🔑 Account Recovery"]
)

business_filter = ""
params: tuple = ()
if selected_business_id:
    business_filter = "where business_id = %s"
    params = (selected_business_id,)

with tab_overview:
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
    confirmations = query_df(
        f"""
        select
            c.created_at,
            b.name as business,
            c.status,
            c.confirmation_text,
            c.correction_text,
            c.expires_at
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

    # 1. Live Status & Metrics
    total_users_df = query_df("select count(*) as count from users")
    current_active_users = int(total_users_df["count"].values[0]) if not total_users_df.empty else 0
    testers_df = query_df("select count(*) as count from approved_testers")
    current_testers = int(testers_df["count"].values[0]) if not testers_df.empty else 0

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Active Access Mode", settings.access_mode.upper())
    m2.metric("Registered Users", f"{current_active_users} / {settings.max_active_users}")
    m3.metric("Approved Testers", current_testers)
    m4.metric("Capacity Status", "FULL (Cap Reached)" if current_active_users >= settings.max_active_users else "OPEN")

    if current_active_users >= settings.max_active_users:
        st.warning(f"⚠️ Hard user cap of {settings.max_active_users} reached! Inbound traffic will be blocked regardless of allowlist status.")

    st.write("---")

    # 2. Approved Testers Management
    st.write("### 👥 Approved Testers (Allowlist Mode)")
    col_add_t, col_list_t = st.columns([1, 2])
    with col_add_t:
        with st.form("add_tester_form"):
            tester_phone = st.text_input("Tester Phone Number", placeholder="+2348012345678")
            tester_label = st.text_input("Label / Name (Optional)", placeholder="e.g. Amaka - QA Tester")
            submit_tester = st.form_submit_button("Add Approved Tester")

            if submit_tester and tester_phone.strip():
                import asyncio
                from app.core.database import async_session_factory
                from app.services.access_control_service import AccessControlService

                async def _add():
                    async with async_session_factory() as session:
                        return await AccessControlService().add_approved_tester(
                            session, phone=tester_phone.strip(), label=tester_label.strip() or None
                        )

                try:
                    asyncio.run(_add())
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
            )
            if phone_to_remove and st.button("🗑️ Remove Selected Tester"):
                import asyncio
                from app.core.database import async_session_factory
                from app.services.access_control_service import AccessControlService

                async def _remove():
                    async with async_session_factory() as session:
                        return await AccessControlService().remove_approved_tester(session, phone_to_remove)

                if asyncio.run(_remove()):
                    st.success(f"Removed {phone_to_remove} from allowlist.")
                    st.cache_data.clear()
                    st.rerun()

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
                import asyncio
                from app.core.database import async_session_factory
                from app.services.invite_service import InviteCodeService
                from datetime import datetime, UTC, timedelta

                async def _create():
                    async with async_session_factory() as session:
                        exp = datetime.now(UTC) + timedelta(days=expiry_days) if expiry_days > 0 else None
                        return await InviteCodeService().create_code(
                            session,
                            code=custom_code if custom_code.strip() else None,
                            max_uses=int(max_uses),
                            expires_at=exp,
                        )

                try:
                    new_code = asyncio.run(_create())
                    st.success(f"Generated invite code: **{new_code.code}** (Max uses: {new_code.max_uses})")
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

            code_to_deact = st.selectbox(
                "Select Code to Deactivate",
                options=codes_df[codes_df["is_active"] == True]["id"].tolist(),
                format_func=lambda cid: f"{codes_df.loc[codes_df['id'] == cid, 'code'].values[0]} ({cid[:8]})"
                if cid in codes_df["id"].values
                else cid,
            ) if any(codes_df["is_active"] == True) else None

            if code_to_deact and st.button("🚫 Deactivate Selected Code"):
                import asyncio
                from uuid import UUID
                from app.core.database import async_session_factory
                from app.services.invite_service import InviteCodeService

                async def _deact():
                    async with async_session_factory() as session:
                        return await InviteCodeService().deactivate_code(session, UUID(code_to_deact))

                if asyncio.run(_deact()):
                    st.success("Invite code deactivated.")
                    st.cache_data.clear()
                    st.rerun()

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
                f"{businesses_df.loc[businesses_df['id'] == bid, 'name'].values[0]} "
                f"({businesses_df.loc[businesses_df['id'] == bid, 'phone_number'].values[0]}) "
                f"[{businesses_df.loc[businesses_df['id'] == bid, 'transaction_count'].values[0]} txs]"
            ),
            help="Select the business account whose owner needs their number updated.",
        )

        curr_phone = businesses_df.loc[businesses_df["id"] == selected_rec_b_id, "phone_number"].values[0]
        curr_txs = businesses_df.loc[businesses_df["id"] == selected_rec_b_id, "transaction_count"].values[0]

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


