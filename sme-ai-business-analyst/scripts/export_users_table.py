#!/usr/bin/env python3
"""
Exports the live PostgreSQL `users` table into users_table.csv and users_table.md
for direct viewing and inspection in the editor.
"""
import asyncio
import csv
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select
import os
import psycopg
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")


def _get_db_url() -> str:
    url = os.getenv("SYNC_DATABASE_URL") or os.getenv("DATABASE_URL", "")
    return url.replace("postgresql+asyncpg://", "postgresql://")


def export():
    """Synchronous export of users table using psycopg."""
    url = _get_db_url()
    if not url:
        return

    root = Path(__file__).resolve().parent.parent
    csv_path = root / "users_table.csv"
    md_path = root / "users_table.md"

    try:
        with psycopg.connect(url) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT id::text, phone_number, coalesce(display_name, ''), role, coalesce(niche, ''), is_active, created_at, last_inbound_at, coalesce(business_id::text, '')
                    FROM users
                    ORDER BY created_at ASC
                """)
                users = cur.fetchall()

        # 1. Export CSV
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([
                "id", "phone_number", "display_name", "role", "niche", "is_active", "created_at", "last_inbound_at", "business_id"
            ])
            for u in users:
                writer.writerow([
                    u[0], u[1], u[2], u[3], u[4], u[5],
                    u[6].isoformat() if u[6] else "",
                    u[7].isoformat() if u[7] else "",
                    u[8]
                ])

        # 2. Export Markdown Table
        with open(md_path, "w", encoding="utf-8") as f:
            f.write("# Live Database Table: `users`\n\n")
            f.write(f"**Total Registered Users**: {len(users)}\n\n")
            f.write("| # | Phone Number | Display Name | Role | Niche | Active | Created At | User ID |\n")
            f.write("|---|---|---|---|---|---|---|---|\n")
            for i, u in enumerate(users, 1):
                name = u[2] if u[2] else "-"
                created = u[6].strftime("%Y-%m-%d %H:%M:%S UTC") if u[6] else "-"
                f.write(f"| {i} | `{u[1]}` | {name} | {u[3]} | {u[4]} | {u[5]} | {created} | `{u[0]}` |\n")

        print(f"Exported {len(users)} users successfully to {csv_path.name} and {md_path.name}")
    except Exception as e:
        print(f"Error exporting users: {e}")


async def export_async():
    export()


if __name__ == "__main__":
    export()
