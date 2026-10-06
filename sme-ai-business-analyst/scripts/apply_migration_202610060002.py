import asyncio
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.database import async_session_factory

async def main():
    migration_file = Path(__file__).resolve().parent.parent / "supabase" / "migrations" / "202610060002_fix_sync_transaction_activity_trigger.sql"
    sql = migration_file.read_text(encoding="utf-8")
    
    stmt1 = "ALTER TABLE user_activities ALTER COLUMN user_id DROP NOT NULL;"
    stmt2 = sql[sql.find("CREATE OR REPLACE FUNCTION"):]
    
    async with async_session_factory() as session:
        conn = await session.connection()
        raw_conn = await conn.get_raw_connection()
        await raw_conn.driver_connection.execute(stmt1)
        await raw_conn.driver_connection.execute(stmt2)
        await session.commit()
    print("Migration applied successfully!")

if __name__ == "__main__":
    asyncio.run(main())
