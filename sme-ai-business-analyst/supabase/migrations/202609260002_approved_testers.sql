-- Migration: Create approved_testers table
-- Supports allowlist access control strategy

CREATE TABLE IF NOT EXISTS approved_testers (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    phone_number VARCHAR(32) NOT NULL UNIQUE,
    label VARCHAR(255) NULL,
    added_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    added_by VARCHAR(128) NULL DEFAULT 'admin'
);

CREATE INDEX IF NOT EXISTS ix_approved_testers_phone_number ON approved_testers(phone_number);
