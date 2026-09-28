#!/usr/bin/env python3
"""
Merge duplicate identity for +2347065015924:
1. Canonical Account:
   - Business ID: 89500a70-4376-41ef-828b-6a841ed70ded (holds all 8 transactions, 411 messages, etc.)
   - User ID:     9d1d24e3-b678-4345-a656-a16eb8d3ef6d
2. Duplicate Account:
   - Business ID: 40c61108-95a2-4fac-b313-d5be9296f755
   - User ID:     f819ae53-2abe-42ba-9eb5-d338feb1fdf3
3. Action:
   - Reattach all foreign keys pointing to duplicate business/user to canonical IDs.
   - Delete duplicate user and business rows.
   - Restore phone_number = '2347065015924' on canonical user and business rows.
   - Re-export users_table.md and users_table.csv.
"""
import asyncio
from pathlib import Path
import sys
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text
from app.core.database import async_session_factory
from scripts.export_users_table import export as export_users

CANONICAL_B_ID = UUID("89500a70-4376-41ef-828b-6a841ed70ded")
CANONICAL_U_ID = UUID("9d1d24e3-b678-4345-a656-a16eb8d3ef6d")
DUP_B_ID = UUID("40c61108-95a2-4fac-b313-d5be9296f755")
DUP_U_ID = UUID("f819ae53-2abe-42ba-9eb5-d338feb1fdf3")
PHONE = "2347065015924"


async def merge():
    async with async_session_factory() as s:
        print("Starting identity merge for phone:", PHONE)

        # 1. Update foreign key tables pointing to DUP_B_ID
        b_tables = [
            "whatsapp_messages", "media_assets", "ai_extractions", "confirmations",
            "transactions", "inventory_items", "inventory_movements", "corrections",
            "business_reports", "ai_cost_events", "audit_logs", "tasks", "activities",
            "magic_links", "user_goals", "user_activities", "user_knowledge_documents",
            "user_knowledge_chunks", "conversation_memories"
        ]
        for tbl in b_tables:
            r = await s.execute(
                text(f"UPDATE {tbl} SET business_id = :canon WHERE business_id = :dup"),
                {"canon": CANONICAL_B_ID, "dup": DUP_B_ID}
            )
            if r.rowcount > 0:
                print(f"  Reattached {r.rowcount} row(s) in {tbl} from duplicate business to canonical.")

        # 2. Update foreign key tables pointing to DUP_U_ID
        u_tables = [
            ("whatsapp_messages", "user_id"),
            ("audit_logs", "actor_user_id"),
            ("tasks", "user_id"),
            ("user_goals", "user_id"),
            ("user_activities", "user_id"),
            ("user_knowledge_documents", "user_id"),
            ("user_knowledge_chunks", "user_id"),
            ("conversation_memories", "user_id"),
            ("invite_codes", "created_by_user_id"),
        ]
        for tbl, col in u_tables:
            r = await s.execute(
                text(f"UPDATE {tbl} SET {col} = :canon WHERE {col} = :dup"),
                {"canon": CANONICAL_U_ID, "dup": DUP_U_ID}
            )
            if r.rowcount > 0:
                print(f"  Reattached {r.rowcount} row(s) in {tbl}.{col} from duplicate user to canonical.")

        # 3. Delete duplicate user row
        r_del_u = await s.execute(
            text("DELETE FROM users WHERE id = :dup_u"),
            {"dup_u": DUP_U_ID}
        )
        print(f"  Deleted duplicate user row ({r_del_u.rowcount} row).")

        # 4. Delete duplicate business row
        r_del_b = await s.execute(
            text("DELETE FROM businesses WHERE id = :dup_b"),
            {"dup_b": DUP_B_ID}
        )
        print(f"  Deleted duplicate business row ({r_del_b.rowcount} row).")

        # 5. Restore phone_number on canonical user and business rows
        await s.execute(
            text("UPDATE users SET phone_number = :phone WHERE id = :canon_u"),
            {"phone": PHONE, "canon_u": CANONICAL_U_ID}
        )
        await s.execute(
            text("UPDATE businesses SET phone_number = :phone WHERE id = :canon_b"),
            {"phone": PHONE, "canon_b": CANONICAL_B_ID}
        )
        print(f"  Restored phone_number = '{PHONE}' on canonical account.")

        await s.commit()
        print("Identity merge committed successfully!")

    # Re-export users table for markdown and csv view
    await export_users()
    print("Users table re-exported.")


if __name__ == "__main__":
    asyncio.run(merge())
