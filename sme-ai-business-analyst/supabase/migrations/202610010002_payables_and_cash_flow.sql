-- Migration: Phase 5 Cash Flow Forecasting & Payables Tracking
-- Tables: payables

CREATE TABLE IF NOT EXISTS payables (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    business_id uuid NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
    amount numeric(14, 2) NOT NULL CHECK (amount > 0),
    currency text NOT NULL DEFAULT 'NGN',
    due_date date NOT NULL,
    description text NOT NULL,
    vendor_name text,
    status text NOT NULL DEFAULT 'outstanding' CHECK (status IN ('outstanding', 'paid', 'cancelled')),
    paid_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_payables_business_due ON payables(business_id, due_date, status);
CREATE INDEX IF NOT EXISTS idx_payables_business_id ON payables(business_id);

-- Row Level Security (RLS)
ALTER TABLE payables ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS service_role_all ON payables;
CREATE POLICY service_role_all ON payables
    FOR ALL
    TO service_role
    USING (true)
    WITH CHECK (true);

DROP POLICY IF EXISTS tenant_isolation_payables ON payables;
CREATE POLICY tenant_isolation_payables ON payables
    FOR ALL
    USING (business_id = NULLIF(current_setting('app.current_business_id', true), '')::uuid)
    WITH CHECK (business_id = NULLIF(current_setting('app.current_business_id', true), '')::uuid);

COMMENT ON TABLE payables IS 'Upcoming supplier payables and bills for cash flow forecasting.';
