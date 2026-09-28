-- Fix initcap type casting on custom enum business_record_type in trigger
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

        v_title := initcap(NEW.transaction_type::text) || ': ' || coalesce(NEW.item_name, NEW.description, 'Record') || 
                   ' (NGN ' || to_char(coalesce(NEW.amount, 0), 'FM999,999,999.00') || ')';

        INSERT INTO user_activities (
            id, user_id, business_id, activity_type, title, details, visibility, occurred_at, created_at
        ) VALUES (
            gen_random_uuid(),
            v_user_id,
            NEW.business_id,
            NEW.transaction_type::text,
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
