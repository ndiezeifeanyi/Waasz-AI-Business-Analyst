import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.database import get_db_session
from sqlalchemy import select
from app.models.message import WhatsAppMessage

async def get_msgs():
    async for db in get_db_session():
        res = await db.execute(select(WhatsAppMessage).order_by(WhatsAppMessage.created_at.desc()).limit(20))
        msgs = res.scalars().all()
        for m in reversed(msgs):
            body_clean = (m.body or "").replace("\n", " ")[:100]
            print(f"[{m.created_at}] {m.direction} ({m.message_type}): {body_clean}")
        break

if __name__ == "__main__":
    asyncio.run(get_msgs())
