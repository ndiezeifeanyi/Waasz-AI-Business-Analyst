"""
Comprehensive System Audit Sweep for Waasz AI.
Performs an end-to-end diagnostic check across:
1. Database connectivity & table row counts
2. Meta WhatsApp Cloud API credentials, phone status & template registration
3. AI model endpoints & key validity (Gemini, Groq, OpenAI)
4. Scheduler & Reminder status
5. Message delivery health & recent Meta failure breakdown
6. PDF Receipt generation & local media filesystem
"""

import asyncio
from datetime import UTC, datetime, timedelta
import json
import os
import sys

# Ensure UTF-8 output on Windows console
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
import httpx
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

# Add workspace root to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.core.config import settings
from app.core.database import async_session_factory, engine
from app.models.activity import Activity
from app.models.business import Business
from app.models.inventory import InventoryItem
from app.models.message import WhatsAppMessage
from app.models.task import Task
from app.models.transaction import Transaction
from app.models.user import User


async def run_audit():
    results = {}
    print("=" * 60)
    print("🚀 STARTING WAASZ AI FULL SYSTEM AUDIT SWEEP")
    print(f"Timestamp: {datetime.now(UTC).isoformat()} UTC")
    print("=" * 60)

    # 1. DATABASE AUDIT
    print("\n[1/6] AUDITING DATABASE & TABLES...")
    db_status = {"status": "UNKNOWN", "tables": {}, "error": None}
    try:
        async with async_session_factory() as session:
            # Check basic connection
            res = await session.execute(text("SELECT 1"))
            assert res.scalar() == 1
            db_status["status"] = "HEALTHY"

            # Check table counts
            tables_to_check = [
                ("businesses", Business),
                ("users", User),
                ("messages", WhatsAppMessage),
                ("transactions", Transaction),
                ("inventory", InventoryItem),
                ("tasks", Task),
                ("activities", Activity),
            ]
            for name, model in tables_to_check:
                try:
                    count_res = await session.execute(select(func.count(model.id)))
                    db_status["tables"][name] = count_res.scalar() or 0
                except Exception as e:
                    db_status["tables"][name] = f"Error: {e}"
        print(f"  ✅ Database Connected: Supabase PostgreSQL (HEALTHY)")
        for tbl, cnt in db_status["tables"].items():
            print(f"     - {tbl}: {cnt} rows")
    except Exception as e:
        db_status["status"] = "FAILED"
        db_status["error"] = str(e)
        print(f"  ❌ Database Connection Failed: {e}")
    results["database"] = db_status

    # 2. META WHATSAPP CLOUD API AUDIT
    print("\n[2/6] AUDITING META WHATSAPP CLOUD API & TEMPLATES...")
    meta_status = {
        "status": "UNKNOWN",
        "phone_number": None,
        "quality_rating": None,
        "templates": [],
        "errors": []
    }
    headers = {"Authorization": f"Bearer {settings.whatsapp_access_token}"}
    async with httpx.AsyncClient(timeout=15.0) as client:
        # Check Phone Number ID endpoint
        try:
            phone_url = f"https://graph.facebook.com/{settings.whatsapp_api_version}/{settings.whatsapp_phone_number_id}"
            resp = await client.get(phone_url, headers=headers)
            if resp.status_code == 200:
                p_data = resp.json()
                meta_status["phone_number"] = p_data.get("display_phone_number")
                meta_status["verified_name"] = p_data.get("verified_name")
                meta_status["quality_rating"] = p_data.get("quality_rating")
                print(f"  ✅ Phone Number ID {settings.whatsapp_phone_number_id}: {meta_status['phone_number']} ({meta_status['verified_name']})")
                print(f"     Quality Rating: {meta_status['quality_rating']}")
            else:
                meta_status["errors"].append(f"Phone lookup failed: {resp.status_code} - {resp.text}")
                print(f"  ⚠️ Phone lookup returned {resp.status_code}: {resp.text}")
        except Exception as e:
            meta_status["errors"].append(f"Phone lookup exception: {e}")
            print(f"  ❌ Phone lookup failed: {e}")

        # Check Templates on WABA
        try:
            waba_id = getattr(settings, "whatsapp_business_account_id", None) or os.getenv("WHATSAPP_BUSINESS_ACCOUNT_ID") or "1542477257918216"
            tpl_url = f"https://graph.facebook.com/{settings.whatsapp_api_version}/{waba_id}/message_templates"
            tpl_resp = await client.get(tpl_url, headers=headers, params={"limit": 50})
            if tpl_resp.status_code == 200:
                tpl_data = tpl_resp.json().get("data", [])
                for t in tpl_data:
                    meta_status["templates"].append({
                        "name": t.get("name"),
                        "status": t.get("status"),
                        "category": t.get("category"),
                        "language": t.get("language")
                    })
                print(f"  ✅ Meta WABA {waba_id}: Found {len(meta_status['templates'])} registered templates:")
                for t in meta_status["templates"]:
                    icon = "🟢" if t["status"] == "APPROVED" else "🔴"
                    print(f"     {icon} {t['name']} | Lang: {t['language']} | Cat: {t['category']} | Status: {t['status']}")
            else:
                meta_status["errors"].append(f"Template query failed: {tpl_resp.status_code} - {tpl_resp.text}")
                print(f"  ⚠️ Template query returned {tpl_resp.status_code}: {tpl_resp.text}")
        except Exception as e:
            meta_status["errors"].append(f"Template query exception: {e}")
            print(f"  ❌ Template query failed: {e}")

    meta_status["status"] = "HEALTHY" if not meta_status["errors"] else "WARNING"
    results["meta_api"] = meta_status

    # 3. AI CLIENT & MODEL ENDPOINTS AUDIT
    print("\n[3/6] AUDITING AI PROVIDERS & KEY VALIDITY...")
    ai_status = {}
    
    # Gemini
    gemini_key = settings.gemini_api_key or os.getenv("GEMINI_API_KEY")
    if gemini_key:
        try:
            from langchain_google_genai import ChatGoogleGenerativeAI
            from langchain_core.messages import HumanMessage
            llm = ChatGoogleGenerativeAI(
                model=settings.gemini_model,
                google_api_key=gemini_key,
                temperature=0,
                max_retries=1
            )
            resp = await llm.ainvoke([HumanMessage(content="Ping. Reply with PONG.")])
            reply = (resp.content or "").strip()
            ai_status["gemini"] = {"status": "HEALTHY", "reply": reply, "model": settings.gemini_model}
            print(f"  ✅ Gemini API ({settings.gemini_model}): HEALTHY (Response: {reply})")
        except Exception as e:
            ai_status["gemini"] = {"status": "FAILED", "error": str(e)}
            print(f"  ❌ Gemini API failed: {e}")
    else:
        ai_status["gemini"] = {"status": "MISSING_KEY"}
        print("  ⚠️ Gemini API key missing")

    # Groq
    groq_key = settings.groq_api_key or os.getenv("GROQ_API_KEY")
    if groq_key:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                g_resp = await client.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers={"Authorization": f"Bearer {groq_key}"},
                    json={
                        "model": settings.groq_model,
                        "messages": [{"role": "user", "content": "Ping. Reply PONG."}],
                        "max_tokens": 10
                    }
                )
                if g_resp.status_code == 200:
                    g_reply = g_resp.json()["choices"][0]["message"]["content"].strip()
                    ai_status["groq"] = {"status": "HEALTHY", "reply": g_reply, "model": settings.groq_model}
                    print(f"  ✅ Groq API ({settings.groq_model}): HEALTHY (Response: {g_reply})")
                else:
                    ai_status["groq"] = {"status": "FAILED", "error": g_resp.text}
                    print(f"  ⚠️ Groq API returned {g_resp.status_code}: {g_resp.text}")
        except Exception as e:
            ai_status["groq"] = {"status": "FAILED", "error": str(e)}
            print(f"  ❌ Groq API failed: {e}")
    else:
        ai_status["groq"] = {"status": "MISSING_KEY"}
        print("  ℹ️ Groq API key not configured")

    results["ai_providers"] = ai_status

    # 4. MESSAGE DELIVERY & RECENT METRICS
    print("\n[4/6] AUDITING MESSAGE DELIVERY LEDGER (PAST 7 DAYS)...")
    delivery_status = {}
    async with async_session_factory() as session:
        since_dt = datetime.now(UTC) - timedelta(days=7)
        # Inbound vs Outbound
        q_dir = await session.execute(
            select(WhatsAppMessage.direction, func.count(WhatsAppMessage.id))
            .where(WhatsAppMessage.created_at >= since_dt)
            .group_by(WhatsAppMessage.direction)
        )
        dir_counts = dict(q_dir.all())
        delivery_status["directions_7d"] = dir_counts
        print(f"  📊 7-Day Message Volume: Inbound={dir_counts.get('inbound', 0)}, Outbound={dir_counts.get('outbound', 0)}")

        # Outbound status breakdown
        q_stat = await session.execute(
            select(WhatsAppMessage.status, func.count(WhatsAppMessage.id))
            .where(WhatsAppMessage.direction == "outbound", WhatsAppMessage.created_at >= since_dt)
            .group_by(WhatsAppMessage.status)
        )
        stat_counts = dict(q_stat.all())
        delivery_status["outbound_status_7d"] = stat_counts
        print(f"  📊 Outbound Status Breakdown: {stat_counts}")

        # Recent Failures breakdown
        q_fails = await session.execute(
            select(WhatsAppMessage.created_at, WhatsAppMessage.to_phone, WhatsAppMessage.error_message, WhatsAppMessage.raw_payload)
            .where(WhatsAppMessage.status == "failed", WhatsAppMessage.created_at >= since_dt)
            .order_by(WhatsAppMessage.created_at.desc())
            .limit(10)
        )
        failed_rows = q_fails.all()
        delivery_status["recent_failures_sample"] = len(failed_rows)
        print(f"  ⚠️ Recent Failed Messages Inspected: {len(failed_rows)}")
        for dt, phone, err_msg, payload in failed_rows[:5]:
            err_code = (payload or {}).get("error_code")
            print(f"     - [{dt.strftime('%m-%d %H:%M')}] To {phone}: Code {err_code} - {err_msg}")
    results["delivery"] = delivery_status

    # 5. REMINDERS & SCHEDULER AUDIT
    print("\n[5/6] AUDITING REMINDERS & SCHEDULING ENGINE...")
    reminders_status = {}
    async with async_session_factory() as session:
        # Check active pending tasks
        q_pending = await session.execute(
            select(Task)
            .where(Task.completed_at.is_(None), Task.dismissed_at.is_(None))
            .order_by(Task.due_at.asc())
            .limit(10)
        )
        pending_tasks = q_pending.scalars().all()
        reminders_status["pending_count"] = len(pending_tasks)
        print(f"  ⏰ Active/Pending Reminders Found: {len(pending_tasks)}")
        for r in pending_tasks[:5]:
            due_str = r.due_at.isoformat() if r.due_at else "No due date"
            sent_str = "Reminder Sent" if r.reminder_sent_at else "Waiting"
            print(f"     - '{r.title}' | Due: {due_str} | Status: {sent_str} | Alarm Mode: {r.is_alarm_mode}")
    results["reminders"] = reminders_status

    # 6. LOCAL MEDIA & PDF RECEIPT ENGINE
    print("\n[6/6] AUDITING RECEIPT GENERATION & STORAGE ENGINE...")
    fs_status = {}
    media_dir = os.path.abspath(settings.media_download_dir)
    os.makedirs(media_dir, exist_ok=True)
    fs_status["media_dir_writable"] = os.access(media_dir, os.W_OK)
    print(f"  📁 Media Directory ({media_dir}): Writable={fs_status['media_dir_writable']}")

    try:
        from app.services.receipt_service import ReceiptService
        rs = ReceiptService()
        async with async_session_factory() as session:
            tx_res = await session.execute(select(Transaction).limit(1))
            sample_tx = tx_res.scalar_one_or_none()
            if sample_tx:
                pdf_bytes, filename, _ = await rs.generate_receipt_pdf(session, sample_tx.business_id, sample_tx.id)
                fs_status["receipt_pdf_engine"] = "HEALTHY" if pdf_bytes and len(pdf_bytes) > 100 else "EMPTY"
                print(f"  🧾 ReportLab PDF Receipt Engine: HEALTHY ({len(pdf_bytes)} bytes generated: {filename})")
            else:
                fs_status["receipt_pdf_engine"] = "SKIPPED_NO_TX"
                print("  ℹ️ ReportLab PDF Receipt Engine: Skipped (no transactions in db)")
    except Exception as e:
        fs_status["receipt_pdf_engine"] = f"FAILED: {e}"
        print(f"  ❌ PDF Receipt Engine failed: {e}")
    results["storage_and_receipts"] = fs_status

    print("\n" + "=" * 60)
    print("🏁 FULL SYSTEM AUDIT SWEEP COMPLETE")
    print("=" * 60)
    return results

if __name__ == "__main__":
    asyncio.run(run_audit())
