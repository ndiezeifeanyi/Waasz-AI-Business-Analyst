import os
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

# Ensure root application directory is in sys.path so 'app' can be imported anywhere
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import pandas as pd
import psycopg
import streamlit as st
from dotenv import load_dotenv

st.set_page_config(
    page_title="Waasz AI — Executive Telemetry",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded",
)
load_dotenv(ROOT_DIR / ".env", override=True)

from admin.auth_manager import (
    auth_manager,
    format_duration,
    get_live_base_url,
    require_admin_auth,
    set_live_base_url,
)

# Security Gatekeeper: Enforce cryptographic login, weekly expiration, and lockout shield
admin_session = require_admin_auth()

# ---------------------------------------------------------------------------
# Executive Motion UI Design System (No AI Slop / Clean Obsidian Aesthetic)
# ---------------------------------------------------------------------------
st.markdown(
    """
<style>
/* Import Clean Professional Typography */
@import url('https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@300;400;500;600;700;800&family=Inter:wght@400;500;600&display=swap');

html, body, .stMarkdown, p, h1, h2, h3, h4, [data-testid="stMetricLabel"], [data-testid="stMetricValue"], [data-testid="stCaptionContainer"] {
    font-family: 'Plus Jakarta Sans', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
}

/* Ensure Material icon glyphs and SVG arrows never render as raw ligature text */
[data-testid="stExpander"] summary span,
[data-testid="stExpander"] summary svg,
[data-testid="stExpanderToggleIcon"],
[class*="material-symbols"],
[class*="material-icons"] {
    font-family: inherit !important;
}

/* Background & Canvas Foundations */
.stApp {
    background: radial-gradient(circle at 15% 10%, #0F172A 0%, #080D1A 60%, #050811 100%);
    color: #F8FAFC;
}

/* Hide Default Streamlit Clutter */
#MainMenu {visibility: hidden;}
footer {visibility: hidden;}
header {visibility: hidden;}
[data-testid="stToolbar"] {visibility: hidden;}

/* Motion Card Foundations */
div[data-testid="stMetric"] {
    background: rgba(17, 24, 39, 0.75);
    backdrop-filter: blur(16px);
    -webkit-backdrop-filter: blur(16px);
    border: 1px solid rgba(255, 255, 255, 0.07);
    border-radius: 14px;
    padding: 14px 16px;
    box-shadow: 0 4px 20px -2px rgba(0, 0, 0, 0.45);
    transition: transform 0.25s cubic-bezier(0.16, 1, 0.3, 1), 
                border-color 0.25s cubic-bezier(0.16, 1, 0.3, 1), 
                box-shadow 0.25s cubic-bezier(0.16, 1, 0.3, 1);
}

div[data-testid="stMetric"]:hover {
    transform: translateY(-3px);
    border-color: rgba(14, 165, 233, 0.45);
    box-shadow: 0 12px 25px -6px rgba(14, 165, 233, 0.15), 0 0 1px 1px rgba(14, 165, 233, 0.3);
}

[data-testid="stMetricLabel"] p {
    font-size: 0.72rem !important;
    font-weight: 600 !important;
    color: #94A3B8 !important;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    white-space: normal !important;
    line-height: 1.25 !important;
}

[data-testid="stMetricValue"] div {
    font-size: 1.75rem !important;
    font-weight: 700 !important;
    color: #F8FAFC !important;
    letter-spacing: -0.03em;
}

[data-testid="stMetricDelta"] svg {
    fill: #10B981 !important;
}

/* Modern Segmented Navigation Tabs */
.stTabs [data-baseweb="tab-list"] {
    gap: 6px;
    background: rgba(15, 23, 42, 0.7);
    backdrop-filter: blur(14px);
    padding: 6px;
    border-radius: 12px;
    border: 1px solid rgba(255, 255, 255, 0.08);
    margin-bottom: 24px;
}

.stTabs [data-baseweb="tab"] {
    height: 42px;
    border-radius: 8px;
    color: #94A3B8;
    font-weight: 500;
    font-size: 0.88rem;
    padding: 0 18px;
    border: none;
    background: transparent;
    transition: all 0.2s cubic-bezier(0.16, 1, 0.3, 1);
}

.stTabs [data-baseweb="tab"]:hover {
    color: #F8FAFC;
    background: rgba(255, 255, 255, 0.04);
}

.stTabs [aria-selected="true"] {
    background: rgba(14, 165, 233, 0.14) !important;
    color: #38BDF8 !important;
    font-weight: 600 !important;
    border: 1px solid rgba(14, 165, 233, 0.35) !important;
    box-shadow: 0 2px 10px rgba(14, 165, 233, 0.15);
}

/* Pulsing Status Badges */
@keyframes pulseGlowGreen {
    0% { transform: scale(0.95); box-shadow: 0 0 0 0 rgba(16, 185, 129, 0.7); }
    70% { transform: scale(1.05); box-shadow: 0 0 0 8px rgba(16, 185, 129, 0); }
    100% { transform: scale(0.95); box-shadow: 0 0 0 0 rgba(16, 185, 129, 0); }
}

@keyframes pulseGlowAmber {
    0% { transform: scale(0.95); box-shadow: 0 0 0 0 rgba(245, 158, 11, 0.7); }
    70% { transform: scale(1.05); box-shadow: 0 0 0 8px rgba(245, 158, 11, 0); }
    100% { transform: scale(0.95); box-shadow: 0 0 0 0 rgba(245, 158, 11, 0); }
}

.pulse-dot-green {
    display: inline-block;
    width: 8px;
    height: 8px;
    background-color: #10B981;
    border-radius: 50%;
    animation: pulseGlowGreen 2s infinite;
    margin-right: 8px;
}

.pulse-dot-amber {
    display: inline-block;
    width: 8px;
    height: 8px;
    background-color: #F59E0B;
    border-radius: 50%;
    animation: pulseGlowAmber 2s infinite;
    margin-right: 8px;
}

.live-badge-healthy {
    display: inline-flex;
    align-items: center;
    padding: 6px 14px;
    border-radius: 9999px;
    font-size: 0.8rem;
    font-weight: 600;
    background: rgba(16, 185, 129, 0.1);
    border: 1px solid rgba(16, 185, 129, 0.25);
    color: #34D399;
}

.live-badge-warning {
    display: inline-flex;
    align-items: center;
    padding: 6px 14px;
    border-radius: 9999px;
    font-size: 0.8rem;
    font-weight: 600;
    background: rgba(245, 158, 11, 0.1);
    border: 1px solid rgba(245, 158, 11, 0.25);
    color: #FBBF24;
}

/* Sidebar Refinement */
[data-testid="stSidebar"] {
    background-color: #090D16 !important;
    border-right: 1px solid rgba(255, 255, 255, 0.06);
}

/* Custom Buttons with Micro-Shadows */
.stButton>button {
    border-radius: 10px;
    padding: 0.55rem 1.1rem;
    font-weight: 600;
    border: 1px solid rgba(255, 255, 255, 0.1);
    transition: all 0.2s cubic-bezier(0.16, 1, 0.3, 1);
}

.stButton>button:hover {
    transform: translateY(-2px);
    border-color: rgba(14, 165, 233, 0.4);
    box-shadow: 0 6px 20px rgba(14, 165, 233, 0.25);
}

/* Dataframe Clean Styling */
div[data-testid="stDataFrame"] {
    border-radius: 12px;
    overflow: hidden;
    border: 1px solid rgba(255, 255, 255, 0.08);
}
</style>
""",
    unsafe_allow_html=True,
)


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


def metric_int(df: pd.DataFrame, column: str, default: int = 0) -> str:
    if df.empty or column not in df:
        return str(default)
    value = df.iloc[0][column]
    if pd.isna(value):
        return str(default)
    try:
        return f"{int(float(value)):,}"
    except (ValueError, TypeError):
        return str(value)


