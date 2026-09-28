-- Migration: Create invite_codes and waitlist tables
-- Supports self-service invite-code access control and waitlist registration

CREATE TABLE IF NOT EXISTS invite_codes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    code VARCHAR(64) NOT NULL UNIQUE,
    created_by_user_id UUID NULL REFERENCES users(id) ON DELETE SET NULL,
    max_uses INTEGER NOT NULL DEFAULT 1,
    use_count INTEGER NOT NULL DEFAULT 0,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    expires_at TIMESTAMPTZ NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_invite_codes_code ON invite_codes(code);
CREATE INDEX IF NOT EXISTS ix_invite_codes_active_claim ON invite_codes(code, is_active, use_count, max_uses);

CREATE TABLE IF NOT EXISTS waitlist (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    phone_number VARCHAR(32) NOT NULL UNIQUE,
    requested_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_waitlist_phone_number ON waitlist(phone_number);
