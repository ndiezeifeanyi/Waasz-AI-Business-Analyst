# Generalized Multi-Tenant WhatsApp Assistant Platform

A production-grade, multi-tenant AI assistant operating over WhatsApp Cloud API, serving multiple personas:
- **SME Business Owners**: Sales/expense ledger, inventory movements, shared price lists, and business revenue roadmaps.
- **9-to-5 Corporate Employees**: Project milestones, performance reviews, career goals, and task reminders.
- **Freelancers & Consultants**: Client deliverables, hourly notes, invoice tracking, and contract milestones.
- **Students & Academics**: Study schedules, coursework notes, assignment deadlines, and exam goals.
- **Personal Productivity**: Daily reflections, habit goals, family reminders, and private knowledge notes.

> **Defense-in-Depth Security**: Tenant isolation is strictly enforced via parameterized SQL queries and verified in CI with negative isolation test suites. All data is scoped by `user_id` and, where enabled, `business_id` with `'private'` vs `'business_shared'` visibility controls.

## Key Capabilities

- 📱 **Omnichannel WhatsApp Engine**: Text, voice notes, and receipt OCR with 24-hour customer care window compliance and Meta utility template failover.
- 🧠 **Two-Tier Memory & Knowledge System**: Rolling short-term conversational buffer + durable semantic vector facts (`pgvector` 768-dim) with 15-minute idle compaction.
- 🎯 **Goal Tracking & Roadmap Engine**: Adaptive periodic progress reports (weekly/monthly) and suggestions tailored to user niche personas.
- 🔒 **Privacy & Right-to-be-Forgotten**: Instant atomic erasure of user activities, documents, embeddings, and conversation histories via `"forget me"` verification.
- 👥 **Group Chat Ready (Meta OBA Gated)**: Ready-to-enable group messaging module gated strictly on Meta Official Business Account verification and mention-only triggers.
- 💰 **Dynamic Cost Governance**: Hard $0.50/day per-user limit with dynamic systemic ceiling (`max(10.0, active_users * 0.50 * 1.25)`) and 80% soft alert. Unit cost < $0.02/user/month.
- 🔐 **Production Security**: JWT RBAC, HMAC-SHA256 webhook signatures, prompt injection detection, and input sanitization.

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    WhatsApp Business                         │
│                   (Incoming Messages)                        │
└──────────────────────┬──────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────┐
│              FastAPI Backend (Production-Ready)              │
│  ├─ Security Middleware (Rate limiting, Headers, Sanitization)
│  ├─ Webhook Signature Verification (HMAC-SHA256)
│  ├─ Rate Limiting (Per-IP, Endpoint-specific)
│  └─ Error Handling (No stack traces in production)
└──────────────┬──────────────────────────────────────────────┘
               │
      ┌────────┴────────┐
      ▼                 ▼
┌──────────────┐  ┌──────────────────┐
│ AI Extraction│  │ Input Validation │
│ (LangChain)  │  │ & Sanitization   │
├─ Gemini 1.5F├  ├─ Prompt Injection│
├─ Groq 70B   │  │  Detection       │
├─ OpenAI     │  ├─ Phone validation│
└─────┬────────┘  └──────────────────┘
      │
      ▼
┌──────────────────────────────────────┐
│   PostgreSQL Database (Supabase)     │
│  ├─ Connection pooling (20 pools)    │
│  ├─ SSL/TLS (production)             │
│  └─ Row-level security (future)      │
└──────────────────────────────────────┘
      │
      ▼
┌──────────────────────────────────────┐
│  Report Generation & Confirmation    │
│  ├─ Daily/Weekly reports             │
│  └─ One-tap confirmation workflow    │
└──────────────────────────────────────┘
```

## Prerequisites

- **Python 3.12+**
- **PostgreSQL 13+** (or Supabase)
- **Meta WhatsApp Business Account**
- **At least one AI provider** (Gemini, Groq, or OpenAI)

## Local Setup (Windows)

### 1. Clone and Environment Setup

```powershell
cd "C:\Users\NDIEZE\Desktop\Whatsapp SME test run 1\sme-ai-business-analyst"
py -3.12 -m venv .venv
.\.venv\Scripts\python -m pip install --upgrade pip setuptools wheel
.\.venv\Scripts\python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

### 2. Configure Environment

Edit `.env` and set these **critical** variables:

```env
# Security
APP_ENV=local
SECRET_KEY=                          # Required! Generate with: python -c "import secrets; print(secrets.token_urlsafe(32))"
WHATSAPP_VERIFY_TOKEN=your_token    # Create a random string

# Database
DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/sme_ai
SYNC_DATABASE_URL=postgresql://user:pass@localhost:5432/sme_ai

# AI Provider (choose at least one)
GEMINI_API_KEY=                     # From https://aistudio.google.com/app/apikey
GROQ_API_KEY=                       # From https://console.groq.com/keys
OPENAI_API_KEY=                     # From https://platform.openai.com/api-keys
```

