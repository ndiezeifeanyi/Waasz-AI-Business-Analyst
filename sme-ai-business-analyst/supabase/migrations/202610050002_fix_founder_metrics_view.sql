-- Migration: Synchronize Founder Dashboard Metrics View
-- Ensures active_businesses and confirmed_records only count businesses with real registered users,
-- and adds outbound_messages_24h.

DROP VIEW IF EXISTS v_founder_dashboard_metrics;

CREATE VIEW v_founder_dashboard_metrics AS
SELECT
    (SELECT count(DISTINCT b.id) FROM businesses b JOIN users u ON u.business_id = b.id WHERE b.deleted_at IS NULL) AS active_businesses,
    (SELECT count(*) FROM whatsapp_messages WHERE direction = 'inbound' AND created_at >= now() - INTERVAL '24 hours') AS inbound_messages_24h,
    (SELECT count(*) FROM whatsapp_messages WHERE direction = 'outbound' AND created_at >= now() - INTERVAL '24 hours') AS outbound_messages_24h,
    (SELECT count(*) FROM confirmations WHERE status = 'pending') AS pending_confirmations,
    (SELECT count(*) FROM transactions t JOIN businesses b ON b.id = t.business_id JOIN users u ON u.business_id = b.id WHERE t.status = 'confirmed' AND t.created_at >= now() - INTERVAL '24 hours') AS confirmed_records_24h,
    (SELECT coalesce(sum(estimated_cost_usd), 0) FROM ai_cost_events WHERE created_at::date = current_date) AS ai_spend_today_usd;
