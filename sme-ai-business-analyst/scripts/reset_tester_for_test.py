#!/usr/bin/env python3
"""
Prepares +2347065015924 to be treated as a brand new, unapproved phone number:
1. Deletes it from approved_testers table.
2. Archives existing phone_number in users and businesses tables.
3. Re-exports users_table.csv and users_table.md.
"""
import asyncio
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text
from app.core.database import async_session_factory
from scripts.export_users_table import export as export_users


async def run():
    async with async_session_factory() as session:
        # 1. Delete from approved_testers
        r1 = await session.execute(
            text("DELETE FROM approved_testers WHERE phone_number LIKE '%7065015924%'")
        )
        print(f"Deleted {r1.rowcount} row(s) from approved_testers.")

        # 2. Archive in users
        r2 = await session.execute(
            text("UPDATE users SET phone_number = '9997065015924' WHERE phone_number = '2347065015924'")
        )
        print(f"Archived {r2.rowcount} row(s) in users.")

        # 3. Archive in businesses
        r3 = await session.execute(
            text("UPDATE businesses SET phone_number = '9997065015924' WHERE phone_number = '2347065015924'")
        )
        print(f"Archived {r3.rowcount} row(s) in businesses.")

        await session.commit()
        print("Database commit successful.")

    # Re-export users table for editor
    await export_users()
    print("Users table re-exported.")


if __name__ == "__main__":
    asyncio.run(run())
