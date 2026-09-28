# Multi-Niche WhatsApp Assistant Demo Script

This script walks through demonstrating the generalized multi-tenant assistant across both SME retail and 9-to-5 employee personas.

---

## 1. Start the Backend API

```powershell
.\.venv\Scripts\python -m uvicorn app.main:app --reload
```

---

## 2. Scenario A: SME Business Owner Demo

### Step 1: Record a Sale (Ledger Flow)
```powershell
.\.venv\Scripts\python scripts/demo_webhook.py --phone "+2348011112222" --text "Sold 5 bags of rice for 225,000"
```
**Expected Response**:
```text
I understood: Sale: 5 bags rice for ₦225,000. Reply 1=Yes, 2=Edit.
```
Reply `1` to confirm. The transaction is committed and automatically mirrored into `user_activities` via the database trigger.

### Step 2: Ingest Shared Business Knowledge
```powershell
.\.venv\Scripts\python scripts/demo_webhook.py --phone "+2348011112222" --text "price list: Rice 50kg bag 45,000 NGN, Sugar 50kg 48,000 NGN, Indomie carton 7,000 NGN"
```
**Expected Response**:
```text
📚 Saved to your private knowledge base: 'Rice 50kg bag 45,000 NGN...'
```

### Step 3: Track Business Target
```powershell
.\.venv\Scripts\python scripts/demo_webhook.py --phone "+2348011112222" --text "my goal is achieve 2,000,000 NGN sales this month"
```
**Expected Response**:
```text
🎯 Goal recorded: 'achieve 2,000,000 NGN sales this month'. I will track your progress and include it in your roadmaps!
```

---

## 3. Scenario B: 9-to-5 Corporate Employee Demo

### Step 1: Ingest Career Project Note
```powershell
.\.venv\Scripts\python scripts/demo_webhook.py --phone "+2348033334444" --text "save note: Led cross-functional sprint demo on payment microservice architecture"
```
**Expected Response**:
```text
📚 Saved to your private knowledge base: 'Led cross-functional sprint demo...'
```

### Step 2: Track Career Milestone
```powershell
.\.venv\Scripts\python scripts/demo_webhook.py --phone "+2348033334444" --text "my goal is get promoted to Lead Product Manager by Q4"
```
**Expected Response**:
```text
🎯 Goal recorded: 'get promoted to Lead Product Manager by Q4'. I will track your progress and include it in your roadmaps!
```

### Step 3: Set Work Task Reminder
```powershell
.\.venv\Scripts\python scripts/demo_webhook.py --phone "+2348033334444" --text "Remind me tomorrow at 9am to submit quarterly performance appraisal"
```
**Expected Response**:
```text
Got it — I'll remind you on tomorrow at 9:00 AM for 'submit quarterly performance appraisal'.
```

### Step 4: Persona-Adapted Q&A
```powershell
.\.venv\Scripts\python scripts/demo_webhook.py --phone "+2348033334444" --text "What should I highlight in my promotion review with my manager?"
```
**Expected Response**:
```text
(Adapted by Executive Career Coach persona using saved notes and Q4 promotion goal)
```

---

## 4. Privacy & Forget-Me Flow

```powershell
.\.venv\Scripts\python scripts/demo_webhook.py --phone "+2348033334444" --text "forget me"
```
**Expected Response**:
```text
⚠️ Are you sure you want to permanently delete all your data, memory, notes, and records? Reply CONFIRM DELETE to proceed.
```

Reply:
```powershell
.\.venv\Scripts\python scripts/demo_webhook.py --phone "+2348033334444" --text "CONFIRM DELETE"
```
**Expected Response**:
```text
✅ All your personal records, conversation memory, and uploaded documents have been permanently deleted.
```
