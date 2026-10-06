-- Migration: 202610060002_fix_sync_transaction_activity_trigger.sql
-- Fixes fn_sync_transaction_to_user_activity() to resolve user_id via members table
-- and populates created_by_member_id on user_activities.

ALTER TABLE user_activities ALTER COLUMN user_id DROP NOT NULL;

CREATE OR REPLACE FUNCTION fn_sync_transaction_to_user_activity()
RETURNS TRIGGER AS $$
DECLARE
    v_user_id UUID;
    v_title TEXT;
BEGIN
    IF NEW.status = 'confirmed' AND (OLD.status IS NULL OR OLD.status != 'confirmed') THEN
        -- 1. Try resolving user_id via created_by_member_id
        IF NEW.created_by_member_id IS NOT NULL THEN
            SELECT u.id INTO v_user_id
            FROM members m
            JOIN users u ON u.phone_number = m.wa_id
            WHERE m.id = NEW.created_by_member_id
            LIMIT 1;
        END IF;

        -- 2. Try resolving via source_message_id
        IF v_user_id IS NULL AND NEW.source_message_id IS NOT NULL THEN
            SELECT wm.user_id INTO v_user_id
            FROM whatsapp_messages wm
            WHERE wm.id = NEW.source_message_id
            LIMIT 1;
        END IF;

        -- 3. Fallback to owner member
        IF v_user_id IS NULL THEN
            SELECT u.id INTO v_user_id
            FROM members m
            JOIN users u ON u.phone_number = m.wa_id
            WHERE m.business_id = NEW.business_id AND m.role = 'owner'
            LIMIT 1;
        END IF;

        -- 4. Fallback to business phone number
        IF v_user_id IS NULL THEN
            SELECT u.id INTO v_user_id
            FROM businesses b
            JOIN users u ON u.phone_number = b.phone_number
            WHERE b.id = NEW.business_id
            LIMIT 1;
        END IF;

        v_title := initcap(NEW.transaction_type::text) || ': ' || coalesce(NEW.item_name, NEW.description, 'Record') || 
                   ' (NGN ' || to_char(coalesce(NEW.amount, 0), 'FM999,999,999.00') || ')';

        INSERT INTO user_activities (
            id, user_id, created_by_member_id, business_id, activity_type, title, details, visibility, occurred_at, created_at
        ) VALUES (
            gen_random_uuid(),
            v_user_id,
            NEW.created_by_member_id,
            NEW.business_id,
            NEW.transaction_type::text,
            v_title,
            jsonb_build_object(
                'transaction_id', NEW.id,
                'amount', NEW.amount,
                'currency', NEW.currency,
                'quantity', NEW.quantity,
                'unit', NEW.unit,
                'item_name', NEW.item_name,
                'created_by_member_id', NEW.created_by_member_id
            ),
            'business_shared',
            NEW.occurred_at,
            now()
        );
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
