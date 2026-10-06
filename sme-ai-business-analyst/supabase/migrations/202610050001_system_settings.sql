-- Migration: System Settings Table for Dynamic Configuration
-- Enables administrators to adjust user capacity caps and access modes from the Founder Dashboard

CREATE TABLE IF NOT EXISTS system_settings (
    key VARCHAR(100) PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Seed defaults if not present
INSERT INTO system_settings (key, value)
VALUES 
    ('max_active_users', '50'),
    ('access_mode', 'allowlist')
ON CONFLICT (key) DO NOTHING;
