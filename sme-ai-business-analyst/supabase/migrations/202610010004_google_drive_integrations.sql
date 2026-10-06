-- Migration: 202610010004_google_drive_integrations.sql
-- Description: Create google_drive_integrations table for OAuth tokens and monthly PDF backups

CREATE TABLE IF NOT EXISTS google_drive_integrations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    business_id UUID NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
    user_id UUID REFERENCES users(id) ON DELETE SET NULL,
    encrypted_refresh_token TEXT NOT NULL,
    connected_email VARCHAR(255),
    folder_id VARCHAR(255),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    last_backup_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_google_drive_integrations_business_id UNIQUE (business_id)
);

CREATE INDEX IF NOT EXISTS idx_google_drive_integrations_business_id ON google_drive_integrations(business_id);
CREATE INDEX IF NOT EXISTS idx_google_drive_integrations_is_active ON google_drive_integrations(is_active);

ALTER TABLE google_drive_integrations ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "google_drive_integrations_business_isolation" ON google_drive_integrations;
CREATE POLICY "google_drive_integrations_business_isolation" ON google_drive_integrations
    FOR ALL
    USING (business_id = NULLIF(current_setting('app.current_business_id', true), '')::uuid);
