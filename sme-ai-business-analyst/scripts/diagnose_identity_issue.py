#!/usr/bin/env python3
"""
Diagnosis of the identity duplication / orphan issue for +2347065015924.
"""
import asyncio
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text
from app.core.database import async_session_factory


async def diagnose():
    async with async_session_factory() as s:
        # 1. Query users
        r_users = await s.execute(text("""
            SELECT id, business_id, phone_number, display_name, role, is_active, created_at, deleted_at 
            FROM users 
            WHERE phone_number LIKE '%7065015924%'
            ORDER BY created_at ASC
        """))
        users = r_users.fetchall()
        print("=== USERS ===")
        for u in users:
            print(dict(u._mapping))

        b_ids = [u.business_id for u in users if u.business_id]
        
        # 2. Query businesses
        r_businesses = await s.execute(text("""
            SELECT id, phone_number, name, onboarding_status, created_at, deleted_at 
            FROM businesses 
            WHERE phone_number LIKE '%7065015924%' OR id = ANY(:b_ids)
            ORDER BY created_at ASC
        """), {"b_ids": b_ids})
        businesses = r_businesses.fetchall()
        print("\n=== BUSINESSES ===")
        for b in businesses:
            print(dict(b._mapping))

        all_b_ids = list(set([b.id for b in businesses]))
        all_u_ids = [u.id for u in users]

        # 3. Query transactions
        if all_b_ids:
            r_tx = await s.execute(text("""
                SELECT business_id, count(*) as count, min(occurred_at) as earliest, max(occurred_at) as latest
                FROM transactions 
                WHERE business_id = ANY(:all_b_ids)
                GROUP BY business_id
            """), {"all_b_ids": all_b_ids})
            print("\n=== TRANSACTIONS PER BUSINESS ===")
            for tc in r_tx.fetchall():
                print(dict(tc._mapping))
        else:
            print("\n=== TRANSACTIONS PER BUSINESS: None ===")

        # 4. Query user_activities
        if all_u_ids:
            r_act = await s.execute(text("""
                SELECT user_id, count(*) as count
                FROM user_activities
                WHERE user_id = ANY(:all_u_ids)
                GROUP BY user_id
            """), {"all_u_ids": all_u_ids})
            print("\n=== USER ACTIVITIES PER USER ===")
            for ac in r_act.fetchall():
                print(dict(ac._mapping))
        else:
            print("\n=== USER ACTIVITIES PER USER: None ===")

        # 5. Query whatsapp_messages
        if all_u_ids or all_b_ids:
            r_msg = await s.execute(text("""
                SELECT user_id, business_id, count(*) as count
                FROM whatsapp_messages
                WHERE user_id = ANY(:all_u_ids) OR business_id = ANY(:all_b_ids)
                GROUP BY user_id, business_id
            """), {"all_u_ids": all_u_ids, "all_b_ids": all_b_ids})
            print("\n=== WHATSAPP MESSAGES PER USER/BUSINESS ===")
            for mc in r_msg.fetchall():
                print(dict(mc._mapping))

        # 6. Check confirmations table
        r_conf = await s.execute(text("""
            SELECT business_id, count(*) as count
            FROM confirmations
            WHERE business_id = ANY(:all_b_ids)
            GROUP BY business_id
        """), {"all_b_ids": all_b_ids})
        print("\n=== CONFIRMATIONS PER BUSINESS ===")
        for cc in r_conf.fetchall():
            print(dict(cc._mapping))

        # 7. Check ai_cost_events per business
        r_cost = await s.execute(text("""
            SELECT business_id, count(*) as count, sum(estimated_cost_usd) as total_usd
            FROM ai_cost_events
            WHERE business_id = ANY(:all_b_ids)
            GROUP BY business_id
        """), {"all_b_ids": all_b_ids})
        print("\n=== AI COST EVENTS PER BUSINESS ===")
        for cost in r_cost.fetchall():
            print(dict(cost._mapping))


if __name__ == "__main__":
    asyncio.run(diagnose())
