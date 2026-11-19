"""
Data Retention Purge Service for Debt Recovery.
Enforces statutory data retention limits by:
1. Anonymising debtor names and phone numbers on Customer records.
2. Sanitizing any debtor notes on Debt records.
3. Deleting message logs (DebtorMessage) and event audit logs (DebtEvent, ReminderState, DebtorConsent).
4. Strictly PRESERVING Debt and sales ledger (Transaction) rows.
Includes a dry-run mode that calculates and prints counts without writing changes.
This job is NOT scheduled automatically.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.business import Business
from app.models.customer import Customer
from app.models.debt import Debt
from app.models.debt_recovery import DebtEvent, DebtorConsent, DebtorMessage, ReminderState

logger = logging.getLogger(__name__)
UTC = timezone.utc


class RetentionService:
    async def purge_expired_debt_records(
        self, db: AsyncSession, dry_run: bool = False
    ) -> dict[str, int]:
        """
        Anonymises debtor names and phones and deletes message/event logs for settled/closed debts
        that exceed data_retention_days (default 730 days / 2 years).
        Preserves Debt and sales ledger (Transaction) rows.
        If dry_run is True: prints and logs counts without mutating database state.
        """
        now = datetime.now(UTC)
        total_stats = {
            "purged_messages": 0,
            "purged_events": 0,
            "purged_consents": 0,
            "purged_reminder_states": 0,
            "anonymised_customers": 0,
            "sanitized_debts": 0,
            "preserved_debts": 0,
            "dry_run": 1 if dry_run else 0,
        }

        # Query all active businesses
        biz_res = await db.execute(select(Business).where(Business.deleted_at.is_(None)))
        businesses = biz_res.scalars().all()

        for biz in businesses:
            retention_days = getattr(biz, "data_retention_days", 730) or 730
            cutoff = now - timedelta(days=retention_days)

            # Query settled or closed debts older than the cutoff
            debt_stmt = select(Debt).where(
                Debt.business_id == biz.id,
                Debt.status.in_(["paid", "uncollectible", "cancelled", "written_off"]),
                Debt.created_at < cutoff,
            )
            debt_res = await db.execute(debt_stmt)
            expired_debts = debt_res.scalars().all()

            if not expired_debts:
                continue

            expired_debt_ids = [d.id for d in expired_debts]
            customer_ids = list({d.customer_id for d in expired_debts if d.customer_id})

            total_stats["preserved_debts"] += len(expired_debts)

            if dry_run:
                # Count messages
                msg_count_stmt = select(func.count(DebtorMessage.id)).where(
                    DebtorMessage.debt_id.in_(expired_debt_ids)
                )
                msg_cnt = (await db.execute(msg_count_stmt)).scalar() or 0
                total_stats["purged_messages"] += msg_cnt

                # Count events
                evt_count_stmt = select(func.count(DebtEvent.id)).where(
                    DebtEvent.debt_id.in_(expired_debt_ids)
                )
                evt_cnt = (await db.execute(evt_count_stmt)).scalar() or 0
                total_stats["purged_events"] += evt_cnt

                # Count reminder states
                rem_count_stmt = select(func.count(ReminderState.debt_id)).where(
                    ReminderState.debt_id.in_(expired_debt_ids)
                )
                rem_cnt = (await db.execute(rem_count_stmt)).scalar() or 0
                total_stats["purged_reminder_states"] += rem_cnt

                # Count consents
                c_count_stmt = select(func.count(DebtorConsent.id)).where(
                    DebtorConsent.debt_id.in_(expired_debt_ids)
                )
                c_cnt = (await db.execute(c_count_stmt)).scalar() or 0
                total_stats["purged_consents"] += c_cnt

                total_stats["anonymised_customers"] += len(customer_ids)
                total_stats["sanitized_debts"] += len(expired_debts)

                print(
                    f"[DRY RUN] Business '{biz.name}' (cutoff {cutoff.date()}):\n"
                    f"  - DebtorMessage rows to delete: {msg_cnt}\n"
                    f"  - DebtEvent rows to delete: {evt_cnt}\n"
                    f"  - ReminderState rows to delete: {rem_cnt}\n"
                    f"  - DebtorConsent rows to delete: {c_cnt}\n"
                    f"  - Debtors to anonymise: {len(customer_ids)}\n"
                    f"  - Debt notes to sanitize: {len(expired_debts)}\n"
                    f"  - Debt rows KEPT: {len(expired_debts)} (Sales ledger rows intact)"
                )
                logger.info(
                    "[DRY RUN] Retention analysis for %s: %d messages, %d events, %d consents to delete; "
                    "%d customers to anonymise; %d debts kept intact.",
                    biz.name, msg_cnt, evt_cnt, c_cnt, len(customer_ids), len(expired_debts)
                )
            else:
                # 1. Delete associated DebtorMessage records
                del_msg = await db.execute(
                    delete(DebtorMessage).where(DebtorMessage.debt_id.in_(expired_debt_ids))
                )
                total_stats["purged_messages"] += del_msg.rowcount or 0

                # 2. Delete associated DebtEvent records
                del_evt = await db.execute(
                    delete(DebtEvent).where(DebtEvent.debt_id.in_(expired_debt_ids))
                )
                total_stats["purged_events"] += del_evt.rowcount or 0

                # 3. Delete associated ReminderState records
                del_rem = await db.execute(
                    delete(ReminderState).where(ReminderState.debt_id.in_(expired_debt_ids))
                )
                total_stats["purged_reminder_states"] += del_rem.rowcount or 0

                # 4. Delete associated DebtorConsent records
                del_c = await db.execute(
                    delete(DebtorConsent).where(DebtorConsent.debt_id.in_(expired_debt_ids))
                )
                total_stats["purged_consents"] += del_c.rowcount or 0

                # 5. Anonymise Customer records (remove debtor personal identifiers)
                if customer_ids:
                    cust_stmt = select(Customer).where(Customer.id.in_(customer_ids))
                    cust_res = await db.execute(cust_stmt)
                    customers = cust_res.scalars().all()
                    for c in customers:
                        c.name = f"Anonymised Debtor {str(c.id)[:6].upper()}"
                        c.normalized_name = f"anonymised debtor {str(c.id)[:6].lower()}"
                        c.phone_number = None
                        total_stats["anonymised_customers"] += 1

                # 6. Sanitize Debt notes (keep Debt rows and Transaction rows!)
                for d in expired_debts:
                    d.notes = None
                    total_stats["sanitized_debts"] += 1

                print(
                    f"[EXECUTE] Business '{biz.name}' retention purge completed:\n"
                    f"  - Deleted {del_msg.rowcount or 0} messages, {del_evt.rowcount or 0} events, {del_c.rowcount or 0} consents.\n"
                    f"  - Anonymised {len(customer_ids)} debtor names/phones.\n"
                    f"  - Kept all {len(expired_debts)} Debt and Sales ledger records intact."
                )
                logger.info(
                    "Purged expired debt logs & anonymised debtors for business %s: %s",
                    biz.name, total_stats
                )

        if not dry_run:
            await db.commit()

        return total_stats
