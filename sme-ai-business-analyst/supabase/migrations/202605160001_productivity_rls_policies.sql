-- Add missing RLS policies for productivity module tables (tasks, activities, magic_links)
-- Resolves empty-result bugs when accessing these tables under RLS

-- 1. Service role full access
drop policy if exists service_role_all on tasks;
create policy service_role_all on tasks
for all
using (current_setting('request.jwt.claim.role', true) = 'service_role')
with check (current_setting('request.jwt.claim.role', true) = 'service_role');

drop policy if exists service_role_all on activities;
create policy service_role_all on activities
for all
using (current_setting('request.jwt.claim.role', true) = 'service_role')
with check (current_setting('request.jwt.claim.role', true) = 'service_role');

drop policy if exists service_role_all on magic_links;
create policy service_role_all on magic_links
for all
using (current_setting('request.jwt.claim.role', true) = 'service_role')
with check (current_setting('request.jwt.claim.role', true) = 'service_role');

-- 2. Tenant isolation policies (for business users accessing via authenticated JWT)
drop policy if exists tenant_isolation on tasks;
create policy tenant_isolation on tasks
for all
using (
    business_id::text = current_setting('request.jwt.claim.business_id', true)
    or current_setting('request.jwt.claim.role', true) in ('service_role', 'admin', 'system')
)
with check (
    business_id::text = current_setting('request.jwt.claim.business_id', true)
    or current_setting('request.jwt.claim.role', true) in ('service_role', 'admin', 'system')
);

drop policy if exists tenant_isolation on activities;
create policy tenant_isolation on activities
for all
using (
    business_id::text = current_setting('request.jwt.claim.business_id', true)
    or current_setting('request.jwt.claim.role', true) in ('service_role', 'admin', 'system')
)
with check (
    business_id::text = current_setting('request.jwt.claim.business_id', true)
    or current_setting('request.jwt.claim.role', true) in ('service_role', 'admin', 'system')
);

drop policy if exists tenant_isolation on magic_links;
create policy tenant_isolation on magic_links
for all
using (
    business_id::text = current_setting('request.jwt.claim.business_id', true)
    or current_setting('request.jwt.claim.role', true) in ('service_role', 'admin', 'system')
)
with check (
    business_id::text = current_setting('request.jwt.claim.business_id', true)
    or current_setting('request.jwt.claim.role', true) in ('service_role', 'admin', 'system')
);
