import asyncio
from app.core.database import async_session_factory
from app.models.message import WhatsAppMessage
from sqlalchemy import select

async def main():
    async with async_session_factory() as session:
        stmt = (
            select(WhatsAppMessage)
            .order_by(WhatsAppMessage.created_at.desc())
            .limit(10)
        )
        res = await session.execute(stmt)
        msgs = res.scalars().all()
        for m in reversed(msgs):
            print("=" * 80)
            print(f"[{m.created_at}] DIRECTION: {m.direction} | TYPE: {m.message_type}")
            print(f"FROM: {m.from_phone} -> TO: {m.to_phone}")
            print(f"BODY:\n{m.body}")
            print("=" * 80)

if __name__ == "__main__":
    asyncio.run(main())
