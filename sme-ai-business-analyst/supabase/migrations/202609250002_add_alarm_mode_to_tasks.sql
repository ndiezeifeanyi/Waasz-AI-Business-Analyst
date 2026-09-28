-- Migration: Add repeating alarm mode columns to tasks table
-- Backward-compatible: defaults to single-shot (is_alarm_mode = false)

ALTER TABLE tasks ADD COLUMN IF NOT EXISTS is_alarm_mode BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS repeat_interval_seconds INTEGER NOT NULL DEFAULT 60;
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS max_repeats INTEGER NOT NULL DEFAULT 10;
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS repeat_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS dismissed_at TIMESTAMPTZ NULL;
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS last_repeat_sent_at TIMESTAMPTZ NULL;

CREATE INDEX IF NOT EXISTS ix_tasks_alarm_dispatch ON tasks(is_alarm_mode, reminder_sent_at, dismissed_at, completed_at);
