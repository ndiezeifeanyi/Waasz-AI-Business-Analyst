import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.database import get_db_session
from sqlalchemy import select, update
from app.models.transaction import Transaction
from app.models.confirmation import Confirmation

async def inspect_and_void():
    async for db in get_db_session():
        # Find confirmations created in the last 24 hours
        c_res = await db.execute(
            select(Confirmation)
            .order_by(Confirmation.created_at.desc())
            .limit(10)
        )
        confs = c_res.scalars().all()
        for c in confs:
            rec = c.extracted_record or {}
            items = rec.get("items") or []
            print(f"CONF: {c.id} | status={c.status} | items={len(items)} | total={rec.get('amount')} | type={rec.get('record_type')} | created={c.created_at}")

        # Find recent transactions created this morning
        t_res = await db.execute(
            select(Transaction)
            .order_by(Transaction.created_at.desc())
            .limit(50)
        )
        txs = t_res.scalars().all()
        print(f"\nTotal recent transactions: {len(txs)}")
        
        # Check if there are confirmed expenses from this morning (batch of 28 totaling ~821k)
        morning_expenses = [
            t for t in txs 
            if t.status == "confirmed" and t.transaction_type == "expense" and t.created_at.hour in (5, 6, 7)
        ]
        print(f"Morning confirmed expenses count: {len(morning_expenses)}")
        for t in morning_expenses:
            t.status = "void"
            print(f"Voided expense: {t.id} - {t.item_name} (₦{t.amount})")

        await db.commit()
        print("Database commit completed.")
        break

if __name__ == "__main__":
    asyncio.run(inspect_and_void())
