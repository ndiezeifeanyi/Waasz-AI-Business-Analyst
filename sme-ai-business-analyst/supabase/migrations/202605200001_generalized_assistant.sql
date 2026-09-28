-- Generalized Multi-Tenant Assistant Schema Migration
-- Enables pgvector, creates user_goals, user_activities, user_knowledge_documents,
-- user_knowledge_chunks, conversation_memories, and installs atomic transaction sync trigger.

-- 1. Enable pgvector extension
CREATE EXTENSION IF NOT EXISTS vector;

-- 2. Evolve users, businesses, and whatsapp_messages tables
ALTER TABLE users 
    ADD COLUMN IF NOT EXISTS niche VARCHAR(40) NOT NULL DEFAULT 'sme_owner',
    ADD COLUMN IF NOT EXISTS niche_context JSONB NOT NULL DEFAULT '{}'::jsonb,
    ADD COLUMN IF NOT EXISTS data_retention_days INTEGER NOT NULL DEFAULT 365,
    ADD COLUMN IF NOT EXISTS last_inbound_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS last_compacted_at TIMESTAMPTZ;

ALTER TABLE businesses
    ADD COLUMN IF NOT EXISTS data_retention_days INTEGER NOT NULL DEFAULT 730;

ALTER TABLE whatsapp_messages
    ADD COLUMN IF NOT EXISTS user_id UUID REFERENCES users(id) ON DELETE SET NULL;

-- Normalize phone numbers to ensure consistent E.164 formatting before backfilling
UPDATE users 
SET phone_number = '+' || regexp_replace(phone_number, '^\+?', '')
WHERE phone_number NOT LIKE '+%';

UPDATE whatsapp_messages
SET from_phone = '+' || regexp_replace(from_phone, '^\+?', '')
WHERE from_phone IS NOT NULL AND from_phone NOT LIKE '+%';

UPDATE whatsapp_messages
SET to_phone = '+' || regexp_replace(to_phone, '^\+?', '')
WHERE to_phone IS NOT NULL AND to_phone NOT LIKE '+%';

-- Backfill whatsapp_messages.user_id from users matching normalized phone numbers
UPDATE whatsapp_messages wm
SET user_id = u.id
FROM users u
WHERE wm.user_id IS NULL 
  AND (
      wm.from_phone = u.phone_number 
      OR wm.to_phone = u.phone_number
      OR regexp_replace(wm.from_phone, '\D', '', 'g') = regexp_replace(u.phone_number, '\D', '', 'g')
  );

CREATE INDEX IF NOT EXISTS idx_whatsapp_messages_user_created ON whatsapp_messages(user_id, created_at DESC);