### 3. Database Setup

Create PostgreSQL database:
```bash
createdb sme_ai
```

Apply migrations:
```powershell
.\.venv\Scripts\python scripts/apply_migrations.py
```

Optional: Load demo data
```bash
psql sme_ai -f supabase/seed.sql
```

### 4. Startup Verification

Before running, verify configuration:
```powershell
.\.venv\Scripts\python scripts/verify_startup.py
```

This checks:
- ✓ Environment configuration
- ✓ Secret key setup
- ✓ Database connectivity
- ✓ AI provider configuration
- ✓ WhatsApp setup (if production)

### 5. Start Application

```powershell
.\.venv\Scripts\python -m uvicorn app.main:app --reload
```

Application runs on `http://localhost:8080`

### 6. Health Checks

```powershell
# Application is running
curl http://localhost:8080/health

# Application ready to handle requests (checks database)
curl http://localhost:8080/ready

# API documentation (development only)
curl http://localhost:8080/docs
```

### 7. Founder Dashboard

```powershell
.\.venv\Scripts\python -m streamlit run admin/streamlit_app.py
```

Dashboard available at `http://localhost:8501`

## Production Setup

### 1. Environment Configuration

Create `.env` for production with:

```env
APP_ENV=production
APP_DEBUG=false
SECRET_KEY=<generate-32-char-random-key>
WHATSAPP_VERIFY_TOKEN=<random-token>
WHATSAPP_APP_SECRET=<from-meta>
WHATSAPP_ACCESS_TOKEN=<from-meta>
WHATSAPP_PHONE_NUMBER_ID=<from-meta>
DATABASE_URL=<your-production-database-async-url>
SYNC_DATABASE_URL=<your-production-database-sync-url>
GEMINI_API_KEY=<your-key>
```

### 2. Database (Recommended: Supabase)

1. Create Supabase project
2. Copy connection strings
3. Update `DATABASE_URL` and `SYNC_DATABASE_URL`
4. Run migrations
5. Configure backups

### 3. WhatsApp Setup

1. Create Meta Business App
2. Add WhatsApp product
3. Get credentials (App Secret, Access Token, Phone Number ID)
4. Set webhook URL: `https://your-domain.com/webhooks/whatsapp`
5. Configure webhook token
6. Subscribe to `messages` and `message_status` events

### 4. Deployment (Railway Recommended)

```powershell
# Install Railway CLI
npm install -g @railway/cli

# Login
railway login

# Deploy
railway up
```

Or use Render/Fly.io - see [deployment.md](docs/deployment.md)

## Deployment Security Checklist

Before going live, complete the [DEPLOYMENT_SECURITY_CHECKLIST.md](docs/DEPLOYMENT_SECURITY_CHECKLIST.md):

- [ ] All environment variables configured
- [ ] `APP_DEBUG=false` in production
- [ ] `SECRET_KEY` changed from default
- [ ] Database backups enabled
- [ ] SSL/TLS certificate valid
- [ ] Rate limiting tested
- [ ] Error handling verified (no stack traces exposed)
- [ ] Logging configured (secrets masked)
- [ ] Monitoring and alerts set up
- [ ] Disaster recovery tested

## Security

### Security Architecture

This platform implements **defense-in-depth** security:

1. **API Security**
   - 🔒 JWT authentication with Argon2 password hashing
   - 🛡️ Rate limiting (30 req/min per IP, per-endpoint limits)
   - 📋 CORS with explicit origin whitelist
   - 🚨 Security headers (CSP, HSTS, X-Frame-Options)

2. **WhatsApp Webhook**
   - ✅ HMAC-SHA256 signature verification (fail-secure)
   - 🔄 Idempotency with deduplication
   - 📊 Replay attack prevention

3. **Input Validation**
   - 🚫 Prompt injection detection
   - 🧹 SQL injection prevention (parameterized queries)
   - ✂️ Text sanitization (null bytes, control chars)

4. **Data Protection**
   - 💾 Database connection pooling
   - 🔐 SSL/TLS for production
   - 🔍 Audit logging of all access

5. **Error Handling**
   - 🔒 No stack traces exposed in production
   - 🪵 Secrets masked in logs
   - 📝 Structured error logging

### Security Documentation

- [SECURITY.md](docs/SECURITY.md) - Comprehensive security architecture
- [DEPLOYMENT_SECURITY_CHECKLIST.md](docs/DEPLOYMENT_SECURITY_CHECKLIST.md) - Pre-deployment checklist

## Testing

### Unit & Integration Tests

