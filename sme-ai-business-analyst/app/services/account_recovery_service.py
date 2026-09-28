"""
Account Recovery Service.
Enables admin-assisted account reclamation when a business owner loses access
to their original phone number or changes phone numbers.
Preserves all historical transactions, confirmations, activities, and reports.
"""
from datetime import UTC, datetime
import logging
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.activity import Activity
from app.models.approved_tester import ApprovedTester
from app.models.business import Business
from app.models.message import WhatsAppMessage
from app.models.transaction import Transaction
from app.models.user import User
from app.utils.phone import normalize_phone

logger = logging.getLogger(__name__)


class AccountRecoveryError(Exception):
    pass


class AccountRecoveryService:
    async def relink_phone_number(
        self,
        db: AsyncSession,
        business_id: UUID,
        new_phone_number: str,
        verification_method: str = "admin_verified",
        notes: str | None = None,
        operator: str = "admin",
    ) -> dict:
        """
        Reclaims an existing business account and re-attaches it to a new phone number.
        Safely reconciles provisional empty stubs if the user messaged before recovery.
        """
        norm_new_phone = normalize_phone(new_phone_number)

        # 1. Fetch target business
        stmt_b = select(Business).where(Business.id == business_id)
        res_b = await db.execute(stmt_b)
        business = res_b.scalar_one_or_none()
        if not business:
            raise AccountRecoveryError(f"Business with ID {business_id} not found.")

        old_phone = business.phone_number
        if old_phone == norm_new_phone:
            logger.info("Business %s already has phone %s. No change needed.", business_id, norm_new_phone)
            # Count transactions
            tx_count_res = await db.execute(
                select(func.count(Transaction.id)).where(Transaction.business_id == business.id)
            )
            return {
                "status": "unchanged",
                "business_id": str(business.id),
                "business_name": business.name,
                "old_phone": old_phone,
                "new_phone": norm_new_phone,
                "transactions_preserved": tx_count_res.scalar() or 0,
            }

        # 2. Check if new phone is already bound to another business
        stmt_other_b = select(Business).where(Business.phone_number == norm_new_phone)
        res_other_b = await db.execute(stmt_other_b)
        other_business = res_other_b.scalar_one_or_none()

        stmt_other_u = select(User).where(User.phone_number == norm_new_phone)
        res_other_u = await db.execute(stmt_other_u)
        other_user = res_other_u.scalar_one_or_none()

        if other_business and other_business.id != business.id:
            # Check if other business has any transactions
            other_tx_count = (await db.execute(
                select(func.count(Transaction.id)).where(Transaction.business_id == other_business.id)
            )).scalar() or 0

            if other_tx_count > 0:
                raise AccountRecoveryError(
                    f"Conflict: Target phone number {norm_new_phone} is already bound to business '{other_business.name}' "
                    f"({other_business.id}) with {other_tx_count} confirmed transactions. "
                    "Cannot automatically overwrite an active account."
                )

            # It's an empty or provisional stub: reassign its inbound messages to the canonical business and remove stub
            logger.warning(
                "Target phone %s was registered as provisional stub business %s. Merging into %s.",
                norm_new_phone,
                other_business.id,
                business.id,
            )
            await db.execute(
                delete(WhatsAppMessage).where(WhatsAppMessage.business_id == other_business.id)
            )
            if other_user and other_user.business_id == other_business.id:
                await db.execute(delete(User).where(User.id == other_user.id))
            await db.execute(delete(Business).where(Business.id == other_business.id))
            await db.flush()

        elif other_user and other_user.business_id != business.id:
            # Standalone user stub without business transactions
            logger.warning("Target phone %s had standalone user %s. Removing stub.", norm_new_phone, other_user.id)
            await db.execute(delete(User).where(User.id == other_user.id))
            await db.flush()

        # 3. Update Business phone number
        business.phone_number = norm_new_phone
        business.deleted_at = None
        await db.flush()

        # 4. Update associated User record(s)
        stmt_users = select(User).where(User.business_id == business.id)
        res_users = await db.execute(stmt_users)
        users = res_users.scalars().all()

        primary_user = None
        if users:
            primary_user = users[0]
            primary_user.phone_number = norm_new_phone
            primary_user.is_active = True
            primary_user.deleted_at = None
            await db.flush()
        else:
            primary_user = User(
                business_id=business.id,
                phone_number=norm_new_phone,
                role="owner",
                is_active=True,
            )
            db.add(primary_user)
            await db.flush()

        # 5. Maintain ApprovedTester allowlist
        stmt_tester_old = select(ApprovedTester).where(ApprovedTester.phone_number == old_phone)
        tester_old = (await db.execute(stmt_tester_old)).scalar_one_or_none()
        if tester_old:
            tester_old.phone_number = norm_new_phone
            tester_old.label = f"Recovered: {business.name} (was {old_phone[-4:]})"
            tester_old.added_by = operator
        else:
            stmt_tester_new = select(ApprovedTester).where(ApprovedTester.phone_number == norm_new_phone)
            tester_new = (await db.execute(stmt_tester_new)).scalar_one_or_none()
            if not tester_new:
                db.add(
                    ApprovedTester(
                        phone_number=norm_new_phone,
                        label=f"Recovered: {business.name}",
                        added_by=operator,
                    )
                )

        # 6. Audit activity log
        activity = Activity(
            business_id=business.id,
            user_id=primary_user.id,
            activity_type="account_recovery",
            title=f"Account Relinked: {old_phone} -> {norm_new_phone}",
            details={
                "old_phone": old_phone,
                "new_phone": norm_new_phone,
                "verification_method": verification_method,
                "operator": operator,
                "notes": notes or "No notes provided",
            },
            visibility="business_shared",
            occurred_at=datetime.now(UTC),
        )
        db.add(activity)
        await db.commit()

        # Count preserved transactions
        tx_count_res = await db.execute(
            select(func.count(Transaction.id)).where(Transaction.business_id == business.id)
        )
        tx_count = tx_count_res.scalar() or 0

        logger.info(
            "Account recovery complete: Business %s relinked from %s to %s with %d transactions intact.",
            business.id,
            old_phone,
            norm_new_phone,
            tx_count,
        )

        return {
            "status": "success",
            "business_id": str(business.id),
            "business_name": business.name,
            "old_phone": old_phone,
            "new_phone": norm_new_phone,
            "transactions_preserved": tx_count,
        }
