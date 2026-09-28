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
from app.core.database import async_session_factory
from app.models.user import User


async def export():
    async with async_session_factory() as session:
        users = (await session.execute(select(User).order_by(User.created_at.asc()))).scalars().all()

        root = Path(__file__).resolve().parent.parent
        csv_path = root / "users_table.csv"
        md_path = root / "users_table.md"

        # 1. Export CSV
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([
                "id",
                "phone_number",
                "display_name",
                "role",
                "niche",
                "is_active",
                "created_at",
                "last_inbound_at",
                "business_id",
            ])
            for u in users:
                role_val = getattr(u.role, "value", str(u.role))
                writer.writerow([
                    str(u.id),
                    u.phone_number,
                    u.display_name or "",
                    role_val,
                    u.niche or "",
                    u.is_active,
                    u.created_at.isoformat() if u.created_at else "",
                    u.last_inbound_at.isoformat() if u.last_inbound_at else "",
                    str(u.business_id) if u.business_id else "",
                ])

        # 2. Export Markdown Table
        with open(md_path, "w", encoding="utf-8") as f:
            f.write("# Live Database Table: `users`\n\n")
            f.write(f"**Total Registered Users**: {len(users)}\n\n")
            f.write("| # | Phone Number | Display Name | Role | Niche | Active | Created At | User ID |\n")
            f.write("|---|---|---|---|---|---|---|---|\n")
            for i, u in enumerate(users, 1):
                name = u.display_name if u.display_name else "-"
                role_val = getattr(u.role, "value", str(u.role))
                created = u.created_at.strftime("%Y-%m-%d %H:%M:%S UTC") if u.created_at else "-"
                f.write(f"| {i} | `{u.phone_number}` | {name} | {role_val} | {u.niche} | {u.is_active} | {created} | `{u.id}` |\n")

        print(f"Exported {len(users)} users successfully to {csv_path.name} and {md_path.name}")


if __name__ == "__main__":
    asyncio.run(export())
