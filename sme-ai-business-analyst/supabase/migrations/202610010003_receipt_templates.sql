-- 202610010003_receipt_templates.sql
-- Persistent per-business receipt and invoice template system

CREATE TABLE IF NOT EXISTS receipt_templates (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    business_id UUID NOT NULL UNIQUE REFERENCES businesses(id) ON DELETE CASCADE,
    business_display_name VARCHAR(255) NOT NULL,
    business_logo TEXT,
    address TEXT,
    contact_phone VARCHAR(50) NOT NULL,
    contact_email VARCHAR(255),
    payment_terms_note TEXT,
    footer_note TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_receipt_templates_business_id ON receipt_templates(business_id);

ALTER TABLE receipt_templates ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS service_role_receipt_templates ON receipt_templates;
CREATE POLICY service_role_receipt_templates ON receipt_templates
    FOR ALL
    USING (auth.role() = 'service_role');
