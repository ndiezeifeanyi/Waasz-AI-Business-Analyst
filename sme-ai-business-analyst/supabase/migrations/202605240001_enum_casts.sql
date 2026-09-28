-- Migration: Create implicit casts from character varying to custom enum types
-- Resolves asyncpg and raw SQL type mismatch errors when inserting/updating varchar expressions into enum columns.

do $$
declare
    t text;
    enum_types text[] := array[
        'user_role',
        'subscription_plan',
        'message_direction',
        'whatsapp_message_type',
        'message_status',
        'business_record_type',
        'extraction_status',
        'confirmation_status',
        'transaction_status',
        'inventory_movement_type',
        'media_status',
        'report_type',
        'report_status',
        'ai_provider',
        'ai_operation'
    ];
begin
    foreach t in array enum_types loop
        begin
            execute format('create cast (character varying as %I) with inout as implicit', t);
        exception when duplicate_object then
            null;
        when others then
            raise notice 'Could not create cast for %: %', t, sqlerrm;
        end;
    end loop;
end $$;
