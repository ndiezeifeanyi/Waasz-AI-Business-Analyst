begin;

create extension if not exists pgcrypto;

do $$
begin
    create type user_role as enum ('owner', 'staff', 'admin');
exception when duplicate_object then null;
end $$;

do $$
begin
    create type subscription_plan as enum ('free', 'pro', 'pilot');
exception when duplicate_object then null;
end $$;

do $$
begin
    create type message_direction as enum ('inbound', 'outbound');
exception when duplicate_object then null;
end $$;

do $$
begin
    create type whatsapp_message_type as enum (
        'text',
        'image',
        'audio',
        'document',
        'interactive',
        'button',
        'unknown'
    );
exception when duplicate_object then null;
end $$;

do $$
begin
    create type message_status as enum (
        'received',
        'queued',
        'processed',
        'failed',
        'sent',
        'delivered',
        'read'
    );
exception when duplicate_object then null;
end $$;

do $$
begin
    create type business_record_type as enum ('sale', 'expense', 'inventory_update', 'unknown');
exception when duplicate_object then null;
end $$;

do $$
begin
    create type extraction_status as enum (
        'pending',
        'succeeded',
        'failed',
        'needs_clarification',
        'blocked_by_cost'
    );
exception when duplicate_object then null;
end $$;

do $$
begin
    create type confirmation_status as enum (
        'pending',
        'confirmed',
        'needs_edit',
        'corrected',
        'expired',
        'rejected'
    );
exception when duplicate_object then null;
end $$;

do $$
begin
    create type transaction_status as enum (
        'pending_confirmation',
        'confirmed',
        'rejected',
        'void'
    );
exception when duplicate_object then null;
end $$;

do $$
begin
    create type inventory_movement_type as enum ('stock_in', 'stock_out', 'adjustment');
exception when duplicate_object then null;
end $$;

do $$
begin
    create type media_status as enum ('pending', 'downloaded', 'processed', 'failed');
exception when duplicate_object then null;
end $$;

do $$
begin
    create type report_type as enum ('daily', 'weekly');
exception when duplicate_object then null;
end $$;

do $$
begin
    create type report_status as enum ('generated', 'sent', 'failed');
exception when duplicate_object then null;
end $$;

do $$
begin
    create type ai_provider as enum (
        'gemini',
        'groq',
        'openai',
        'google_vision',
        'tesseract',
        'whisper',
        'local_heuristic'
    );
exception when duplicate_object then null;
end $$;

do $$
begin
    create type ai_operation as enum ('extraction', 'confirmation', 'correction', 'report', 'ocr', 'voice');
exception when duplicate_object then null;
end $$;

create or replace function set_updated_at()
returns trigger as $$
begin
    new.updated_at = now();
    return new;
end;
$$ language plpgsql;

create table if not exists businesses (
    id uuid primary key default gen_random_uuid(),
    name text not null check (length(trim(name)) > 0),
    owner_name text,
    phone_number text not null unique check (phone_number ~ '^[0-9+]{8,20}$'),
    business_type text,
    country text not null default 'Nigeria',
    currency text not null default 'NGN',
    timezone text not null default 'Africa/Lagos',
    subscription_plan subscription_plan not null default 'pilot',
    onboarding_status text not null default 'active',
    daily_ai_spend_limit_usd numeric(12, 4) not null default 5.00 check (daily_ai_spend_limit_usd >= 0),
    settings jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    deleted_at timestamptz
);

drop trigger if exists trg_businesses_updated_at on businesses;
create trigger trg_businesses_updated_at
before update on businesses
for each row execute function set_updated_at();

create table if not exists users (
    id uuid primary key default gen_random_uuid(),
    business_id uuid references businesses(id) on delete cascade,
    phone_number text not null unique check (phone_number ~ '^[0-9+]{8,20}$'),
    display_name text,
    role user_role not null default 'owner',
    locale text not null default 'en-NG',
    is_active boolean not null default true,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    deleted_at timestamptz
);

drop trigger if exists trg_users_updated_at on users;
create trigger trg_users_updated_at
before update on users
for each row execute function set_updated_at();

create table if not exists webhook_events (
    id uuid primary key default gen_random_uuid(),
    provider text not null default 'whatsapp',
    provider_event_id text,
    payload jsonb not null,
    status message_status not null default 'received',
    error_message text,
    received_at timestamptz not null default now(),
    processed_at timestamptz,
    created_at timestamptz not null default now()
);

