-- Productivity & Activity Module

create table tasks (
    id uuid primary key default gen_random_uuid(),
    business_id uuid not null references businesses(id) on delete cascade,
    title text not null,
    description text,
    due_at timestamptz,
    reminder_sent_at timestamptz,
    completed_at timestamptz,
    created_at timestamptz not null default now()
);

create table activities (
    id uuid primary key default gen_random_uuid(),
    business_id uuid not null references businesses(id) on delete cascade,
    category text not null,
    description text not null,
    occurred_at timestamptz not null default now(),
    created_at timestamptz not null default now()
);

create table magic_links (
    id uuid primary key default gen_random_uuid(),
    business_id uuid not null references businesses(id) on delete cascade,
    token text not null unique,
    expires_at timestamptz not null,
    is_used boolean not null default false,
    created_at timestamptz not null default now()
);

-- Indexes for performance
create index idx_tasks_business_id on tasks(business_id);
create index idx_tasks_due_at on tasks(due_at) where completed_at is null;
create index idx_activities_business_id on activities(business_id);
create index idx_activities_category on activities(category);
create index idx_magic_links_token on magic_links(token);

-- Enable RLS
alter table tasks enable row level security;
alter table activities enable row level security;
alter table magic_links enable row level security;