```powershell
# Run all tests
.\.venv\Scripts\python -m pytest -v

# Run security tests only
.\.venv\Scripts\python -m pytest tests/test_security.py -v

# Run with coverage
.\.venv\Scripts\python -m pytest --cov=app tests/
```

### Code Quality

```powershell
# Lint with ruff
.\.venv\Scripts\python -m ruff check .

# Format with ruff
.\.venv\Scripts\python -m ruff format .
```

### Manual Testing

```powershell
# Send test webhook
.\.venv\Scripts\python scripts/demo_webhook.py --text "Sold 5 bags rice for 250000"

# Check demo script for more examples
cat scripts/demo_webhook.py
```

## API Endpoints

### Public (No Auth Required)

```
GET  /health              - Application health check
GET  /ready               - Readiness check (includes DB)
GET  /webhooks/whatsapp   - WhatsApp webhook verification
POST /webhooks/whatsapp   - WhatsApp webhook message ingestion
```

### Admin (JWT Auth Required, Admin Role)

```
GET  /admin/status        - Admin API status
GET  /admin/metrics       - Founder dashboard metrics
POST /admin/businesses/{id}/reports/daily   - Generate daily report
POST /admin/businesses/{id}/reports/weekly  - Generate weekly report
```

## Configuration

All configuration via environment variables (see `.env.example`):

- **APP_ENV**: local, test, staging, production
- **SECRET_KEY**: Required for JWT signing
- **DATABASE_URL**: PostgreSQL async connection string
- **WHATSAPP_***: Meta webhook credentials
- **GEMINI/GROQ/OPENAI_API_KEY**: AI provider keys
- **RATE_LIMIT_PER_MINUTE**: Requests per minute (default: 30)
- **DAILY_AI_SPEND_LIMIT_USD**: Cost limit per business (default: $5)

See `.env.example` for full configuration reference.

## Production Deployment (Single-Process Architecture)

The application runs as a **single, consolidated FastAPI process** inside a Docker container on **Google Cloud Run**. Starting this one process brings up all request-triggered handlers, the dynamic model resolver, and all 8 background scheduled jobs (reminders, reports, idle compaction, retention purge, re-embedding). No secondary worker processes or dynos are required.

See [docs/deployment.md](docs/deployment.md) for complete production setup instructions, including Secret Manager integration, database migrations, and health verification.

### Running Locally

```powershell
# Start the single consolidated FastAPI process (API + all schedulers)
.\.venv\Scripts\python -m uvicorn app.main:app --host 0.0.0.0 --port 8000

# Verify the full system health on demand
curl http://127.0.0.1:8000/health/system
```

### Optional Admin UI
The Streamlit dashboard (`admin_dashboard.py`) is an optional web UI for team/admin operations and runs separately:
```powershell
.\.venv\Scripts\streamlit run admin_dashboard.py
```

### Legacy Deployment Configs
`railway.json`, `render.yaml`, and `fly.toml` are preserved as unmaintained legacy alternatives. The primary supported container is built from `Dockerfile`.

## Monitoring

### Health Endpoints

```bash
# Basic liveness ping
curl https://your-domain/health

# Readiness check (DB connectivity)
curl https://your-domain/ready

# Comprehensive system self-check (DB + all 8 scheduled jobs + AI models + credentials)
curl https://your-domain/health/system

# Background scheduler inspect
curl https://your-domain/health/scheduler
```

### Logs

```bash
# Application logs (stdout)
docker logs <container-id>

# Check recent errors
grep ERROR app.log | tail -20
```

### Metrics

Founder dashboard available at `/admin/metrics` (requires authentication)

## Known Limitations (Phase 1)

- ❌ No end-to-end encryption (uses TLS only)
- ❌ No customer mobile app (WhatsApp only)
- ❌ No forecasting/credit features
- ❌ No multi-language support yet
- ❌ No SMS fallback (WhatsApp-only)
- ❌ Simplified AI confidence (future: probabilistic models)

## Roadmap

- **Phase 2**: Multi-language support, SMS fallback, customer dashboard
- **Phase 3**: Forecasting, credit scoring, inventory optimization
- **Phase 4**: Mobile app, advanced analytics, export to accounting software

## Contributing

All PRs must:
- Pass security tests: `pytest tests/test_security.py`
- Pass lint: `ruff check .`
- Include docstrings
- Have security implications documented

## License

MIT - See LICENSE file

## Support

- 📖 Documentation: See `/docs` folder
- 🐛 Issues: GitHub Issues
- 💬 Questions: GitHub Discussions

## Security Reporting

🚨 **Found a security issue?**

Please email security@example.com with:
- Description of vulnerability
- Steps to reproduce
- Potential impact

Do NOT create public GitHub issues for security vulnerabilities.

---

**Built with ❤️ for African SMEs using FastAPI, LangChain, and PostgreSQL**

