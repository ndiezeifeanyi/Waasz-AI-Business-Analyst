begin;

create unique index if not exists idx_webhook_events_provider_event
    on webhook_events(provider, provider_event_id)
    where provider_event_id is not null;

create unique index if not exists idx_whatsapp_messages_wa_id
    on whatsapp_messages(whatsapp_message_id)
    where whatsapp_message_id is not null;

create index if not exists idx_businesses_phone_number
    on businesses(phone_number);

create index if not exists idx_users_business_id
    on users(business_id);

create index if not exists idx_users_phone_number
    on users(phone_number);

create index if not exists idx_whatsapp_messages_business_created
    on whatsapp_messages(business_id, created_at desc);

create index if not exists idx_whatsapp_messages_from_phone
    on whatsapp_messages(from_phone);

create index if not exists idx_whatsapp_messages_status
    on whatsapp_messages(status);

create index if not exists idx_media_assets_message
    on media_assets(whatsapp_message_id);

create index if not exists idx_media_assets_business_status
    on media_assets(business_id, processing_status);

create index if not exists idx_ai_extractions_business_created
    on ai_extractions(business_id, created_at desc);

create index if not exists idx_ai_extractions_status
    on ai_extractions(status);

create index if not exists idx_confirmations_business_status
    on confirmations(business_id, status, created_at desc);

create index if not exists idx_confirmations_token
    on confirmations(token);

create index if not exists idx_confirmations_pending_expiry
    on confirmations(expires_at)
    where status = 'pending';

create index if not exists idx_transactions_business_type_date
    on transactions(business_id, transaction_type, occurred_at desc);

create index if not exists idx_transactions_business_status
    on transactions(business_id, status);

create index if not exists idx_inventory_items_business_item
    on inventory_items(business_id, normalized_item_name);

create index if not exists idx_inventory_movements_business_date
    on inventory_movements(business_id, occurred_at desc);

create index if not exists idx_corrections_confirmation
    on corrections(confirmation_id);

create index if not exists idx_business_reports_business_period
    on business_reports(business_id, report_type, period_start desc);

create index if not exists idx_ai_cost_events_business_date
    on ai_cost_events(business_id, created_at desc);

create index if not exists idx_ai_cost_events_operation
    on ai_cost_events(operation, created_at desc);

create index if not exists idx_audit_logs_business_created
    on audit_logs(business_id, created_at desc);

alter table businesses enable row level security;
alter table users enable row level security;
alter table webhook_events enable row level security;
alter table whatsapp_messages enable row level security;
alter table media_assets enable row level security;
alter table ai_extractions enable row level security;
alter table confirmations enable row level security;
alter table transactions enable row level security;
alter table inventory_items enable row level security;
alter table inventory_movements enable row level security;
alter table corrections enable row level security;
alter table business_reports enable row level security;
alter table ai_cost_events enable row level security;
alter table audit_logs enable row level security;

drop policy if exists service_role_all on businesses;
create policy service_role_all on businesses
for all
using (current_setting('request.jwt.claim.role', true) = 'service_role')
with check (current_setting('request.jwt.claim.role', true) = 'service_role');

drop policy if exists service_role_all on users;
create policy service_role_all on users
for all
using (current_setting('request.jwt.claim.role', true) = 'service_role')
with check (current_setting('request.jwt.claim.role', true) = 'service_role');

drop policy if exists service_role_all on webhook_events;
create policy service_role_all on webhook_events
for all
using (current_setting('request.jwt.claim.role', true) = 'service_role')
with check (current_setting('request.jwt.claim.role', true) = 'service_role');

drop policy if exists service_role_all on whatsapp_messages;
create policy service_role_all on whatsapp_messages
for all
using (current_setting('request.jwt.claim.role', true) = 'service_role')
with check (current_setting('request.jwt.claim.role', true) = 'service_role');

drop policy if exists service_role_all on media_assets;
create policy service_role_all on media_assets
for all
using (current_setting('request.jwt.claim.role', true) = 'service_role')
with check (current_setting('request.jwt.claim.role', true) = 'service_role');

drop policy if exists service_role_all on ai_extractions;
create policy service_role_all on ai_extractions
for all
using (current_setting('request.jwt.claim.role', true) = 'service_role')
with check (current_setting('request.jwt.claim.role', true) = 'service_role');

drop policy if exists service_role_all on confirmations;
create policy service_role_all on confirmations
for all
using (current_setting('request.jwt.claim.role', true) = 'service_role')
with check (current_setting('request.jwt.claim.role', true) = 'service_role');

drop policy if exists service_role_all on transactions;
create policy service_role_all on transactions
for all
using (current_setting('request.jwt.claim.role', true) = 'service_role')
with check (current_setting('request.jwt.claim.role', true) = 'service_role');

drop policy if exists service_role_all on inventory_items;
create policy service_role_all on inventory_items
for all
using (current_setting('request.jwt.claim.role', true) = 'service_role')
with check (current_setting('request.jwt.claim.role', true) = 'service_role');

drop policy if exists service_role_all on inventory_movements;
create policy service_role_all on inventory_movements
for all
using (current_setting('request.jwt.claim.role', true) = 'service_role')
with check (current_setting('request.jwt.claim.role', true) = 'service_role');

drop policy if exists service_role_all on corrections;
create policy service_role_all on corrections
for all
using (current_setting('request.jwt.claim.role', true) = 'service_role')
with check (current_setting('request.jwt.claim.role', true) = 'service_role');

drop policy if exists service_role_all on business_reports;
create policy service_role_all on business_reports
for all
using (current_setting('request.jwt.claim.role', true) = 'service_role')
with check (current_setting('request.jwt.claim.role', true) = 'service_role');

drop policy if exists service_role_all on ai_cost_events;
create policy service_role_all on ai_cost_events
for all
using (current_setting('request.jwt.claim.role', true) = 'service_role')
with check (current_setting('request.jwt.claim.role', true) = 'service_role');

drop policy if exists service_role_all on audit_logs;
create policy service_role_all on audit_logs
for all
using (current_setting('request.jwt.claim.role', true) = 'service_role')
with check (current_setting('request.jwt.claim.role', true) = 'service_role');

comment on table businesses is 'SME businesses using the WhatsApp-first business analyst.';
comment on table users is 'WhatsApp users attached to a business.';
comment on table webhook_events is 'Raw provider webhook events for idempotency and replay.';
comment on table whatsapp_messages is 'Inbound and outbound WhatsApp messages.';
comment on table media_assets is 'Voice notes, receipt photos, and other media pulled from WhatsApp.';
comment on table ai_extractions is 'AI extraction attempts from text, voice, or OCR source content.';
comment on table confirmations is 'Mandatory one-tap confirmation records before final ledger writes.';
comment on table transactions is 'Confirmed and staged sale or expense records.';
comment on table inventory_items is 'Current inventory balances by business and normalized item.';
comment on table inventory_movements is 'Confirmed and staged inventory changes.';
comment on table corrections is 'User corrections to pending confirmations.';
comment on table business_reports is 'Daily and weekly WhatsApp report outputs.';
comment on table ai_cost_events is 'Estimated AI/API spend events for daily budget enforcement.';
comment on table audit_logs is 'Security and operational audit log.';

commit;
