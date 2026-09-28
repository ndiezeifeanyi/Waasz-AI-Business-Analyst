from __future__ import annotations

import os
from pathlib import Path

import psycopg
from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parents[1]
MIGRATIONS_DIR = ROOT_DIR / "supabase" / "migrations"


def _database_url() -> str:
    load_dotenv(ROOT_DIR / ".env")
    url = os.getenv("SYNC_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("Set SYNC_DATABASE_URL or DATABASE_URL before running migrations.")
    return url.replace("postgresql+asyncpg://", "postgresql://")


def main() -> None:
    migrations = sorted(MIGRATIONS_DIR.glob("*.sql"))
    if not migrations:
        raise RuntimeError(f"No migrations found in {MIGRATIONS_DIR}")

    with psycopg.connect(_database_url(), autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                create table if not exists schema_migrations (
                    version text primary key,
                    applied_at timestamptz not null default now()
                )
                """
            )
            for migration in migrations:
                version = migration.name
                cur.execute("select 1 from schema_migrations where version = %s", (version,))
                if cur.fetchone():
                    print(f"Skipping {version}")
                    continue
                print(f"Applying {version}")
                cur.execute(migration.read_text(encoding="utf-8"))
                cur.execute("insert into schema_migrations(version) values (%s)", (version,))
    print("Migrations applied successfully.")


if __name__ == "__main__":
    main()
