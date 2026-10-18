import asyncio
from app.core.database import async_session_factory
from app.models.message import WhatsAppMessage
from app.models.transaction import Transaction
from app.models.confirmation import Confirmation
from sqlalchemy import select

async def main():
    async with async_session_factory() as session:
        print("=== RECENT MESSAGES ===")
        stmt = select(WhatsAppMessage).order_by(WhatsAppMessage.created_at.desc()).limit(20)
        res = await session.execute(stmt)
        msgs = res.scalars().all()
        for m in reversed(msgs):
            body_preview = (m.body or "").replace("\n", " ")[:120]
            print(f"[{m.created_at}] ({m.direction}) {body_preview}")

        print("\n=== RECENT CONFIRMATIONS ===")
        c_stmt = select(Confirmation).order_by(Confirmation.created_at.desc()).limit(10)
        c_res = await session.execute(c_stmt)
        for c in reversed(c_res.scalars().all()):
            print(f"[{c.created_at}] status={c.status} text={c.confirmation_text}")

        print("\n=== RECENT TRANSACTIONS ===")
        t_stmt = select(Transaction).order_by(Transaction.created_at.desc()).limit(15)
        t_res = await session.execute(t_stmt)
        for t in reversed(t_res.scalars().all()):
            print(f"[{t.created_at}] type={t.transaction_type} amt={t.amount} status={t.status} notes={t.notes}")

if __name__ == "__main__":
    asyncio.run(main())