# ---------------------------------------------------------------------------
# Dynamic Real-Time Health & Alert Sync (No Stale Banners)
# ---------------------------------------------------------------------------
recent_pulse_df = query_df(
    """
    select
        count(*) filter (where status = 'failed' and created_at >= now() - interval '15 minutes') as active_fails_15m,
        count(*) filter (where status in ('delivered', 'read') and created_at >= now() - interval '15 minutes') as active_ok_15m
    from whatsapp_messages
    where direction = 'outbound'
    """
)
active_fails_15m = 0
if not recent_pulse_df.empty:
    active_fails_15m = int(recent_pulse_df.iloc[0].get("active_fails_15m", 0) or 0)

# ---------------------------------------------------------------------------
# Executive Top Header
# ---------------------------------------------------------------------------
top_col1, top_col2 = st.columns([3, 1])
with top_col1:
    st.markdown(
        """
        <div style="display: flex; align-items: center; gap: 14px; margin-bottom: 4px;">
            <span style="font-size: 1.85rem; font-weight: 800; letter-spacing: -0.04em; color: #FFFFFF;">
                WAASZ <span style="color: #38BDF8;">AI</span>
            </span>
            <span style="background: rgba(56, 189, 248, 0.12); color: #38BDF8; font-size: 0.72rem; font-weight: 700; padding: 4px 10px; border-radius: 9999px; letter-spacing: 0.05em; border: 1px solid rgba(56, 189, 248, 0.3);">
                FOUNDER TELEMETRY
            </span>
        </div>
        <p style="color: #94A3B8; font-size: 0.88rem; margin-top: -6px; margin-bottom: 16px;">
            Real-time business intelligence, conversational ledger telemetry, and Meta Cloud API delivery cockpit.
        </p>
        """,
        unsafe_allow_html=True,
    )

with top_col2:
    if active_fails_15m > 0:
        st.markdown(
            f"""
            <div style="text-align: right; padding-top: 10px;">
                <span class="live-badge-warning">
                    <span class="pulse-dot-amber"></span> Notice: {active_fails_15m} Delivery Issue(s)
                </span>
            </div>
            """,
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            """
            <div style="text-align: right; padding-top: 10px;">
                <span class="live-badge-healthy">
                    <span class="pulse-dot-green"></span> All Systems Operational
                </span>
            </div>
            """,
            unsafe_allow_html=True,
        )

# ---------------------------------------------------------------------------
# Core Executive KPI Metrics (6 Cards)
# ---------------------------------------------------------------------------
metrics = query_df("select * from v_founder_dashboard_metrics")
kpi1, kpi2, kpi3, kpi4, kpi5, kpi6 = st.columns(6)

kpi1.metric("Merchants", metric_int(metrics, "active_businesses"), help="Total live SME businesses registered and active")
kpi2.metric("Inbound (24h)", metric_int(metrics, "inbound_messages_24h"), help="WhatsApp messages received in the last 24 hours")
kpi3.metric("Outbound (24h)", metric_int(metrics, "outbound_messages_24h"), help="Dispatches sent in the last 24 hours")
kpi4.metric("Pending Drafts", metric_int(metrics, "pending_confirmations"), help="Draft records currently awaiting user confirmation")
kpi5.metric("Confirmed (24h)", metric_int(metrics, "confirmed_records_24h"), help="Sales and expenses officially written to ledger in last 24h")

try:
    spend_disp = f"${float(metric_value(metrics, 'ai_spend_today_usd', '0')):.4f}"
except (ValueError, TypeError):
    spend_disp = "$0.0000"
kpi6.metric("AI Spend (Today)", spend_disp, help="Actual AI model API cost calculated from today's token usage")