create table if not exists whatsapp_messages (
    id uuid primary key default gen_random_uuid(),
    business_id uuid references businesses(id) on delete set null,
    user_id uuid references users(id) on delete set null,
    webhook_event_id uuid references webhook_events(id) on delete set null,
    direction message_direction not null,
    whatsapp_message_id text,
    whatsapp_conversation_id text,
    from_phone text check (from_phone is null or from_phone ~ '^[0-9+]{8,20}$'),
    to_phone text check (to_phone is null or to_phone ~ '^[0-9+]{8,20}$'),
    message_type whatsapp_message_type not null default 'unknown',
    body text check (body is null or length(body) <= 4000),
    media_id text,
    interactive_reply_id text,
    raw_payload jsonb not null default '{}'::jsonb,
    status message_status not null default 'received',
    error_message text,
    received_at timestamptz,
    sent_at timestamptz,
    created_at timestamptz not null default now()
);

create table if not exists media_assets (
    id uuid primary key default gen_random_uuid(),
    business_id uuid references businesses(id) on delete cascade,
    whatsapp_message_id uuid references whatsapp_messages(id) on delete cascade,
    whatsapp_media_id text not null,
    media_type whatsapp_message_type not null,
    mime_type text,
    sha256 text,
    local_path text,
    storage_url text,
    extracted_text text,
    transcription_text text,
    processing_status media_status not null default 'pending',
    error_message text,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

drop trigger if exists trg_media_assets_updated_at on media_assets;
create trigger trg_media_assets_updated_at
before update on media_assets
for each row execute function set_updated_at();

create table if not exists ai_extractions (
    id uuid primary key default gen_random_uuid(),
    business_id uuid references businesses(id) on delete cascade,
    whatsapp_message_id uuid references whatsapp_messages(id) on delete set null,
    media_asset_id uuid references media_assets(id) on delete set null,
    source_text text not null,
    prompt_name text not null default 'extraction',
    prompt_version text not null default '1',
    provider ai_provider not null default 'gemini',
    model text not null,
    operation ai_operation not null default 'extraction',
    raw_response jsonb not null default '{}'::jsonb,
    extracted_record jsonb not null default '{}'::jsonb,
    record_type business_record_type not null default 'unknown',
    confidence numeric(5, 4) not null default 0 check (confidence >= 0 and confidence <= 1),
    needs_clarification boolean not null default false,
    status extraction_status not null default 'pending',
    error_message text,
    input_tokens integer not null default 0 check (input_tokens >= 0),
    output_tokens integer not null default 0 check (output_tokens >= 0),
    estimated_cost_usd numeric(12, 6) not null default 0 check (estimated_cost_usd >= 0),
    created_at timestamptz not null default now()
);

create table if not exists confirmations (
    id uuid primary key default gen_random_uuid(),
    business_id uuid not null references businesses(id) on delete cascade,
    ai_extraction_id uuid not null unique references ai_extractions(id) on delete cascade,
    source_message_id uuid references whatsapp_messages(id) on delete set null,
    confirmation_message_id uuid references whatsapp_messages(id) on delete set null,
    token text not null unique,
    confirmation_text text not null,
    extracted_record jsonb not null,
    status confirmation_status not null default 'pending',
    attempts integer not null default 0 check (attempts >= 0),
    reply_payload jsonb not null default '{}'::jsonb,
    correction_text text,
    expires_at timestamptz not null default (now() + interval '24 hours'),
    confirmed_at timestamptz,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

drop trigger if exists trg_confirmations_updated_at on confirmations;
create trigger trg_confirmations_updated_at
before update on confirmations
for each row execute function set_updated_at();

create table if not exists transactions (
    id uuid primary key default gen_random_uuid(),
    business_id uuid not null references businesses(id) on delete cascade,
    confirmation_id uuid unique references confirmations(id) on delete set null,
    ai_extraction_id uuid references ai_extractions(id) on delete set null,
    source_message_id uuid references whatsapp_messages(id) on delete set null,
    transaction_type business_record_type not null check (transaction_type in ('sale', 'expense')),
    item_name text,
    description text,
    quantity numeric(14, 2) check (quantity is null or quantity >= 0),
    unit text,
    unit_price numeric(14, 2) check (unit_price is null or unit_price >= 0),
    amount numeric(14, 2) check (amount is null or amount >= 0),
    currency text not null default 'NGN',
    occurred_at timestamptz not null default now(),
    status transaction_status not null default 'pending_confirmation',
    metadata jsonb not null default '{}'::jsonb,
    confirmed_at timestamptz,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

drop trigger if exists trg_transactions_updated_at on transactions;
create trigger trg_transactions_updated_at
before update on transactions
for each row execute function set_updated_at();

create table if not exists inventory_items (
    id uuid primary key default gen_random_uuid(),
    business_id uuid not null references businesses(id) on delete cascade,
    item_name text not null,
    normalized_item_name text not null,
    quantity_on_hand numeric(14, 2) not null default 0,
    unit text,
    low_stock_threshold numeric(14, 2) check (low_stock_threshold is null or low_stock_threshold >= 0),
    last_updated_from_confirmation_id uuid references confirmations(id) on delete set null,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    unique (business_id, normalized_item_name)
);

drop trigger if exists trg_inventory_items_updated_at on inventory_items;
create trigger trg_inventory_items_updated_at
before update on inventory_items
for each row execute function set_updated_at();

create table if not exists inventory_movements (
    id uuid primary key default gen_random_uuid(),
    business_id uuid not null references businesses(id) on delete cascade,
    inventory_item_id uuid references inventory_items(id) on delete set null,
    confirmation_id uuid references confirmations(id) on delete set null,
    ai_extraction_id uuid references ai_extractions(id) on delete set null,
    source_message_id uuid references whatsapp_messages(id) on delete set null,
    movement_type inventory_movement_type not null,
    item_name text not null,
    quantity numeric(14, 2) not null check (quantity >= 0),
    unit text,
    note text,
    occurred_at timestamptz not null default now(),
    status transaction_status not null default 'pending_confirmation',
    confirmed_at timestamptz,
    created_at timestamptz not null default now()
);

create table if not exists corrections (
    id uuid primary key default gen_random_uuid(),
    business_id uuid not null references businesses(id) on delete cascade,
    confirmation_id uuid not null references confirmations(id) on delete cascade,
    correction_message_id uuid references whatsapp_messages(id) on delete set null,
    correction_text text not null,
    corrected_record jsonb not null default '{}'::jsonb,
    status confirmation_status not null default 'corrected',
    created_at timestamptz not null default now()
);

create table if not exists business_reports (
    id uuid primary key default gen_random_uuid(),
    business_id uuid not null references businesses(id) on delete cascade,
    report_type report_type not null,
    period_start date not null,
    period_end date not null,
    total_sales numeric(14, 2) not null default 0,
    total_expenses numeric(14, 2) not null default 0,
    estimated_profit numeric(14, 2) not null default 0,
    records_count integer not null default 0 check (records_count >= 0),
    inventory_changes_count integer not null default 0 check (inventory_changes_count >= 0),
    body text not null,
    status report_status not null default 'generated',
    whatsapp_message_id uuid references whatsapp_messages(id) on delete set null,
    generated_at timestamptz not null default now(),
    sent_at timestamptz,
    created_at timestamptz not null default now(),
    unique (business_id, report_type, period_start, period_end),
    check (period_end >= period_start)
);

create table if not exists ai_cost_events (
    id uuid primary key default gen_random_uuid(),
    business_id uuid references businesses(id) on delete set null,
    ai_extraction_id uuid references ai_extractions(id) on delete set null,
    provider ai_provider not null,
    model text not null,
    operation ai_operation not null,
    input_tokens integer not null default 0 check (input_tokens >= 0),
    output_tokens integer not null default 0 check (output_tokens >= 0),
    estimated_cost_usd numeric(12, 6) not null default 0 check (estimated_cost_usd >= 0),
    metadata jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now()
);

create table if not exists audit_logs (
    id uuid primary key default gen_random_uuid(),
    business_id uuid references businesses(id) on delete set null,
    actor_user_id uuid references users(id) on delete set null,
    action text not null,
    entity_type text,
    entity_id uuid,
    metadata jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now()
);

create or replace view v_business_daily_rollups as
select
    business_id,
    occurred_at::date as business_date,
    coalesce(sum(amount) filter (where transaction_type = 'sale' and status = 'confirmed'), 0) as total_sales,
    coalesce(sum(amount) filter (where transaction_type = 'expense' and status = 'confirmed'), 0) as total_expenses,
    coalesce(sum(amount) filter (where transaction_type = 'sale' and status = 'confirmed'), 0)
        - coalesce(sum(amount) filter (where transaction_type = 'expense' and status = 'confirmed'), 0) as estimated_profit,
    count(*) filter (where status = 'confirmed') as confirmed_records
from transactions
group by business_id, occurred_at::date;

create or replace view v_daily_ai_spend as
select
    business_id,
    created_at::date as spend_date,
    coalesce(sum(estimated_cost_usd), 0) as estimated_spend_usd,
    count(*) as ai_calls
from ai_cost_events
group by business_id, created_at::date;

create or replace view v_founder_dashboard_metrics as
select
    (select count(*) from businesses where deleted_at is null) as active_businesses,
    (select count(*) from whatsapp_messages where direction = 'inbound' and created_at >= now() - interval '24 hours') as inbound_messages_24h,
    (select count(*) from confirmations where status = 'pending') as pending_confirmations,
    (select count(*) from transactions where status = 'confirmed' and created_at >= now() - interval '24 hours') as confirmed_records_24h,
    (select coalesce(sum(estimated_cost_usd), 0) from ai_cost_events where created_at::date = current_date) as ai_spend_today_usd;

commit;
