-- Migration: Add image_gen and grounded_search to ai_operation enum
ALTER TYPE ai_operation ADD VALUE IF NOT EXISTS 'image_gen';
ALTER TYPE ai_operation ADD VALUE IF NOT EXISTS 'grounded_search';
