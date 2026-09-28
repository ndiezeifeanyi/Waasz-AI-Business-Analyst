# Deployment & Operational Architecture

## Official Production Target: Google Cloud Run (Single-Process Container)

The platform is designed and optimized to run as a **single, unified container process** using the root `Dockerfile`.

Starting this single container brings up:
1. **The FastAPI Web Application**: Handles inbound WhatsApp webhooks, authentication, and health checks.
2. **All Request-Triggered Services**: Transaction extraction, interactive confirmation flows, vector memory RAG, document knowledge base, voice transcription, multimodal vision image understanding, dynamic AI cost monitoring, and group policy filtering.
3. **All Background & Scheduled Jobs (`ReportScheduler`)**:
   - **Task & Reminder Dispatch**: Every 30 seconds (including alarm mode interactive repeat alerts).
   - **Automated Knowledge Re-embedding**: Hourly automatic re-embedding of any chunks that temporarily fell back to local hash embeddings.
   - **Conversation Compaction**: Every 15 minutes for idle user dialogues.
   - **Data Retention Purge**: Daily at 03:00 UTC (purging expired message records and media).
   - **Dynamic Model Discovery Refresh**: Every 24 hours with live provider self-healing.
   - **Unified Reports (All Niches)**:
     - Daily reports at configured local hour (default: 20:00).
     - Weekly reports on Mondays (default: 20:00).
     - Monthly reports on the 1st of each month (default: 20:00).

**No secondary processes, external worker dynos, or manual cron steps are required.**

---

## One-Time Manual Setup Steps (Inherent to Deployment)

Only three genuine one-time setup steps are required when deploying a fresh environment:

1. **Configure Environment Secrets (Google Cloud Secret Manager / Env Vars)**:
   ```env
   APP_ENV=production
   APP_DEBUG=false
   APP_BASE_URL=https://your-service-url.run.app
   SECRET_KEY=<secure-random-string>
   DATABASE_URL=postgresql+asyncpg://user:pass@host:5432/dbname
   SYNC_DATABASE_URL=postgresql://user:pass@host:5432/dbname
   WHATSAPP_VERIFY_TOKEN=<your-custom-verify-token>
   WHATSAPP_APP_SECRET=<meta-app-secret>
   WHATSAPP_ACCESS_TOKEN=<meta-system-user-access-token>
   WHATSAPP_PHONE_NUMBER_ID=<meta-phone-number-id>
   GEMINI_API_KEY=<google-gemini-key>
   GROQ_API_KEY=<groq-key>
   OPENAI_API_KEY=<openai-key>
   DAILY_AI_SPEND_LIMIT_USD=10
   ```

2. **Run Initial Database Migrations**:
   Run the migration script once against your PostgreSQL instance:
   ```bash
   python scripts/apply_migrations.py
   ```

3. **Configure Meta WhatsApp Webhook**:
   In Meta App Dashboard (WhatsApp > Configuration):
   - **Callback URL**: `https://<your-service-url.run.app>/api/webhook/whatsapp`
   - **Verify Token**: Same value set in `WHATSAPP_VERIFY_TOKEN`.
   - **Webhook Fields**: Subscribe to `messages`.

---

## Verification & Health Check Endpoints

Once the container boots, verify the entire subsystem state using the single comprehensive health endpoint:

```bash
# Complete Single-Process Audit (DB, Scheduler, All 8 Jobs, Live Models, Credentials)
curl -s https://<your-service-url.run.app>/api/health/system

# Standard Liveness & Readiness Probes
curl -s https://<your-service-url.run.app>/api/health
curl -s https://<your-service-url.run.app>/api/ready
```

---

## Auxiliary Internal Service: Streamlit Admin Dashboard

The admin dashboard (`admin/streamlit_app.py`) is an optional, separate internal web UI for human operators and business analysts. It is **not** required for the backend API, scheduler, or WhatsApp bot to operate. If desired, deploy it as an isolated internal service behind your company's SSO or VPN:

```bash
streamlit run admin/streamlit_app.py --server.port 8501
```

---

## Unmaintained Legacy Deployment Configurations

The repository retains configuration files for alternative platforms for historical reference:
- `fly.toml` (Fly.io)
- `railway.json` (Railway)
- `render.yaml` (Render)

These platforms are **unmaintained alternatives**. Google Cloud Run via `Dockerfile` is the official and verified production deployment path.
