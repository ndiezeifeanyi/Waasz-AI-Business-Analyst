-- Migration: 202611050001_debt_recovery_system.sql
-- Description: Automated, consent-based IOU delivery, reminder and recovery data model.

begin;

-- 1. Debtor Consents (attested at counter by merchant or confirmed by debtor)
create table if not exists debtor_consents (
    id uuid primary key default gen_random_uuid(),
    business_id uuid not null references businesses(id) on delete cascade,
    debt_id uuid not null references debts(id) on delete cascade,
    debtor_phone varchar(32) not null,
    source varchar(32) not null default 'attested_by_merchant',
    wording_version varchar(32) not null default 'counter_v1',
    attested_by_member_id uuid references members(id) on delete set null,
    created_at timestamptz not null default now(),
    revoked_at timestamptz,
    constraint ck_debtor_consents_source check (source in ('attested_by_merchant', 'confirmed_by_debtor'))
);

create index if not exists idx_debtor_consents_business_debt
    on debtor_consents(business_id, debt_id);
create index if not exists idx_debtor_consents_phone
    on debtor_consents(debtor_phone);
create index if not exists idx_debtor_consents_active
    on debtor_consents(debt_id)
    where revoked_at is null;

-- 2. Message Suppressions (Global privacy opt-out table, SHA-256 hash only)
create table if not exists message_suppressions (
    phone_hash varchar(64) primary key,
    reason varchar(32) not null,
    created_at timestamptz not null default now(),
    resumed_at timestamptz,
    constraint ck_message_suppressions_reason check (
        reason in ('stop_keyword', 'stop_button', 'not_me', 'wrong_number', 'unreachable')
    )
);

create index if not exists idx_message_suppressions_active
    on message_suppressions(phone_hash)
    where resumed_at is null;

-- 3. Debtor Messages (Truthful message delivery tracking)
create table if not exists debtor_messages (
    id uuid primary key default gen_random_uuid(),
    business_id uuid not null references businesses(id) on delete cascade,
    debt_id uuid not null references debts(id) on delete cascade,
    stage varchar(32) not null,
    template_name varchar(64) not null,
    template_version varchar(32) not null default 'v2',
    wamid varchar(160),
    status varchar(32) not null default 'queued',
    error_code varchar(32),
    error_title text,
    pricing_category varchar(32),
    attempt integer not null default 1,
    next_retry_at timestamptz,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint ck_debtor_messages_status check (
        status in ('queued', 'accepted', 'sent', 'delivered', 'read', 'failed', 'suppressed')
    )
);

create index if not exists idx_debtor_messages_business_debt
    on debtor_messages(business_id, debt_id);
create index if not exists idx_debtor_messages_wamid
    on debtor_messages(wamid)
    where wamid is not null;
create index if not exists idx_debtor_messages_status
    on debtor_messages(status);

-- 4. Reminder State (Per-debt ladder state machine)
create table if not exists reminder_state (
    debt_id uuid primary key references debts(id) on delete cascade,
    business_id uuid not null references businesses(id) on delete cascade,
    stage varchar(32) not null default 'record',
    paused_until timestamptz,
    paused_reason varchar(64),
    last_sent_at timestamptz,
    escalated_to_merchant_at timestamptz,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create index if not exists idx_reminder_state_business
    on reminder_state(business_id);
create index if not exists idx_reminder_state_stage
    on reminder_state(stage);

-- 5. Debt Events (Immutable audit log for timeline and evidence pack)
create table if not exists debt_events (
    id uuid primary key default gen_random_uuid(),
    business_id uuid not null references businesses(id) on delete cascade,
    debt_id uuid not null references debts(id) on delete cascade,
    type varchar(32) not null,
    payload jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now(),
    constraint ck_debt_events_type check (
        type in ('sale', 'consent', 'message', 'tap', 'part_payment', 'note', 'dispute')
    )
);

create index if not exists idx_debt_events_business_debt
    on debt_events(business_id, debt_id, created_at asc);

-- Row-Level Security
alter table debtor_consents enable row level security;
alter table debtor_messages enable row level security;
alter table reminder_state enable row level security;
alter table debt_events enable row level security;

commit;
