-- Migration: 202610060001_multi_staff_identity.sql
-- Multi-staff support: members table, member attribution, source_wamid idempotency, and scoped confirmations

-- 1. Create members table
CREATE TABLE IF NOT EXISTS members (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    business_id UUID NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
    wa_id VARCHAR(32) NOT NULL,
    display_name TEXT,
    role VARCHAR(32) NOT NULL DEFAULT 'staff',
    status VARCHAR(32) NOT NULL DEFAULT 'invited',
    invited_by UUID REFERENCES members(id) ON DELETE SET NULL,
    consent_accepted_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    removed_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_members_business_id ON members(business_id);
CREATE INDEX IF NOT EXISTS idx_members_wa_id ON members(wa_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_active_member_wa_id ON members (wa_id) WHERE status IN ('invited', 'active');

ALTER TABLE members ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS service_role_members ON members;
CREATE POLICY service_role_members ON members FOR ALL USING (auth.role() = 'service_role');

-- 2. Backfill existing users into members as owners
INSERT INTO members (id, business_id, wa_id, display_name, role, status, consent_accepted_at, created_at)
SELECT
    u.id,
    u.business_id,
    u.phone_number,
    COALESCE(u.display_name, 'Business Owner'),
    'owner',
    'active',
    u.created_at,
    u.created_at
FROM users u
WHERE u.business_id IS NOT NULL
ON CONFLICT (id) DO NOTHING;

-- 3. Add created_by_member_id to relevant tables
ALTER TABLE transactions ADD COLUMN IF NOT EXISTS created_by_member_id UUID REFERENCES members(id) ON DELETE SET NULL;
ALTER TABLE inventory_movements ADD COLUMN IF NOT EXISTS created_by_member_id UUID REFERENCES members(id) ON DELETE SET NULL;
ALTER TABLE customers ADD COLUMN IF NOT EXISTS created_by_member_id UUID REFERENCES members(id) ON DELETE SET NULL;
ALTER TABLE debts ADD COLUMN IF NOT EXISTS created_by_member_id UUID REFERENCES members(id) ON DELETE SET NULL;
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS created_by_member_id UUID REFERENCES members(id) ON DELETE SET NULL;
ALTER TABLE user_goals ADD COLUMN IF NOT EXISTS created_by_member_id UUID REFERENCES members(id) ON DELETE SET NULL;
ALTER TABLE user_activities ADD COLUMN IF NOT EXISTS created_by_member_id UUID REFERENCES members(id) ON DELETE SET NULL;

-- Backfill created_by_member_id from user_id where available
UPDATE tasks SET created_by_member_id = user_id WHERE created_by_member_id IS NULL AND user_id IS NOT NULL;
UPDATE user_goals SET created_by_member_id = user_id WHERE created_by_member_id IS NULL AND user_id IS NOT NULL;
UPDATE user_activities SET created_by_member_id = user_id WHERE created_by_member_id IS NULL AND user_id IS NOT NULL;

-- Backfill transactions, inventory_movements, customers, debts from business owner
UPDATE transactions t
SET created_by_member_id = m.id
FROM members m
WHERE t.created_by_member_id IS NULL
  AND m.business_id = t.business_id
  AND m.role = 'owner';

UPDATE inventory_movements im
SET created_by_member_id = m.id
FROM members m
WHERE im.created_by_member_id IS NULL
  AND m.business_id = im.business_id
  AND m.role = 'owner';

UPDATE customers c
SET created_by_member_id = m.id
FROM members m
WHERE c.created_by_member_id IS NULL
  AND m.business_id = c.business_id
  AND m.role = 'owner';

UPDATE debts d
SET created_by_member_id = m.id
FROM members m
WHERE d.created_by_member_id IS NULL
  AND m.business_id = d.business_id
  AND m.role = 'owner';

CREATE INDEX IF NOT EXISTS idx_transactions_created_by_member ON transactions(created_by_member_id);
CREATE INDEX IF NOT EXISTS idx_debts_created_by_member ON debts(created_by_member_id);

-- 4. Add source_wamid idempotency columns
ALTER TABLE transactions ADD COLUMN IF NOT EXISTS source_wamid VARCHAR(160);
ALTER TABLE inventory_movements ADD COLUMN IF NOT EXISTS source_wamid VARCHAR(160);
ALTER TABLE debts ADD COLUMN IF NOT EXISTS source_wamid VARCHAR(160);

CREATE UNIQUE INDEX IF NOT EXISTS uq_transactions_business_wamid ON transactions(business_id, source_wamid) WHERE source_wamid IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_inventory_movements_business_wamid ON inventory_movements(business_id, source_wamid) WHERE source_wamid IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_debts_business_wamid ON debts(business_id, source_wamid) WHERE source_wamid IS NOT NULL;

-- 5. Extend audit_logs table for append-only multi-member auditing
ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS member_id UUID REFERENCES members(id) ON DELETE SET NULL;
ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS before_state JSONB;
ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS after_state JSONB;
ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS wamid VARCHAR(160);

-- Backfill audit_logs member_id from actor_user_id
UPDATE audit_logs SET member_id = actor_user_id WHERE member_id IS NULL AND actor_user_id IS NOT NULL;

-- 6. Add member_id to confirmations table
ALTER TABLE confirmations ADD COLUMN IF NOT EXISTS member_id UUID REFERENCES members(id) ON DELETE CASCADE;

-- Backfill confirmations member_id from owner member
UPDATE confirmations c
SET member_id = m.id
FROM members m
WHERE c.member_id IS NULL
  AND m.business_id = c.business_id
  AND m.role = 'owner';

CREATE INDEX IF NOT EXISTS idx_confirmations_biz_member ON confirmations(business_id, member_id);