-- 3. User Goals table for roadmap tracking
CREATE TABLE IF NOT EXISTS user_goals (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    business_id UUID REFERENCES businesses(id) ON DELETE CASCADE,
    title VARCHAR(300) NOT NULL,
    description TEXT,
    category VARCHAR(50) NOT NULL DEFAULT 'productivity', -- 'financial', 'career', 'productivity', 'learning'
    target_metric VARCHAR(100),
    target_value NUMERIC(14, 2),
    current_value NUMERIC(14, 2) DEFAULT 0,
    target_date TIMESTAMPTZ,
    status VARCHAR(30) NOT NULL DEFAULT 'in_progress', -- 'in_progress', 'achieved', 'abandoned'
    visibility VARCHAR(20) NOT NULL DEFAULT 'private' CHECK (visibility IN ('private', 'business_shared')),
    milestones JSONB DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 4. Generalized User Activities log (superset of ledger events and personal productivity)
CREATE TABLE IF NOT EXISTS user_activities (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    business_id UUID REFERENCES businesses(id) ON DELETE CASCADE,
    activity_type VARCHAR(50) NOT NULL, -- 'sale', 'expense', 'task_completed', 'milestone_reached', 'note', 'meeting'
    title VARCHAR(300) NOT NULL,
    details JSONB NOT NULL DEFAULT '{}'::jsonb,
    visibility VARCHAR(20) NOT NULL DEFAULT 'private' CHECK (visibility IN ('private', 'business_shared')),
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 5. User Knowledge Documents & Embeddings
CREATE TABLE IF NOT EXISTS user_knowledge_documents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    business_id UUID REFERENCES businesses(id) ON DELETE CASCADE,
    title VARCHAR(300) NOT NULL,
    source_type VARCHAR(50) NOT NULL DEFAULT 'chat_upload', -- 'chat_text', 'document', 'price_list', 'notes'
    visibility VARCHAR(20) NOT NULL DEFAULT 'private' CHECK (visibility IN ('private', 'business_shared')),
    raw_content TEXT NOT NULL,
    metadata JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS user_knowledge_chunks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id UUID REFERENCES user_knowledge_documents(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    business_id UUID REFERENCES businesses(id) ON DELETE CASCADE,
    visibility VARCHAR(20) NOT NULL DEFAULT 'private' CHECK (visibility IN ('private', 'business_shared')),
    chunk_index INTEGER NOT NULL DEFAULT 0,
    chunk_content TEXT NOT NULL,
    embedding vector(768),
    needs_reembedding BOOLEAN NOT NULL DEFAULT false,
    metadata JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 6. Long-Term Conversation Memories
CREATE TABLE IF NOT EXISTS conversation_memories (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    business_id UUID REFERENCES businesses(id) ON DELETE CASCADE,
    memory_type VARCHAR(40) NOT NULL DEFAULT 'fact', -- 'preference', 'fact', 'session_summary', 'goal'
    content TEXT NOT NULL,
    metadata JSONB DEFAULT '{}'::jsonb,
    embedding vector(768),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 7. Performance & Isolation Indexes
CREATE INDEX IF NOT EXISTS idx_user_goals_user ON user_goals(user_id, status);
CREATE INDEX IF NOT EXISTS idx_user_goals_business ON user_goals(business_id, visibility);
CREATE INDEX IF NOT EXISTS idx_user_activities_user_occurred ON user_activities(user_id, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_user_activities_business ON user_activities(business_id, visibility, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_knowledge_chunks_user ON user_knowledge_chunks(user_id);
CREATE INDEX IF NOT EXISTS idx_knowledge_chunks_doc ON user_knowledge_chunks(document_id);
CREATE INDEX IF NOT EXISTS idx_knowledge_chunks_shared ON user_knowledge_chunks(business_id, visibility);
CREATE INDEX IF NOT EXISTS idx_knowledge_chunks_reembed ON user_knowledge_chunks(needs_reembedding) WHERE needs_reembedding = true;
CREATE INDEX IF NOT EXISTS idx_knowledge_documents_shared ON user_knowledge_documents(business_id, visibility);
CREATE INDEX IF NOT EXISTS idx_conversation_memories_user ON conversation_memories(user_id);

-- HNSW Vector Indexes for cosine distance search
CREATE INDEX IF NOT EXISTS idx_knowledge_chunks_embedding ON user_knowledge_chunks USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS idx_conversation_memories_embedding ON conversation_memories USING hnsw (embedding vector_cosine_ops);

-- 8. Backfill existing confirmed transactions into user_activities
INSERT INTO user_activities (id, user_id, business_id, activity_type, title, details, visibility, occurred_at, created_at)
SELECT
    gen_random_uuid(),
    coalesce(u.id, (SELECT id FROM users ORDER BY created_at LIMIT 1)),
    t.business_id,
    t.transaction_type,
    initcap(t.transaction_type::text) || ': ' || coalesce(t.item_name, t.description, 'Record'),
    jsonb_build_object(
        'transaction_id', t.id,
        'amount', t.amount,
        'currency', t.currency,
        'quantity', t.quantity,
        'unit', t.unit,
        'item_name', t.item_name
    ),
    'business_shared',
    t.occurred_at,
    t.created_at
FROM transactions t
LEFT JOIN businesses b ON b.id = t.business_id
LEFT JOIN users u ON u.business_id = b.id AND u.role = 'owner'
WHERE t.status = 'confirmed'
ON CONFLICT DO NOTHING;

-- 9. Atomic Transaction Synchronization Trigger
CREATE OR REPLACE FUNCTION fn_sync_transaction_to_user_activity()
RETURNS TRIGGER AS $$
DECLARE
    v_user_id UUID;
    v_title TEXT;
BEGIN
    IF NEW.status = 'confirmed' AND (OLD.status IS NULL OR OLD.status != 'confirmed') THEN
        SELECT coalesce(wm.user_id, u.id) INTO v_user_id
        FROM businesses b
        LEFT JOIN users u ON u.business_id = b.id AND u.role = 'owner'
        LEFT JOIN whatsapp_messages wm ON wm.id = NEW.source_message_id
        WHERE b.id = NEW.business_id
        LIMIT 1;

        v_title := initcap(NEW.transaction_type) || ': ' || coalesce(NEW.item_name, NEW.description, 'Record') || 
                   ' (NGN ' || to_char(coalesce(NEW.amount, 0), 'FM999,999,999.00') || ')';

        INSERT INTO user_activities (
            id, user_id, business_id, activity_type, title, details, visibility, occurred_at, created_at
        ) VALUES (
            gen_random_uuid(),
            v_user_id,
            NEW.business_id,
            NEW.transaction_type,
            v_title,
            jsonb_build_object(
                'transaction_id', NEW.id,
                'amount', NEW.amount,
                'currency', NEW.currency,
                'quantity', NEW.quantity,
                'unit', NEW.unit,
                'item_name', NEW.item_name
            ),
            'business_shared',
            NEW.occurred_at,
            now()
        );
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_sync_confirmed_transaction_to_activity ON transactions;
CREATE TRIGGER trg_sync_confirmed_transaction_to_activity
AFTER INSERT OR UPDATE ON transactions
FOR EACH ROW
EXECUTE FUNCTION fn_sync_transaction_to_user_activity();

-- 10. Row Level Security Policies (Defense-in-Depth)
ALTER TABLE user_goals ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_activities ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_knowledge_documents ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_knowledge_chunks ENABLE ROW LEVEL SECURITY;
ALTER TABLE conversation_memories ENABLE ROW LEVEL SECURITY;

-- Service Role Full Access
DROP POLICY IF EXISTS service_role_all ON user_goals;
CREATE POLICY service_role_all ON user_goals FOR ALL
USING (current_setting('request.jwt.claim.role', true) = 'service_role')
WITH CHECK (current_setting('request.jwt.claim.role', true) = 'service_role');

DROP POLICY IF EXISTS service_role_all ON user_activities;
CREATE POLICY service_role_all ON user_activities FOR ALL
USING (current_setting('request.jwt.claim.role', true) = 'service_role')
WITH CHECK (current_setting('request.jwt.claim.role', true) = 'service_role');

DROP POLICY IF EXISTS service_role_all ON user_knowledge_documents;
CREATE POLICY service_role_all ON user_knowledge_documents FOR ALL
USING (current_setting('request.jwt.claim.role', true) = 'service_role')
WITH CHECK (current_setting('request.jwt.claim.role', true) = 'service_role');

DROP POLICY IF EXISTS service_role_all ON user_knowledge_chunks;
CREATE POLICY service_role_all ON user_knowledge_chunks FOR ALL
USING (current_setting('request.jwt.claim.role', true) = 'service_role')
WITH CHECK (current_setting('request.jwt.claim.role', true) = 'service_role');

DROP POLICY IF EXISTS service_role_all ON conversation_memories;
CREATE POLICY service_role_all ON conversation_memories FOR ALL
USING (current_setting('request.jwt.claim.role', true) = 'service_role')
WITH CHECK (current_setting('request.jwt.claim.role', true) = 'service_role');

-- User Isolation Policies
DROP POLICY IF EXISTS user_goals_isolation ON user_goals;
CREATE POLICY user_goals_isolation ON user_goals FOR ALL
USING (
    user_id::text = current_setting('request.jwt.claim.user_id', true)
    OR (visibility = 'business_shared' AND business_id::text = current_setting('request.jwt.claim.business_id', true))
    OR current_setting('request.jwt.claim.role', true) IN ('service_role', 'admin', 'system')
);

DROP POLICY IF EXISTS user_activities_isolation ON user_activities;
CREATE POLICY user_activities_isolation ON user_activities FOR ALL
USING (
    user_id::text = current_setting('request.jwt.claim.user_id', true)
    OR (visibility = 'business_shared' AND business_id::text = current_setting('request.jwt.claim.business_id', true))
    OR current_setting('request.jwt.claim.role', true) IN ('service_role', 'admin', 'system')
);

DROP POLICY IF EXISTS user_knowledge_documents_isolation ON user_knowledge_documents;
CREATE POLICY user_knowledge_documents_isolation ON user_knowledge_documents FOR ALL
USING (
    user_id::text = current_setting('request.jwt.claim.user_id', true)
    OR (visibility = 'business_shared' AND business_id::text = current_setting('request.jwt.claim.business_id', true))
    OR current_setting('request.jwt.claim.role', true) IN ('service_role', 'admin', 'system')
);

DROP POLICY IF EXISTS user_knowledge_chunks_isolation ON user_knowledge_chunks;
CREATE POLICY user_knowledge_chunks_isolation ON user_knowledge_chunks FOR ALL
USING (
    user_id::text = current_setting('request.jwt.claim.user_id', true)
    OR (visibility = 'business_shared' AND business_id::text = current_setting('request.jwt.claim.business_id', true))
    OR current_setting('request.jwt.claim.role', true) IN ('service_role', 'admin', 'system')
);

DROP POLICY IF EXISTS conversation_memories_isolation ON conversation_memories;
CREATE POLICY conversation_memories_isolation ON conversation_memories FOR ALL
USING (
    user_id::text = current_setting('request.jwt.claim.user_id', true)
    OR current_setting('request.jwt.claim.role', true) IN ('service_role', 'admin', 'system')
);

-- 11. Add is_provisional to businesses and update founder dashboard view
ALTER TABLE businesses ADD COLUMN IF NOT EXISTS is_provisional BOOLEAN NOT NULL DEFAULT FALSE;

CREATE OR REPLACE VIEW v_founder_dashboard_metrics AS
SELECT
    (SELECT count(*) FROM businesses WHERE deleted_at IS NULL AND is_provisional IS FALSE) AS active_businesses,
    (SELECT count(*) FROM whatsapp_messages WHERE direction = 'inbound' AND created_at >= now() - INTERVAL '24 hours') AS inbound_messages_24h,
    (SELECT count(*) FROM confirmations WHERE status = 'pending') AS pending_confirmations,
    (SELECT count(*) FROM transactions WHERE status = 'confirmed' AND created_at >= now() - INTERVAL '24 hours') AS confirmed_records_24h,
    (SELECT coalesce(sum(estimated_cost_usd), 0) FROM ai_cost_events WHERE created_at::date = current_date) AS ai_spend_today_usd;

