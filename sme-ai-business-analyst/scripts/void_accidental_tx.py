import asyncio
import sys
from pathlib import Path

# Add project root
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.database import get_db_session
from sqlalchemy import select
from app.models.transaction import Transaction

async def void_tx():
    async for db in get_db_session():
        res = await db.execute(
            select(Transaction).where(
                Transaction.item_name == "ends and socket",
                Transaction.amount == 30000,
            )
        )
        txs = res.scalars().all()
        for t in txs:
            t.status = "void"
            print(f"Voided accidental transaction: {t.id} - {t.item_name} (₦{t.amount})")
        await db.commit()
        break

if __name__ == "__main__":
    asyncio.run(void_tx())
