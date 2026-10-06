-- Migration: Phase 0 Foundational Data Model for Business Intelligence & Operations
-- Tables: customers, debts
-- Extensions: inventory_items (unit_cost, reorder_threshold), transactions (is_credit, customer_id)

-- 1. Ensure inventory_items columns
ALTER TABLE inventory_items 
ADD COLUMN IF NOT EXISTS unit_cost numeric(14, 2) CHECK (unit_cost IS NULL OR unit_cost >= 0);

ALTER TABLE inventory_items 
ADD COLUMN IF NOT EXISTS reorder_threshold numeric(14, 2) CHECK (reorder_threshold IS NULL OR reorder_threshold >= 0);

-- Backfill reorder_threshold from low_stock_threshold if null
UPDATE inventory_items 
SET reorder_threshold = low_stock_threshold 
WHERE reorder_threshold IS NULL AND low_stock_threshold IS NOT NULL;

-- 2. Create customers table
CREATE TABLE IF NOT EXISTS customers (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    business_id uuid NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
    name text NOT NULL,
    normalized_name text NOT NULL,
    phone_number text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_customers_business_norm ON customers(business_id, normalized_name);
CREATE INDEX IF NOT EXISTS idx_customers_business_id ON customers(business_id);

-- 3. Extend transactions table
ALTER TABLE transactions 
ADD COLUMN IF NOT EXISTS is_credit boolean NOT NULL DEFAULT false;

ALTER TABLE transactions 
ADD COLUMN IF NOT EXISTS customer_id uuid REFERENCES customers(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_transactions_customer_id ON transactions(business_id, customer_id);
CREATE INDEX IF NOT EXISTS idx_transactions_is_credit ON transactions(business_id, is_credit);

-- 4. Create debts table
CREATE TABLE IF NOT EXISTS debts (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    business_id uuid NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
    customer_id uuid NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    transaction_id uuid REFERENCES transactions(id) ON DELETE SET NULL,
    amount numeric(14, 2) NOT NULL CHECK (amount >= 0),
    currency text NOT NULL DEFAULT 'NGN',
    status text NOT NULL DEFAULT 'outstanding' CHECK (status IN ('outstanding', 'paid')),
    due_date timestamptz,
    notes text,
    created_at timestamptz NOT NULL DEFAULT now(),
    paid_at timestamptz
);

CREATE INDEX IF NOT EXISTS idx_debts_business_customer ON debts(business_id, customer_id);
CREATE INDEX IF NOT EXISTS idx_debts_business_status ON debts(business_id, status);
CREATE INDEX IF NOT EXISTS idx_debts_transaction_id ON debts(transaction_id);

-- 5. Row Level Security (RLS)
ALTER TABLE customers ENABLE ROW LEVEL SECURITY;
ALTER TABLE debts ENABLE ROW LEVEL SECURITY;

-- Drop existing policies if any
DROP POLICY IF EXISTS service_role_all ON customers;
CREATE POLICY service_role_all ON customers
FOR ALL
USING (current_setting('request.jwt.claim.role', true) = 'service_role')
WITH CHECK (current_setting('request.jwt.claim.role', true) = 'service_role');

DROP POLICY IF EXISTS tenant_isolation ON customers;
CREATE POLICY tenant_isolation ON customers
FOR ALL
USING (
    business_id::text = current_setting('request.jwt.claim.business_id', true)
    OR current_setting('request.jwt.claim.role', true) IN ('service_role', 'admin', 'system')
)
WITH CHECK (
    business_id::text = current_setting('request.jwt.claim.business_id', true)
    OR current_setting('request.jwt.claim.role', true) IN ('service_role', 'admin', 'system')
);

DROP POLICY IF EXISTS service_role_all ON debts;
CREATE POLICY service_role_all ON debts
FOR ALL
USING (current_setting('request.jwt.claim.role', true) = 'service_role')
WITH CHECK (current_setting('request.jwt.claim.role', true) = 'service_role');

DROP POLICY IF EXISTS tenant_isolation ON debts;
CREATE POLICY tenant_isolation ON debts
FOR ALL
USING (
    business_id::text = current_setting('request.jwt.claim.business_id', true)
    OR current_setting('request.jwt.claim.role', true) IN ('service_role', 'admin', 'system')
)
WITH CHECK (
    business_id::text = current_setting('request.jwt.claim.business_id', true)
    OR current_setting('request.jwt.claim.role', true) IN ('service_role', 'admin', 'system')
);
