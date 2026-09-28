-- Migration: Add nullable user_id to tasks table for individual sender reminder routing
-- Backward-compatible with existing business-wide tasks (NULL user_id)

ALTER TABLE tasks ADD COLUMN IF NOT EXISTS user_id UUID REFERENCES users(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS ix_tasks_user_id ON tasks(user_id);
