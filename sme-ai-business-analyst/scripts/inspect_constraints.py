#!/usr/bin/env python3
import asyncio
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text
from app.core.database import async_session_factory


async def main():
    async with async_session_factory() as session:
        sql = """
        SELECT conname, pg_get_constraintdef(c.oid)
        FROM pg_constraint c
        JOIN pg_class t ON c.conrelid = t.oid
        WHERE t.relname = 'users';
        """
        rows = (await session.execute(text(sql))).fetchall()
        for r in rows:
            print(r)

if __name__ == "__main__":
    asyncio.run(main())
