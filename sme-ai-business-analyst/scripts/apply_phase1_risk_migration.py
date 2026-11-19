import asyncio
import os
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)
sys.stdout.reconfigure(encoding='utf-8')

from app.core.database import async_session_factory, engine, Base
from app.models.risk_assessment import TransactionRiskAssessment
from app.models.cash_reconciliation import CashRestockReconciliation

async def run_migration():
    print("🚀 Connecting to live database to create Phase 1 Risk & Cash Reconciliation tables...")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all, tables=[
            TransactionRiskAssessment.__table__,
            CashRestockReconciliation.__table__,
        ])
    print("✅ Tables 'transaction_risk_assessments' and 'cash_restock_reconciliations' successfully verified/created in database!")

if __name__ == "__main__":
    asyncio.run(run_migration())
