# Supabase Setup

1. Create a Supabase project and copy the PostgreSQL connection string.
2. Copy `.env.example` to `.env`.
3. Set both database variables:

```env
DATABASE_URL=postgresql+asyncpg://postgres:<password>@<host>:5432/postgres
SYNC_DATABASE_URL=postgresql://postgres:<password>@<host>:5432/postgres
```

4. Apply migrations:

```bash
python scripts/apply_migrations.py
```

5. Optional local/demo seed:

```bash
psql "$SYNC_DATABASE_URL" -f supabase/seed.sql
```

The schema enables row-level security and only grants broad API access to Supabase service-role requests. The backend should use the direct PostgreSQL connection string or a service-role context, never an anon key, for operational writes.