st.markdown("<div style='height: 12px;'></div>", unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Business & User Scope Filtering Controls
# ---------------------------------------------------------------------------
users_directory = query_df(
    """
    select
        b.id::text as business_id,
        coalesce(b.name, 'WhatsApp Business') as business_name,
        coalesce(u.phone_number, b.phone_number) as phone_number,
        coalesce(u.display_name, b.name) as display_name,
        b.created_at
    from businesses b
    left join users u on u.business_id = b.id
    where b.deleted_at is null
    order by b.created_at desc
    """
)

selected_business_id = None
selected_phone = None
selected_business_id = None
selected_phone = None
selected_user_display = None
user_labels = {}

if not users_directory.empty:
    for _, row in users_directory.iterrows():
        b_id = str(row["business_id"])
        ph = str(row["phone_number"] or "").strip()
        nm = str(row["business_name"] or "").strip()
        user_labels[b_id] = f"{ph} — {nm}"

# Session State for User Scope
if "user_scope_filter" not in st.session_state:
    st.session_state["user_scope_filter"] = "GLOBAL_ALL"

scope_keys = ["GLOBAL_ALL", *user_labels.keys()]

def _scope_display_label(key):
    if key == "GLOBAL_ALL":
        return "🌐 All Users & Merchants (Global Overview)"
    return f"👤 {user_labels.get(key, key)}"

def _on_sb_scope_change():
    st.session_state["user_scope_filter"] = st.session_state.get("sb_user_scope_selector", "GLOBAL_ALL")

with st.sidebar:
    logo_file = Path(__file__).resolve().parent / "waasz_logo.jpg"
    if logo_file.exists():
        st.image(str(logo_file), width=84)
    st.markdown("### ⚡ Live Mission Control")

    # 1. DB Ping
    t0 = time.perf_counter()
    db_check = query_df("SELECT 1 as alive")
    db_latency = (time.perf_counter() - t0) * 1000
    if not db_check.empty:
        st.success(f"🟢 Database: {db_latency:.1f}ms")
    else:
        st.error("🔴 Database Offline")

    # 2. WhatsApp Meta API Status
    from app.core.config import settings
    if settings.whatsapp_access_token and not settings.whatsapp_access_token.startswith("replace-"):
        st.success("🟢 Meta Cloud API: Online")
    else:
        st.warning("🟡 Meta Cloud API: Unset")

    # 3. Live AI Model
    try:
        from app.core.model_resolver import model_resolver
        active_model = model_resolver.get_active_model("gemini", "chat") or model_resolver.get_active_model("groq", "chat") or getattr(settings, "gemini_model", "gemini-3.8-flash")
    except Exception:
        active_model = getattr(settings, "gemini_model", "gemini-3.8-flash")
    st.info(f"🤖 Active AI: `{active_model}`")

    st.markdown("---")
    st.markdown("#### 👤 Merchant & User Scope")
    if user_labels:
        sb_val = st.session_state.get("user_scope_filter", "GLOBAL_ALL")
        sb_idx = scope_keys.index(sb_val) if sb_val in scope_keys else 0
        st.selectbox(
            "Select User / Business",
            options=scope_keys,
            index=sb_idx,
            format_func=_scope_display_label,
            key="sb_user_scope_selector",
            on_change=_on_sb_scope_change,
            help="Choose a specific merchant phone number to filter all tabs, metrics, live chat streams, and exports strictly to that user.",
        )

    st.markdown("---")
    st.markdown("#### ⚡ Telemetry Pulse")
    col_s1, col_s2 = st.columns(2)
    with col_s1:
        if st.button("🔄 Sync", use_container_width=True):
            st.cache_data.clear()
            st.rerun()
    with col_s2:
        auto_sync = st.checkbox("Live 15s", value=False, help="Automatically refresh dashboard every 15s")

    if auto_sync:
        time.sleep(15)
        st.cache_data.clear()
        st.rerun()

# ---------------------------------------------------------------------------
# Prominent In-Page User / Merchant Selector (Directly above tabs)
# ---------------------------------------------------------------------------
def _on_top_scope_change():
    st.session_state["user_scope_filter"] = st.session_state.get("top_user_scope_selector", "GLOBAL_ALL")

top_val = st.session_state.get("user_scope_filter", "GLOBAL_ALL")
top_idx = scope_keys.index(top_val) if top_val in scope_keys else 0

c_main_sc1, c_main_sc2 = st.columns([3, 1])
with c_main_sc1:
    st.selectbox(
        "🎯 Focus Dashboard & Exports on Specific User / Merchant:",
        options=scope_keys,
        index=top_idx,
        format_func=_scope_display_label,
        key="top_user_scope_selector",
        on_change=_on_top_scope_change,
        help="Select any merchant phone number to filter all 7 tabs, WhatsApp chats, metrics, ledger audits, and data downloads strictly to that user."
    )

with c_main_sc2:
    st.markdown("<div style='height: 28px;'></div>", unsafe_allow_html=True)
    if st.session_state.get("user_scope_filter", "GLOBAL_ALL") != "GLOBAL_ALL":
        if st.button("🌐 Reset to All Users", key="top_reset_scope_btn", use_container_width=True):
            st.session_state["user_scope_filter"] = "GLOBAL_ALL"
            st.rerun()

active_scope = st.session_state.get("user_scope_filter", "GLOBAL_ALL")
if active_scope != "GLOBAL_ALL" and active_scope in user_labels:
    selected_business_id = active_scope
    selected_user_display = user_labels.get(active_scope)
    matched_user_row = users_directory[users_directory["business_id"] == active_scope]
    if not matched_user_row.empty:
        selected_phone = str(matched_user_row.iloc[0]["phone_number"] or "").strip()

business_filter = ""
params: tuple = ()
if selected_business_id:
    business_filter = "where business_id = %s"
    params = (selected_business_id,)

if selected_business_id and selected_user_display:
    st.markdown(
        f"""
        <div style="background: rgba(14, 165, 233, 0.1); border: 1px solid rgba(14, 165, 233, 0.35); border-radius: 12px; padding: 12px 18px; margin-bottom: 16px; display: flex; align-items: center; justify-content: space-between;">
            <div>
                <span style="color: #38BDF8; font-weight: 700; font-size: 0.9rem;">👤 ACTIVE USER FOCUS:</span>
                <span style="color: #FFFFFF; font-weight: 600; margin-left: 8px;">{selected_user_display}</span>
                <div style="color: #94A3B8; font-size: 0.78rem; margin-top: 2px;">
                    Filtered view: All metrics, WhatsApp messages, confirmations, and data downloads are scoped strictly to this account.
                </div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

# ---------------------------------------------------------------------------
# Structured 7-Tab High-Impact Navigation
# ---------------------------------------------------------------------------
(
    tab_overview,
    tab_messages,
    tab_confirmations,
    tab_console_broadcast,
    tab_tasks,
    tab_costs_knowledge,
    tab_system_access,
) = st.tabs(
    [
        "📊 Executive Overview",
        "💬 Message & Delivery Hub",
        "⏳ Confirmations & Drafts",
        "🤖 Bot Console & Broadcast",
        "⏰ Tasks & Reminders",
        "💰 Costs & Knowledge",
        "⚙️ System Health & Access",
    ]
)

# ===========================================================================
# TAB 1: EXECUTIVE OVERVIEW
# ===========================================================================
with tab_overview:
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
        limit 200
        """
    )

    if not trial_tracker_raw.empty:
        now_dt = datetime.now(UTC)
        status_list, stage_list, sales_disp_list = [], [], []
        rating_disp_list, feedback_list, last_seen_list, joined_list = [], [], [], []

        for _, row in trial_tracker_raw.iterrows():
            # Joined date
            c_at = row["created_at"]
            if pd.notna(c_at):
                joined_list.append(pd.to_datetime(c_at).strftime("%b %d, %Y"))
            else:
                joined_list.append("-")

            # Trial Days remaining
            t_exp_raw = row["trial_expires_at"]
            raw_status = (row["trial_status"] or "active").lower()
            if t_exp_raw:
                try:
                    exp_dt = datetime.fromisoformat(t_exp_raw.replace("Z", "+00:00"))
                    diff_days = (exp_dt - now_dt).days
                    if raw_status == "converted":
                        status_list.append("💎 Subscribed")
                    elif diff_days < 0:
                        status_list.append(f"🔴 Expired ({abs(diff_days)}d ago)")
                    elif diff_days == 0:
                        status_list.append("🟡 Expires Today")
                    else:
                        status_list.append(f"🟢 {diff_days}d remaining")
                except Exception:
                    status_list.append("🟢 Active Trial")
            else:
                status_list.append("🟢 Active Trial")

            # Stage
            stg = (row["onboarding_stage"] or "completed").lower()
            if stg in ("completed", "ready"):
                stage_list.append("✅ Fully Onboarded")
            elif stg == "awaiting_business_name":
                stage_list.append("⏳ Name Pending")
            else:
                stage_list.append(f"🔄 {stg.replace('_', ' ').title()}")

            # Sales
            sales_val = float(row["total_sales_ngn"] or 0)
            sales_disp_list.append(f"₦{sales_val:,.2f}" if sales_val > 0 else "₦0.00")

            # Rating
            r_val = row["review_rating"]
            rating_disp_list.append(f"{r_val} ⭐" if pd.notna(r_val) and str(r_val).isdigit() else "-")

            # Feedback
            fb = row["review_feedback"]
            feedback_list.append(str(fb) if pd.notna(fb) and str(fb).strip() else "-")

            # Last seen
            l_in = row["last_inbound_at"]
            if pd.notna(l_in):
                in_dt = pd.to_datetime(l_in).tz_localize(None) if pd.to_datetime(l_in).tzinfo else pd.to_datetime(l_in)
                hrs = int((datetime.utcnow() - in_dt).total_seconds() // 3600)
                last_seen_list.append(f"{hrs}h ago" if hrs < 48 else f"{hrs // 24}d ago")
            else:
                last_seen_list.append("Never")

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

        search_kw = st.text_input("🔍 Filter Onboarded Businesses", placeholder="Type business name or phone...", key="search_tracker_input")
        if search_kw.strip():
            kw = search_kw.strip().lower()
            disp_df = disp_df[
                disp_df["Business Name"].str.lower().str.contains(kw, na=False)
                | disp_df["WhatsApp Phone"].str.lower().str.contains(kw, na=False)
            ]

        st.dataframe(disp_df, use_container_width=True, hide_index=True)

        reviews_exist = [f for f in feedback_list if f != "-"]
        if reviews_exist:
            fb_items = []
            for _, r_row in trial_tracker_raw[trial_tracker_raw["review_feedback"].notna()].iterrows():
                b_nm = str(r_row["business_name"])
                b_ph = str(r_row["phone_number"])
                b_rt = str(r_row.get("review_rating") or "N/A")
                b_txt = str(r_row["review_feedback"])
                fb_items.append(
                    f'<div style="padding: 10px 0; border-bottom: 1px solid rgba(255, 255, 255, 0.06);">'
                    f'<div style="font-weight: 600; color: #F8FAFC; font-size: 0.88rem;">{b_nm} <span style="color: #94A3B8; font-size: 0.8rem;">({b_ph})</span> — <span style="color: #FBBF24;">{b_rt} ⭐</span></div>'
                    f'<div style="color: #CBD5E1; font-size: 0.84rem; font-style: italic; margin-top: 4px;">"{b_txt}"</div>'
                    f'</div>'
                )
            all_fb_html = "".join(fb_items)
            st.markdown(
                f"""
                <details style="background: rgba(17, 24, 39, 0.75); border: 1px solid rgba(255, 255, 255, 0.08); border-radius: 12px; padding: 12px 18px; margin: 16px 0;">
                    <summary style="font-weight: 600; cursor: pointer; color: #38BDF8; font-size: 0.92rem; outline: none;">
                        💬 Customer Feedback & Reviews ({len(reviews_exist)} submitted)
                    </summary>
                    <div style="margin-top: 10px;">
                        {all_fb_html}
                    </div>
                </details>
                """,
                unsafe_allow_html=True,
            )
    else:
        st.info("No businesses onboarded yet. New WhatsApp signups will appear here live.")

    st.markdown("---")
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
        st.info("No confirmed transaction records yet.")
    else:
        chart_df = rollups.sort_values("business_date").set_index("business_date")
        st.line_chart(chart_df[["total_sales", "total_expenses", "estimated_profit"]])
        st.dataframe(rollups, use_container_width=True, hide_index=True)

# ===========================================================================
# TAB 2: MESSAGE & DELIVERY HUB
# ===========================================================================
with tab_messages:
    st.subheader("💬 WhatsApp Messages & Live Delivery Pulse")
    st.caption("Real-time monitoring of all inbound and outbound WhatsApp messages, Meta delivery receipts, read receipts, and error diagnostics.")

    # 1. Delivery pulse cards (last 24 hours)
    stats_df = query_df(
        f"""
        select
            count(*) filter (where direction = 'inbound') as inbound_count,
            count(*) filter (where direction = 'outbound') as outbound_count,
            count(*) filter (where direction = 'outbound' and status in ('delivered', 'read')) as delivered_count,
            count(*) filter (where direction = 'outbound' and status = 'read') as read_count,
            count(*) filter (where direction = 'outbound' and status = 'sent') as pending_count,
            count(*) filter (where direction = 'outbound' and status = 'failed') as failed_count
        from whatsapp_messages
        where created_at >= now() - interval '24 hours'
        {"and business_id = %s" if selected_business_id else ""}
        """,
        params,
    )

    if not stats_df.empty:
        s_row = stats_df.iloc[0]
        m_in = int(s_row.get("inbound_count", 0) or 0)
        m_out = int(s_row.get("outbound_count", 0) or 0)
        m_deliv = int(s_row.get("delivered_count", 0) or 0)
        m_read = int(s_row.get("read_count", 0) or 0)
        m_pend = int(s_row.get("pending_count", 0) or 0)
        m_fail = int(s_row.get("failed_count", 0) or 0)

        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("Inbound (24h)", m_in, help="Messages received from users in the last 24h")
        c2.metric("Outbound (24h)", m_out, help="Messages dispatched by the system in the last 24h")
        c3.metric("Delivered / Read", f"{m_deliv} ({m_read} read)", help="Messages confirmed delivered or read on WhatsApp")
        c4.metric("Pending Meta Ack", m_pend, help="Messages accepted by Meta awaiting status receipt")
        c5.metric("Failed Deliveries", m_fail, delta=f"-{m_fail}" if m_fail > 0 else "0", delta_color="inverse", help="Messages rejected by Meta Cloud API")

    # 2. Filter & Search Controls
    col_f1, col_f2 = st.columns([2, 1])
    with col_f1:
        status_filter = st.selectbox(
            "Filter Messages by Delivery Status",
            ["All", "🟢 Read / Delivered", "🟡 Sent (Pending)", "🔴 Failed Only", "📥 Inbound Only", "📤 Outbound Only"],
            key="msg_status_filter",
        )
    with col_f2:
        msg_search = st.text_input("🔍 Search Messages or Phone", placeholder="Type phone, keyword, or error...", key="msg_search_input")

    where_clauses = []
    query_params = []
    if selected_business_id:
        if selected_phone:
            clean_p = selected_phone.lstrip("+")
            where_clauses.append("(m.business_id = %s or m.to_phone in (%s, %s, %s) or m.from_phone in (%s, %s, %s))")
            query_params.extend([selected_business_id, selected_phone, clean_p, f"+{clean_p}", selected_phone, clean_p, f"+{clean_p}"])
        else:
            where_clauses.append("m.business_id = %s")
            query_params.append(selected_business_id)

    if status_filter == "🔴 Failed Only":
        where_clauses.append("m.status = 'failed'")
    elif status_filter == "🟢 Read / Delivered":
        where_clauses.append("m.status in ('read', 'delivered')")
    elif status_filter == "🟡 Sent (Pending)":
        where_clauses.append("m.status = 'sent'")
    elif status_filter == "📥 Inbound Only":
        where_clauses.append("m.direction = 'inbound'")
    elif status_filter == "📤 Outbound Only":
        where_clauses.append("m.direction = 'outbound'")

    if msg_search.strip():
        search_term = f"%{msg_search.strip()}%"
        where_clauses.append("(m.body ilike %s or m.from_phone ilike %s or m.to_phone ilike %s or m.error_message ilike %s)")
        query_params.extend([search_term, search_term, search_term, search_term])

    where_sql = ("where " + " and ".join(where_clauses)) if where_clauses else ""

    messages_df = query_df(
        f"""
        select
            m.created_at,
            b.name as business,
            coalesce(m.to_phone, m.from_phone) as phone_number,
            m.direction,
            m.status,
            m.error_message,
            coalesce(m.body, m.interactive_reply_id, m.media_id) as content
        from whatsapp_messages m
        left join businesses b on b.id = m.business_id
        {where_sql}
        order by m.created_at desc
        limit 150
        """,
        tuple(query_params),
    )

    if not messages_df.empty:
        def _fmt_status(s: str) -> str:
            st_clean = str(s or "").lower()
            if st_clean == "read":
                return "🟢 Read"
            elif st_clean == "delivered":
                return "🔵 Delivered"
            elif st_clean == "sent":
                return "🟡 Sent"
            elif st_clean == "failed":
                return "🔴 Failed"
            elif st_clean == "processed":
                return "📥 Inbound"
            return st_clean

        messages_df["Delivery Status"] = messages_df["status"].apply(_fmt_status)

        # Smart, uncluttered diagnostics drawer
        failed_subset = messages_df[messages_df["status"] == "failed"]
        if not failed_subset.empty:
            err_counts = failed_subset["error_message"].value_counts().reset_index()
            err_counts.columns = ["Meta Error Message / Reason", "Occurrences"]
            err_rows = []
            for _, r in err_counts.iterrows():
                err_msg = str(r["Meta Error Message / Reason"] or "Unknown Error")
                occ = r["Occurrences"]
                err_rows.append(
                    f'<div style="display: flex; justify-content: space-between; align-items: center; padding: 7px 0; border-bottom: 1px solid rgba(255,255,255,0.06);">'
                    f'<span style="color: #F87171; font-family: monospace; font-size: 0.84rem; word-break: break-all;">{err_msg}</span>'
                    f'<span style="background: rgba(239,68,68,0.2); color: #FCA5A5; font-size: 0.76rem; font-weight: 700; padding: 2px 8px; border-radius: 9999px; margin-left: 12px; white-space: nowrap;">{occ} failed</span>'
                    f'</div>'
                )
            all_errs_html = "".join(err_rows)
            open_attr = "open" if status_filter == "🔴 Failed Only" else ""
            st.markdown(
                f"""
                <details {open_attr} style="background: rgba(239, 68, 68, 0.08); border: 1px solid rgba(239, 68, 68, 0.25); border-radius: 12px; padding: 12px 18px; margin: 12px 0 16px 0;">
                    <summary style="font-weight: 600; cursor: pointer; color: #F87171; font-size: 0.92rem; outline: none;">
                        ⚠️ Meta Delivery Error Diagnostics ({len(failed_subset)} failures in current view)
                    </summary>
                    <div style="margin-top: 10px;">
                        {all_errs_html}
                    </div>
                </details>
                """,
                unsafe_allow_html=True,
            )

        disp_cols = ["created_at", "business", "phone_number", "direction", "Delivery Status", "error_message", "content"]
        st.dataframe(messages_df[disp_cols], use_container_width=True, hide_index=True)
    else:
        st.info("No messages match the selected filters.")

# ===========================================================================
# TAB 3: CONFIRMATIONS & DRAFTS
# ===========================================================================
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
        st.markdown(f"**Active Pending Drafts:** `{len(pending_df)}`")
    with col_p_clean:
        if st.button("🧹 Clear Expired Drafts", help="Marks all overdue pending confirmations as expired"):
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
                "Select Draft for Override",
                options=pending_df["confirmation_id"].tolist(),
                format_func=lambda cid: (
                    f"{pending_df.loc[pending_df['confirmation_id'] == cid, 'business'].values[0] if cid in pending_df['confirmation_id'].values else 'Draft'}: "
                    f"{str(pending_df.loc[pending_df['confirmation_id'] == cid, 'confirmation_text'].values[0])[:50] if cid in pending_df['confirmation_id'].values else ''}..."
                ),
                key="select_pending_draft_override",
            )
        with col_act_btn1:
            if target_conf_id and st.button("✅ Force Confirm Draft", key="btn_force_confirm", use_container_width=True):
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
            if target_conf_id and st.button("❌ Reject Draft", key="btn_reject_draft", use_container_width=True):
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
        st.markdown(
            """
            <div style="padding: 12px 16px; border-radius: 10px; background: rgba(255, 255, 255, 0.02); border: 1px solid rgba(255, 255, 255, 0.05); color: #94A3B8; font-size: 0.85rem;">
                ✨ 0 pending confirmations waiting for reply. All draft records are resolved.
            </div>
            """,
            unsafe_allow_html=True,
        )

    st.markdown("---")
    st.markdown("#### 📜 Recent Confirmation History")
    confirmations = query_df(
        f"""
        select
            c.created_at,
            b.name as business,
            c.status,
            c.confirmation_text,
            c.confirmed_at
        from confirmations c
        join businesses b on b.id = c.business_id
        {business_filter}
        order by c.created_at desc
        limit 50
        """,
        params,
    )
    st.dataframe(confirmations, use_container_width=True, hide_index=True)

# ===========================================================================
# TAB 4: BOT CONSOLE & BROADCAST
# ===========================================================================
with tab_console_broadcast:
    col_sim_sec, col_bc_sec = st.columns(2)

    with col_sim_sec:
        st.subheader("🤖 Bot Simulator")
        st.caption("Simulate inbound WhatsApp messages directly through the AI agent.")

        users_sim_df = query_df("select u.phone_number, coalesce(u.display_name, b.name, 'Tester') as name from users u left join businesses b on b.id = u.business_id order by u.created_at desc")
        phone_options = users_sim_df["phone_number"].tolist() if not users_sim_df.empty else ["2349155716041"]

        sim_phone = st.selectbox("Sender Identity", options=phone_options, key="sim_select_user")
        custom_sim_phone = st.text_input("Or Custom Phone", placeholder="+2348012345678", key="sim_custom_phone")
        active_sim_phone = custom_sim_phone.strip() if custom_sim_phone.strip() else sim_phone

        quick_prompts = [
            "Sold 3 cartons of noodles for ₦21,000",
            "Bought diesel for generator ₦15,000",
            "Remind me to call distributor at 4pm today",
            "How much profit did I make this week?",
            "What is my current inventory of rice?",
            "1",
        ]
        chosen_prompt = st.selectbox("Quick Prompts", ["Custom Message..."] + quick_prompts, key="sim_template_select")
        default_text = "" if chosen_prompt == "Custom Message..." else chosen_prompt
        sim_input = st.text_area("WhatsApp Message", value=default_text, height=90, placeholder="Type message...", key="sim_input_area")

        if st.button("🚀 Execute Simulator", type="primary", key="sim_send_btn", use_container_width=True) and sim_input.strip():
            import asyncio
            from app.core.database import async_session_factory
            from app.schemas.whatsapp import ParsedWhatsAppMessage, WhatsAppSendResult
            from app.services.webhook_processor import WhatsAppWebhookProcessor

            class CapturedWhatsApp:
                def __init__(self):
                    self.sent_messages = []
                async def send_text(self, to_phone: str, body: str, **kwargs):
                    self.sent_messages.append({"to": to_phone, "type": "text", "body": body})
                    return WhatsAppSendResult(message_id="sim_msg")
                async def send_interactive_buttons(self, to_phone: str, body: str, buttons, **kwargs):
                    btn_text = "\n".join([f"🔘 [{b.get('title')}] (ID: {b.get('id')})" for b in buttons])
                    self.sent_messages.append({"to": to_phone, "type": "buttons", "body": f"{body}\n\n{btn_text}"})
                    return WhatsAppSendResult(message_id="sim_msg")
                async def send_document_bytes(self, *args, **kwargs):
                    self.sent_messages.append({"type": "document", "body": "📄 [PDF Document / Receipt Generated]"})
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
                    for r in replies:
                        st.chat_message("assistant").markdown(r["body"])
                    st.cache_data.clear()
                except Exception as e:
                    st.error(f"Simulation failed: {e}")

    with col_bc_sec:
        st.subheader("📢 Direct WhatsApp Announcer")
        st.caption("Broadcast announcements directly to testers and onboarded merchants.")

        testers_for_bc = query_df("select phone_number, coalesce(label, phone_number) as label from approved_testers order by added_at desc")
        tester_choices = testers_for_bc["phone_number"].tolist() if not testers_for_bc.empty else []

        bc_target = st.radio("Target Audience", options=["All Approved Testers", "Single Phone Number"], key="bc_target_radio", horizontal=True)
        single_phone = ""
        if bc_target == "Single Phone Number":
            if tester_choices:
                single_phone = st.selectbox("Select Tester", options=tester_choices, key="bc_select_tester")
            custom_bc_phone = st.text_input("Or Custom Number", placeholder="+2348012345678", key="bc_custom_phone_input")
            if custom_bc_phone.strip():
                single_phone = custom_bc_phone.strip()

        bc_message = st.text_area(
            "Announcement Text",
            height=90,
            placeholder="e.g. 📢 Waasz Update: Check your daily report on WhatsApp!",
            key="bc_message_content",
        )

        if st.button("📤 Send WhatsApp Broadcast", key="bc_send_button", use_container_width=True):
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
                            st.success(f"✅ Dispatched to {successes}/{len(targets)} recipient(s)!")
                        else:
                            err_msg = res_list[0].get("status") if res_list else "No response"
                            st.error(f"❌ Failed to deliver: {err_msg}")
                        st.dataframe(pd.DataFrame(res_list), use_container_width=True, hide_index=True)

# ===========================================================================
# TAB 5: TASKS & REMINDERS
# ===========================================================================
with tab_tasks:
    st.subheader("⏰ Scheduled Tasks, Reminders & Background Jobs")
    st.caption("All business follow-ups, scheduled alerts, and cron scheduler telemetry.")

    tasks_df = query_df(
        f"""
        select
            t.created_at,
            b.name as business,
            t.title,
            t.description,
            t.due_at,
            t.is_alarm_mode,
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

# ===========================================================================
# TAB 6: COSTS & KNOWLEDGE
# ===========================================================================
with tab_costs_knowledge:
    col_c_left, col_c_right = st.columns(2)

    with col_c_left:
        st.subheader("💰 AI API Spend Telemetry")
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

    with col_c_right:
        st.subheader("🧠 RAG Knowledge & Vector Embeddings")
        st.caption("How RAG works: External documents & long-term conversation memories are embedded with pgvector (768-dim) and injected into LLM context on matching user queries.")

        # Real-time vector store statistics
        rag_stats = query_df(
            """
            select
                (select count(*) from user_knowledge_documents) as total_docs,
                (select count(*) from user_knowledge_chunks) as total_chunks,
                (select count(*) from user_knowledge_chunks where needs_reembedding is true) as pending_chunks,
                (select count(*) from conversation_memories) as total_memories
            """
        )
        r_docs = int(rag_stats.iloc[0].get("total_docs", 0) or 0) if not rag_stats.empty else 0
        r_chunks = int(rag_stats.iloc[0].get("total_chunks", 0) or 0) if not rag_stats.empty else 0
        r_pend = int(rag_stats.iloc[0].get("pending_chunks", 0) or 0) if not rag_stats.empty else 0
        r_mem = int(rag_stats.iloc[0].get("total_memories", 0) or 0) if not rag_stats.empty else 0

        c_stat1, c_stat2, c_stat3 = st.columns(3)
        c_stat1.metric("Catalogs / Docs", r_docs)
        c_stat2.metric("Embedded Chunks", r_chunks)
        c_stat3.metric("Durable Memories", r_mem)

        if r_pend > 0:
            st.warning(f"⚠️ {r_pend} chunk(s) flagged with fallback embeddings awaiting background re-embedding.")

        tab_rag_docs, tab_rag_mems, tab_rag_test = st.tabs(["📄 Catalogs & Docs", "💭 Durable Memories", "🔍 Test Semantic Retrieval"])

        with tab_rag_docs:
            docs_df = query_df(
                f"""
                select
                    d.created_at,
                    b.name as business,
                    d.title,
                    d.source_type,
                    count(c.id) as total_chunks
                from user_knowledge_documents d
                left join businesses b on b.id = d.business_id
                left join user_knowledge_chunks c on c.document_id = d.id
                {"where d.business_id = %s" if selected_business_id else ""}
                group by d.id, d.created_at, b.name, d.title, d.source_type
                order by d.created_at desc
                limit 50
                """,
                params,
            )
            if docs_df.empty:
                st.info("No knowledge documents stored yet. Save price lists or shop catalogs via WhatsApp to see them indexed.")
            else:
                st.dataframe(docs_df, use_container_width=True, hide_index=True)

        with tab_rag_mems:
            mem_df = query_df(
                f"""
                select
                    m.created_at,
                    b.name as business,
                    m.memory_type,
                    m.content
                from conversation_memories m
                left join businesses b on b.id = m.business_id
                {"where m.business_id = %s" if selected_business_id else ""}
                order by m.created_at desc
                limit 50
                """,
                params,
            )
            if mem_df.empty:
                st.info("No durable conversation memories compacted yet. Memories are automatically generated from active conversations.")
            else:
                st.dataframe(mem_df, use_container_width=True, hide_index=True)

            if st.button("⚡ Force Run Conversation Compaction", key="btn_run_compaction", use_container_width=True):
                with st.spinner("Compacting idle conversations into vector memories..."):
                    try:
                        import asyncio
                        from app.db.session import async_session_factory
                        from app.services.memory_service import MemoryService

                        async def _compact():
                            async with async_session_factory() as session:
                                mem_svc = MemoryService()
                                return await mem_svc.compact_idle_conversations(session)

                        compacted = asyncio.run(_compact())
                        st.success(f"Compaction completed: {compacted} conversation session(s) compacted into durable vector memories.")
                        st.cache_data.clear()
                        st.rerun()
                    except Exception as e:
                        st.error(f"Compaction error: {e}")

        with tab_rag_test:
            test_query = st.text_input("Enter Semantic Query", placeholder="e.g. price of bag of rice or shop address", key="rag_test_q")
            if st.button("🚀 Run Vector Search", key="btn_run_vector_search", use_container_width=True) and test_query.strip():
                with st.spinner("Searching pgvector database..."):
                    try:
                        import asyncio
                        from app.services.embedding_service import EmbeddingService

                        async def _run_search():
                            emb_svc = EmbeddingService()
                            vec = await emb_svc.embed_query(test_query.strip())
                            vec_str = "[" + ",".join(str(f) for f in vec) + "]"
                            url = database_url()
                            results = []
                            with psycopg.connect(url) as conn:
                                with conn.cursor() as cur:
                                    cur.execute("""
                                        SELECT 'document_chunk' as source, chunk_content as text,
                                               1 - (embedding <=> %s::vector) as similarity
                                        FROM user_knowledge_chunks
                                        WHERE embedding IS NOT NULL
                                        ORDER BY embedding <=> %s::vector
                                        LIMIT 3
                                    """, (vec_str, vec_str))
                                    for r in cur.fetchall():
                                        results.append({"Source": "Knowledge Chunk", "Score": f"{float(r[2])*100:.1f}%", "Content": r[1]})

                                    cur.execute("""
                                        SELECT 'conversation_memory' as source, content as text,
                                               1 - (embedding <=> %s::vector) as similarity
                                        FROM conversation_memories
                                        WHERE embedding IS NOT NULL
                                        ORDER BY embedding <=> %s::vector
                                        LIMIT 3
                                    """, (vec_str, vec_str))
                                    for r in cur.fetchall():
                                        results.append({"Source": "Durable Memory", "Score": f"{float(r[2])*100:.1f}%", "Content": r[1]})
                            return results

                        matches = asyncio.run(_run_search())
                        if matches:
                            st.success(f"Found {len(matches)} semantic matches:")
                            st.dataframe(pd.DataFrame(matches), use_container_width=True, hide_index=True)
                        else:
                            st.info("No matching records found in vector store.")
                    except Exception as e:
                        st.error(f"Vector search test failed: {e}")

    st.markdown("---")
    st.subheader("📥 Data Export & Download Center")
    st.caption("Select any merchant from the dropdown below to immediately generate and download their individual transaction ledger, debts, WhatsApp chat logs, or bank-certified financial PDF statement. Or export complete platform-wide datasets.")

    def _on_tab6_scope_change():
        st.session_state["user_scope_filter"] = st.session_state.get("tab6_export_user_selector", "GLOBAL_ALL")

    tab6_val = st.session_state.get("user_scope_filter", "GLOBAL_ALL")
    tab6_idx = scope_keys.index(tab6_val) if tab6_val in scope_keys else 0

    c_t6_sel1, c_t6_sel2 = st.columns([3, 1])
    with c_t6_sel1:
        st.selectbox(
            "🎯 Select Merchant / User to Download Data For:",
            options=scope_keys,
            index=tab6_idx,
            format_func=_scope_display_label,
            key="tab6_export_user_selector",
            on_change=_on_tab6_scope_change,
            help="Choose a user to get their individual data files and certified loan statements."
        )
    with c_t6_sel2:
        st.markdown("<div style='height: 28px;'></div>", unsafe_allow_html=True)
        if st.session_state.get("user_scope_filter", "GLOBAL_ALL") != "GLOBAL_ALL":
            if st.button("🌐 Switch to Global View", key="tab6_reset_scope_btn", use_container_width=True):
                st.session_state["user_scope_filter"] = "GLOBAL_ALL"
                st.rerun()

    export_biz_id = st.session_state.get("user_scope_filter", "GLOBAL_ALL")
    if export_biz_id != "GLOBAL_ALL" and export_biz_id in user_labels:
        biz_name = user_labels[export_biz_id].split(" — ")[-1]
        biz_phone = user_labels[export_biz_id].split(" — ")[0]
        file_prefix = f"{biz_name.lower().replace(' ', '_')}_"
        clean_p = biz_phone.lstrip("+")

        st.markdown(
            f"""
            <div style="background: rgba(14, 165, 233, 0.1); border: 1px solid rgba(14, 165, 233, 0.35); border-radius: 12px; padding: 12px 18px; margin: 12px 0 16px 0;">
                <span style="color: #38BDF8; font-weight: 700; font-size: 0.9rem;">👤 Individual User Data Package:</span>
                <span style="color: #FFFFFF; font-weight: 600; margin-left: 8px;">{user_labels[export_biz_id]}</span>
            </div>
            """,
            unsafe_allow_html=True,
        )

        tx_query = "select * from transactions where business_id = %s order by created_at desc"
        debts_query = "select * from debts where business_id = %s order by created_at desc"
        msgs_query = """
            select created_at, direction, status, coalesce(to_phone, from_phone) as phone_number, error_message, body
            from whatsapp_messages
            where business_id = %s or to_phone in (%s, %s, %s) or from_phone in (%s, %s, %s)
            order by created_at desc
        """
        q_params_tx = (export_biz_id,)
        q_params_msgs = (export_biz_id, biz_phone, clean_p, f"+{clean_p}", biz_phone, clean_p, f"+{clean_p}")

        c_dl1, c_dl2, c_dl3 = st.columns(3)
        with c_dl1:
            tx_export_df = query_df(tx_query, q_params_tx)
            if not tx_export_df.empty:
                st.download_button(
                    label=f"📄 Download Transactions CSV ({len(tx_export_df)})",
                    data=tx_export_df.to_csv(index=False),
                    file_name=f"{file_prefix}transactions.csv",
                    mime="text/csv",
                    use_container_width=True,
                )
            else:
                st.caption("No transactions for this user.")
        with c_dl2:
            debts_export_df = query_df(debts_query, q_params_tx)
            if not debts_export_df.empty:
                st.download_button(
                    label=f"📑 Download Debts CSV ({len(debts_export_df)})",
                    data=debts_export_df.to_csv(index=False),
                    file_name=f"{file_prefix}debts.csv",
                    mime="text/csv",
                    use_container_width=True,
                )
            else:
                st.caption("No debts recorded.")
        with c_dl3:
            msgs_export_df = query_df(msgs_query, q_params_msgs)
            if not msgs_export_df.empty:
                st.download_button(
                    label=f"💬 Download WhatsApp Logs CSV ({len(msgs_export_df)})",
                    data=msgs_export_df.to_csv(index=False),
                    file_name=f"{file_prefix}whatsapp_logs.csv",
                    mime="text/csv",
                    use_container_width=True,
                )
            else:
                st.caption("No chat messages recorded.")

        # Certified Statement PDF download
        st.markdown("<div style='height: 8px;'></div>", unsafe_allow_html=True)
        st.markdown("##### 🏦 Certified Bank & Loan Statement (PDF)")
        st.caption(f"Compiled, signed official ledger statement for loan review and financial records for {biz_name}.")
        try:
            import asyncio
            import concurrent.futures
            from uuid import UUID
            from app.core.database import async_session_factory
            from app.services.receipt_service import ReceiptService

            async def _get_pdf():
                async with async_session_factory() as session:
                    r_svc = ReceiptService()
                    return await r_svc.generate_statement_pdf(session, UUID(export_biz_id), days=30)

            def _run_async_pdf():
                try:
                    loop = asyncio.get_event_loop()
                    if loop.is_running():
                        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                            return executor.submit(lambda: asyncio.run(_get_pdf())).result()
                    else:
                        return loop.run_until_complete(_get_pdf())
                except RuntimeError:
                    return asyncio.run(_get_pdf())

            pdf_bytes, pdf_filename, pdf_summary = _run_async_pdf()
            st.download_button(
                label=f"🏦 Download Official Financial Statement (PDF) — {biz_name}",
                data=pdf_bytes,
                file_name=pdf_filename,
                mime="application/pdf",
                use_container_width=True,
            )
        except Exception as e:
            st.warning(f"PDF Statement generator unavailable: {e}")

    # Platform-Wide Datasets Section (Always available)
    st.markdown("<div style='height: 20px; border-top: 1px solid rgba(255,255,255,0.08); margin: 24px 0 16px 0;'></div>", unsafe_allow_html=True)
    st.markdown("##### 🌐 Global Platform Exports (All Users Combined)")
    st.caption("Download complete historical datasets across all businesses registered on the platform.")

    c_g1, c_g2, c_g3, c_g4 = st.columns(4)
    with c_g1:
        all_users_df = query_df("select id, phone_number, display_name, role, is_active, created_at from users order by created_at desc")
        if not all_users_df.empty:
            st.download_button(
                label=f"👥 All Users Directory (CSV)",
                data=all_users_df.to_csv(index=False),
                file_name="waasz_users_directory.csv",
                mime="text/csv",
                use_container_width=True,
            )
    with c_g2:
        all_tx_df = query_df("select * from transactions order by created_at desc limit 2000")
        if not all_tx_df.empty:
            st.download_button(
                label=f"📄 All Transactions (CSV)",
                data=all_tx_df.to_csv(index=False),
                file_name="all_transactions.csv",
                mime="text/csv",
                use_container_width=True,
            )
    with c_g3:
        all_debts_df = query_df("select * from debts order by created_at desc limit 2000")
        if not all_debts_df.empty:
            st.download_button(
                label=f"📑 All Customer Debts (CSV)",
                data=all_debts_df.to_csv(index=False),
                file_name="all_debts.csv",
                mime="text/csv",
                use_container_width=True,
            )
    with c_g4:
        ai_cost_df = query_df("select * from ai_cost_events order by created_at desc limit 1000")
        if not ai_cost_df.empty:
            st.download_button(
                label=f"💰 AI Spend Events (CSV)",
                data=ai_cost_df.to_csv(index=False),
                file_name="ai_cost_events.csv",
                mime="text/csv",
                use_container_width=True,
            )

# ===========================================================================
# TAB 7: SYSTEM HEALTH & ACCESS
# ===========================================================================
with tab_system_access:
    sys_settings_df = query_df("select key, value from system_settings where key in ('max_active_users', 'access_mode')")
    db_settings = dict(zip(sys_settings_df["key"], sys_settings_df["value"])) if not sys_settings_df.empty else {}

    current_max_users = int(db_settings.get("max_active_users", getattr(settings, "max_active_users", 20)))
    current_access_mode = db_settings.get("access_mode", getattr(settings, "access_mode", "allowlist")).lower().strip()

    total_users_df = query_df("select count(*) as count from users")
    current_active_users = int(total_users_df["count"].values[0]) if not total_users_df.empty else 0

    subtab_strat, subtab_invites, subtab_testers, subtab_webhook = st.tabs([
        "🛡️ Access Strategy & Capacity",
        "🎟️ Invite Codes Management",
        "👥 Approved Testers Allowlist",
        "🌐 Webhook & Admin Security",
    ])

    with subtab_strat:
        col_st1, col_st2 = st.columns(2)
        with col_st1:
            st.subheader("🛡️ Current Strategy & Capacity")
            m_col1, m_col2 = st.columns(2)
            m_col1.metric("Active Strategy", current_access_mode.upper())
            m_col2.metric("User Capacity", f"{current_active_users} / {current_max_users}")

            if current_access_mode == "open":
                st.info("🟢 **Open Access Mode**: Anyone messaging Waasz on WhatsApp can register and onboard immediately up to the capacity limit.")
            elif current_access_mode == "invite_code":
                st.warning("🎟️ **Invite Code Mode**: New users must provide a valid invite code to register. Open the **🎟️ Invite Codes Management** tab to generate and send codes.")
            elif current_access_mode == "allowlist":
                st.warning("👥 **Allowlist Mode**: Only phone numbers added to the approved testers directory can register. Open the **👥 Approved Testers Allowlist** tab to manage numbers.")

        with col_st2:
            st.subheader("⚙️ Update Strategy Settings")
            with st.form("capacity_settings_form"):
                new_capacity = st.number_input("Max User Limit", min_value=1, max_value=50000, value=current_max_users, step=5)
                modes = ["open", "invite_code", "allowlist"]
                mode_idx = modes.index(current_access_mode) if current_access_mode in modes else 0
                new_mode = st.selectbox(
                    "Access Strategy Mode",
                    options=modes,
                    index=mode_idx,
                    format_func=lambda m: "🟢 Open Access" if m == "open" else ("🎟️ Invite Code Only" if m == "invite_code" else "👥 Allowlist Only"),
                )
                if st.form_submit_button("Save Strategy & Capacity", use_container_width=True):
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
                                    cur.execute("""
                                        INSERT INTO system_settings (key, value, updated_at)
                                        VALUES ('access_mode', %s, NOW())
                                        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()
                                    """, (new_mode.lower().strip(),))
                                conn.commit()
                            st.success("Access settings updated successfully!")
                            st.cache_data.clear()
                            st.rerun()
                        except Exception as e:
                            st.error(f"Failed to update access settings: {e}")

    with subtab_invites:
        st.subheader("🎟️ Invite Code Operations & Dispatch")
        st.caption("Generate private access codes, track usages, and dispatch invite codes directly to users via WhatsApp.")

        import secrets
        default_gen_code = f"WAASZ-{secrets.token_hex(3).upper()}"

        col_ic_create, col_ic_table = st.columns([1, 2])
        with col_ic_create:
            with st.form("create_invite_code_form"):
                st.markdown("##### ➕ Create New Invite Code")
                gen_code_input = st.text_input("Invite Code", value=default_gen_code, key="input_new_invite_code")
                gen_max_uses = st.number_input("Max Uses", min_value=1, max_value=500, value=1, step=1, key="input_new_invite_uses")
                gen_expiry_days = st.selectbox("Expires In", options=["Never", "7 Days", "14 Days", "30 Days"], index=0, key="select_invite_expiry")
                gen_send_phone = st.text_input("WhatsApp Recipient (Optional)", placeholder="2348012345678", key="input_invite_send_phone")

                if st.form_submit_button("🎟️ Generate & Save Invite Code", use_container_width=True):
                    code_to_create = gen_code_input.strip().upper()
                    if not code_to_create:
                        st.error("Invite code cannot be empty.")
                    else:
                        url = database_url()
                        if url:
                            try:
                                exp_sql = "NULL"
                                if gen_expiry_days == "7 Days":
                                    exp_sql = "NOW() + interval '7 days'"
                                elif gen_expiry_days == "14 Days":
                                    exp_sql = "NOW() + interval '14 days'"
                                elif gen_expiry_days == "30 Days":
                                    exp_sql = "NOW() + interval '30 days'"

                                with psycopg.connect(url) as conn:
                                    with conn.cursor() as cur:
                                        cur.execute(f"""
                                            INSERT INTO invite_codes (code, max_uses, use_count, is_active, expires_at, created_at)
                                            VALUES (%s, %s, 0, true, {exp_sql}, NOW())
                                            ON CONFLICT (code) DO UPDATE SET max_uses = EXCLUDED.max_uses, is_active = true
                                        """, (code_to_create, int(gen_max_uses)))
                                    conn.commit()

                                st.success(f"Invite code '{code_to_create}' generated successfully!")

                                # If recipient phone was supplied, dispatch directly via WhatsApp
                                if gen_send_phone.strip():
                                    clean_dest_phone = gen_send_phone.strip().replace(" ", "").replace("+", "")
                                    import asyncio
                                    from app.services.whatsapp_client import WhatsAppClient

                                    async def _send_code():
                                        client = WhatsAppClient()
                                        msg = (
                                            f"🎉 Hello! You've been invited to access *Waasz AI Business Manager*.\n\n"
                                            f"Your exclusive private invite code is:\n"
                                            f"👉 *{code_to_create}*\n\n"
                                            f"Simply reply directly to this chat with your code to activate your account!"
                                        )
                                        return await client.send_text(to_phone=clean_dest_phone, body=msg)

                                    send_res = asyncio.run(_send_code())
                                    if getattr(send_res, "message_id", None):
                                        st.success(f"📲 Dispatched invite code to {clean_dest_phone} via WhatsApp!")
                                    else:
                                        st.warning(f"Invite code saved, but WhatsApp dispatch failed: {getattr(send_res, 'error_message', 'Check Meta token')}")

                                st.cache_data.clear()
                                st.rerun()
                            except Exception as e:
                                st.error(f"Failed to create invite code: {e}")

        with col_ic_table:
            st.markdown("##### 📋 Active & Historic Invite Codes")
            invite_codes_df = query_df(
                """
                select
                    code,
                    use_count || ' / ' || max_uses as usage,
                    case
                        when is_active is false then '🔴 Revoked'
                        when expires_at is not null and expires_at < now() then '⏳ Expired'
                        when use_count >= max_uses then '⚪ Exhausted'
                        else '🟢 Active'
                    end as status,
                    expires_at,
                    created_at
                from invite_codes
                order by created_at desc
                limit 50
                """
            )
            if invite_codes_df.empty:
                st.info("No invite codes created yet.")
            else:
                st.dataframe(invite_codes_df, use_container_width=True, hide_index=True)

                # Quick revoke action
                active_codes = invite_codes_df[invite_codes_df["status"] == "🟢 Active"]["code"].tolist()
                if active_codes:
                    with st.form("revoke_code_form"):
                        code_to_revoke = st.selectbox("Revoke Code", options=active_codes, key="sel_code_revoke")
                        if st.form_submit_button("🚫 Revoke Selected Code", use_container_width=True):
                            url = database_url()
                            if url:
                                try:
                                    with psycopg.connect(url) as conn:
                                        with conn.cursor() as cur:
                                            cur.execute("UPDATE invite_codes SET is_active = false WHERE code = %s", (code_to_revoke,))
                                        conn.commit()
                                    st.success(f"Revoked {code_to_revoke} successfully.")
                                    st.cache_data.clear()
                                    st.rerun()
                                except Exception as e:
                                    st.error(f"Revoke failed: {e}")

            # Waitlist View
            st.markdown("##### ⏳ Waitlist Inquiries ('REQUEST' SMS/Chat)")
            waitlist_df = query_df("select phone_number, registered_at, notes from waitlist order by registered_at desc limit 20")
            if waitlist_df.empty:
                st.caption("No users currently waiting on the waitlist.")
            else:
                st.dataframe(waitlist_df, use_container_width=True, hide_index=True)

    with subtab_testers:
        st.subheader("👥 Approved Testers Allowlist")
        st.caption("Manage testers permitted to interact when Allowlist mode is active.")

        col_add_t, col_view_t = st.columns([1, 2])
        with col_add_t:
            with st.form("add_tester_form"):
                st.markdown("##### ➕ Add Approved Tester")
                new_t_phone = st.text_input("Phone Number", placeholder="2348012345678", key="input_tester_phone")
                new_t_label = st.text_input("Name / Label", placeholder="Chief Obi", key="input_tester_label")
                if st.form_submit_button("Add to Allowlist", use_container_width=True):
                    if new_t_phone.strip():
                        url = database_url()
                        if url:
                            try:
                                clean_t_phone = new_t_phone.strip().replace(" ", "").replace("+", "")
                                with psycopg.connect(url) as conn:
                                    with conn.cursor() as cur:
                                        cur.execute("""
                                            INSERT INTO approved_testers (phone_number, label, added_at)
                                            VALUES (%s, %s, NOW())
                                            ON CONFLICT (phone_number) DO UPDATE SET label = EXCLUDED.label
                                        """, (clean_t_phone, new_t_label.strip() or "Tester"))
                                    conn.commit()
                                st.success(f"Added {clean_t_phone} to approved testers!")
                                st.cache_data.clear()
                                st.rerun()
                            except Exception as e:
                                st.error(f"Error adding tester: {e}")
                    else:
                        st.error("Please enter a phone number.")

        with col_view_t:
            st.markdown("##### 📋 Approved Testers Directory")
            testers_df = query_df("select phone_number, label, added_at from approved_testers order by added_at desc")
            if not testers_df.empty:
                st.dataframe(testers_df, use_container_width=True, hide_index=True)
                phone_to_remove = st.selectbox("Select Tester to Remove", options=testers_df["phone_number"].tolist(), key="sel_rem_tester")
                if st.button("🗑️ Remove Tester", key="btn_remove_tester"):
                    url = database_url()
                    if url:
                        try:
                            with psycopg.connect(url) as conn:
                                with conn.cursor() as cur:
                                    cur.execute("DELETE FROM approved_testers WHERE phone_number = %s", (phone_to_remove,))
                                conn.commit()
                            st.success(f"Removed {phone_to_remove} from allowlist.")
                            st.cache_data.clear()
                            st.rerun()
                        except Exception as e:
                            st.error(f"Failed to remove tester: {e}")
            else:
                st.info("No approved testers registered yet.")

    with subtab_webhook:
        col_w1, col_w2 = st.columns(2)
        with col_w1:
            st.subheader("🌐 Webhook Sync")
            curr_base = get_live_base_url()
            st.markdown(f"**Live Webhook:** `{curr_base}/webhooks/whatsapp`")

            with st.form("webhook_update_form"):
                new_url_input = st.text_input("Public Base Endpoint", value=curr_base)
                if st.form_submit_button("Update & Sync Endpoint", use_container_width=True):
                    if new_url_input.strip().startswith("http"):
                        set_live_base_url(new_url_input)
                        st.success("Synced endpoint successfully!")
                        st.cache_data.clear()
                        st.rerun()
                    else:
                        st.error("Invalid URL format.")

        with col_w2:
            st.subheader("🛡️ Admin Security Session")
            st.caption(
                f"Logged in as: **{admin_session.get('sub', 'admin')}** | "
                f"Time remaining: **{format_duration(admin_session.get('remaining_seconds', 0))}**"
            )
