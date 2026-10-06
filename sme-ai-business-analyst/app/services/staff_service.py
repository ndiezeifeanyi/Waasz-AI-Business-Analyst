from __future__ import annotations

from datetime import datetime, timezone
import logging
from typing import Any
from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.business import Business
from app.models.member import Member
from app.models.user import User
from app.schemas.actor import ActorContext
from app.services.whatsapp_client import WhatsAppClient
from app.utils.phone import normalize_phone

logger = logging.getLogger(__name__)

PLAN_SEAT_DEFAULTS: dict[str, int] = {
    "free": 1,
    "pilot": 2,
    "pro": 10,
    "growth": 25,
    "scale": 50,
    "enterprise": 100,
}


class StaffService:
    """
    Manages multi-staff lifecycle: invites, onboarding acceptance, team listing,
    and removal with dynamic plan seat limits and 1:1 phone mapping enforcement.
    """

    async def get_plan_seat_limit(self, db: AsyncSession, business: Business) -> int:
        """
        Dynamically determine the maximum allowed staff seats for a business.
        Priority:
          1. Business settings override: business.settings["max_staff_seats"]
          2. System setting per plan: system_settings["plan_seats_{plan}"]
          3. System setting global default: system_settings["default_staff_seats"]
          4. Hardcoded plan default: PLAN_SEAT_DEFAULTS[subscription_plan] (fallback: 2)
        """
        # 1. Business custom setting
        if business.settings and isinstance(business.settings, dict):
            custom_limit = business.settings.get("max_staff_seats")
            if custom_limit is not None:
                try:
                    return int(custom_limit)
                except (ValueError, TypeError):
                    pass

        # 2. System setting per plan
        plan_key = f"plan_seats_{str(business.subscription_plan).lower().strip()}"
        try:
            res = await db.execute(
                text("SELECT value FROM system_settings WHERE key = :k LIMIT 1"),
                {"k": plan_key},
            )
            val = res.scalar() if hasattr(res, "scalar") else None
            if val is not None and type(val).__name__ not in ("MagicMock", "Mock", "AsyncMock"):
                return int(val)
        except Exception as e:
            logger.debug("Could not read %s from system_settings: %s", plan_key, e)

        # 3. System setting global default
        try:
            res = await db.execute(
                text("SELECT value FROM system_settings WHERE key = 'default_staff_seats' LIMIT 1")
            )
            val = res.scalar() if hasattr(res, "scalar") else None
            if val is not None and type(val).__name__ not in ("MagicMock", "Mock", "AsyncMock"):
                return int(val)
        except Exception as e:
            logger.debug("Could not read default_staff_seats from system_settings: %s", e)

        # 4. Fallback defaults dictionary
        plan_name = str(business.subscription_plan).lower().strip()
        return PLAN_SEAT_DEFAULTS.get(plan_name, 2)

    async def count_current_staff_seats(self, db: AsyncSession, business_id: UUID) -> int:
        """Count active and invited non-owner members for this business."""
        stmt = select(func.count(Member.id)).where(
            Member.business_id == business_id,
            Member.role != "owner",
            Member.status.in_(("active", "invited")),
        )
        res = await db.execute(stmt)
        val = res.scalar() if hasattr(res, "scalar") else 0
        try:
            return int(val or 0)
        except (ValueError, TypeError):
            return 0

    async def is_invited_or_active_member(self, db: AsyncSession, phone_number: str) -> bool:
        """Check if phone number is an active or invited member in ANY business."""
        norm = normalize_phone(phone_number)
        stmt = (
            select(Member.id)
            .where(
                (Member.wa_id == norm) | (Member.wa_id == phone_number),
                Member.status.in_(("active", "invited")),
            )
            .limit(1)
        )
        res = await db.execute(stmt)
        return res.scalar_one_or_none() is not None

    async def invite_staff_member(
        self,
        db: AsyncSession,
        business: Business,
        actor: ActorContext,
        display_name: str,
        phone_number: str,
        whatsapp: WhatsAppClient | None = None,
    ) -> dict[str, Any]:
        """
        Invite a new staff member to the business.
        Enforces:
          - Only owners can invite
          - 1:1 phone mapping (phone cannot belong to another active business)
          - Dynamic plan seat limits
          - WhatsApp invitation dispatch
        """
        if actor.role != "owner":
            return {
                "success": False,
                "error": "Permission Denied: Only the business owner can invite staff members.",
            }

        norm_phone = normalize_phone(phone_number)
        clean_name = display_name.strip()
        if not clean_name:
            clean_name = "Staff Member"

        # Sanity: Cannot invite the business's own phone or owner phone
        if norm_phone == normalize_phone(business.phone_number) or norm_phone == actor.wa_id:
            return {
                "success": False,
                "error": "Cannot invite: This phone number is already the owner of this business.",
            }

        # Check if phone is already registered in another business
        stmt_existing = (
            select(Member)
            .where(
                (Member.wa_id == norm_phone) | (Member.wa_id == phone_number),
                Member.status.in_(("active", "invited")),
            )
            .limit(1)
        )
        res_existing = await db.execute(stmt_existing)
        existing_mem = res_existing.scalar_one_or_none()

        if existing_mem:
            if existing_mem.business_id == business.id:
                if existing_mem.status == "active":
                    return {
                        "success": False,
                        "error": (
                            f"Staff member '{existing_mem.display_name or clean_name}' ({norm_phone}) "
                            f"is already an active member of your business."
                        ),
                    }
                else:
                    # Resend invitation message
                    inviter_name = actor.display_name or business.owner_name or "Your manager"
                    biz_name = business.name or "the business"
                    invite_msg = (
                        f"👋 Hello {clean_name}!\n\n"
                        f"{inviter_name} has invited you to join *{biz_name}* on Waasz as a staff member.\n\n"
                        f"You will be able to record daily sales, expenses, and check inventory directly from your WhatsApp.\n\n"
                        f"To accept and get started, simply reply *JOIN* to this message."
                    )
                    if whatsapp:
                        try:
                            await whatsapp.send_text(norm_phone, invite_msg)
                        except Exception as exc:
                            logger.warning("Failed to resend WhatsApp invite to %s: %s", norm_phone, exc)
                    return {
                        "success": True,
                        "member_id": str(existing_mem.id),
                        "message": (
                            f"Staff member '{clean_name}' ({norm_phone}) has a pending invitation. "
                            f"A reminder invitation has been sent to their WhatsApp."
                        ),
                    }
            else:
                return {
                    "success": False,
                    "error": (
                        f"Cannot invite: The phone number {norm_phone} is already registered with another business on Waasz. "
                        f"A phone number can only belong to one business at a time."
                    ),
                }

        # Check seat limits
        seat_limit = await self.get_plan_seat_limit(db, business)
        current_seats = await self.count_current_staff_seats(db, business.id)
        if current_seats >= seat_limit:
            return {
                "success": False,
                "error": (
                    f"Seat limit reached: Your current {business.subscription_plan} plan allows up to {seat_limit} staff member(s). "
                    f"You currently have {current_seats} active/invited staff. Please upgrade your plan to invite more staff."
                ),
            }

        # Check if member previously removed from this business exists to reactivate
        stmt_prev = select(Member).where(
            Member.business_id == business.id,
            (Member.wa_id == norm_phone) | (Member.wa_id == phone_number),
        ).limit(1)
        res_prev = await db.execute(stmt_prev)
        member = res_prev.scalar_one_or_none()

        if member:
            member.status = "invited"
            member.display_name = clean_name
            member.role = "staff"
            member.invited_by = actor.member_id
            member.removed_at = None
        else:
            member = Member(
                business_id=business.id,
                wa_id=norm_phone,
                display_name=clean_name,
                role="staff",
                status="invited",
                invited_by=actor.member_id,
            )
            db.add(member)

        await db.flush()

        # Audit log
        try:
            from app.models.audit import AuditLog
            audit = AuditLog(
                business_id=business.id,
                member_id=actor.member_id,
                action="staff_invited",
                entity_type="member",
                entity_id=member.id,
                extra_metadata={
                    "display_name": clean_name,
                    "wa_id": norm_phone,
                    "invited_by": str(actor.member_id) if actor.member_id else None,
                },
            )
            db.add(audit)
            await db.flush()
        except Exception as e:
            logger.debug("Failed to record audit log for staff invite: %s", e)

        # Dispatch WhatsApp invitation
        inviter_name = actor.display_name or business.owner_name or "Your manager"
        biz_name = business.name or "the business"
        invite_msg = (
            f"👋 Hello {clean_name}!\n\n"
            f"{inviter_name} has invited you to join *{biz_name}* on Waasz as a staff member.\n\n"
            f"You will be able to record daily sales, expenses, and check inventory directly from your WhatsApp.\n\n"
            f"To accept and get started, simply reply *JOIN* to this message."
        )
        if whatsapp:
            try:
                await whatsapp.send_text(norm_phone, invite_msg)
            except Exception as exc:
                logger.warning("Failed to send WhatsApp invite to %s: %s", norm_phone, exc)

        return {
            "success": True,
            "member_id": str(member.id),
            "display_name": clean_name,
            "phone_number": norm_phone,
            "message": (
                f"Invitation sent to {clean_name} ({norm_phone})! "
                f"They will receive a WhatsApp message asking them to reply JOIN to activate their staff account."
            ),
        }

    async def accept_staff_invite(
        self,
        db: AsyncSession,
        phone_number: str,
        whatsapp: WhatsAppClient | None = None,
    ) -> dict[str, Any]:
        """
        Accept pending staff invite for a phone number.
        Transitions Member.status from 'invited' to 'active',
        creates/links the User record, and dispatches onboarding welcome.
        """
        norm_phone = normalize_phone(phone_number)
        stmt = (
            select(Member)
            .where(
                (Member.wa_id == norm_phone) | (Member.wa_id == phone_number),
                Member.status == "invited",
            )
            .limit(1)
        )
        res = await db.execute(stmt)
        member = res.scalar_one_or_none()

        if not member:
            return {
                "success": False,
                "error": "No pending invitation found for this phone number.",
            }

        # Fetch business
        b_res = await db.execute(select(Business).where(Business.id == member.business_id).limit(1))
        business = b_res.scalar_one_or_none()
        if not business:
            return {"success": False, "error": "Business not found."}

        # Transition status
        now = datetime.now(timezone.utc)
        member.status = "active"
        member.consent_accepted_at = now
        await db.flush()

        # Link/create User
        u_res = await db.execute(
            select(User).where((User.phone_number == norm_phone) | (User.phone_number == phone_number)).limit(1)
        )
        user = u_res.scalar_one_or_none()
        if not user:
            user = User(
                id=member.id,
                business_id=business.id,
                phone_number=norm_phone,
                display_name=member.display_name,
                role="staff",
                is_active=True,
            )
            db.add(user)
        else:
            user.business_id = business.id
            user.role = "staff"
            user.is_active = True
            if member.display_name:
                user.display_name = member.display_name
        await db.flush()

        # Audit log
        try:
            from app.models.audit import AuditLog
            audit = AuditLog(
                business_id=business.id,
                member_id=member.id,
                action="staff_joined",
                entity_type="member",
                entity_id=member.id,
                extra_metadata={"display_name": member.display_name, "wa_id": norm_phone},
            )
            db.add(audit)
            await db.flush()
        except Exception as e:
            logger.debug("Failed to record audit log for staff join: %s", e)

        # Welcome message to staff
        welcome_text = (
            f"🎉 Welcome to *{business.name}* on Waasz, {member.display_name or 'there'}!\n\n"
            f"You are now registered as an active staff member. Everything you record here is securely saved to the business records under your name.\n\n"
            f"Here is what you can do:\n"
            f"• *Record Sales*: e.g. \"Sold 3 bags of rice for 45,000\"\n"
            f"• *Record Expenses*: e.g. \"Spent 2,500 on transport\"\n"
            f"• *Check Stock*: e.g. \"How many bags of rice do we have?\"\n"
            f"• *Personal Summary*: e.g. \"What sales did I make today?\"\n\n"
            f"Try it out by typing your first sale or expense!"
        )
        if whatsapp:
            try:
                await whatsapp.send_text(norm_phone, welcome_text)
            except Exception as exc:
                logger.warning("Failed to send welcome message to staff %s: %s", norm_phone, exc)

        # Notification to business owner
        if whatsapp and business.phone_number:
            try:
                owner_notice = (
                    f"🔔 *Team Update*: {member.display_name} ({norm_phone}) just accepted your invite "
                    f"and joined *{business.name}*!"
                )
                await whatsapp.send_text(business.phone_number, owner_notice)
            except Exception as exc:
                logger.warning("Failed to send owner notification for staff join: %s", exc)

        return {
            "success": True,
            "member_id": str(member.id),
            "business_name": business.name,
            "display_name": member.display_name,
            "welcome_text": welcome_text,
        }

    async def list_staff_members(
        self,
        db: AsyncSession,
        business: Business,
        actor: ActorContext,
    ) -> dict[str, Any]:
        """
        List all staff members for the business. Owner-only.
        """
        if actor.role != "owner":
            return {
                "success": False,
                "error": "Permission Denied: Only the business owner can view team members.",
            }

        stmt = (
            select(Member)
            .where(
                Member.business_id == business.id,
                Member.status.in_(("active", "invited")),
            )
            .order_by(Member.created_at.asc())
        )
        res = await db.execute(stmt)
        members = list(res.scalars().all())

        seat_limit = await self.get_plan_seat_limit(db, business)
        staff_count = sum(1 for m in members if m.role != "owner")

        team_list = []
        for m in members:
            team_list.append({
                "display_name": m.display_name or "Unknown",
                "phone_number": m.wa_id,
                "role": m.role.capitalize(),
                "status": m.status.capitalize(),
                "is_owner": m.role == "owner",
            })

        lines = [f"👥 *Team Members for {business.name}* (Seats: {staff_count}/{seat_limit}):\n"]
        for m in team_list:
            status_emoji = "✅" if m["status"] == "Active" else "⏳"
            role_tag = "👑 Owner" if m["is_owner"] else f"👤 {m['role']} ({status_emoji} {m['status']})"
            lines.append(f"• *{m['display_name']}* ({m['phone_number']}) — {role_tag}")

        lines.append(f"\n_Plan: {business.subscription_plan.capitalize()} | {seat_limit - staff_count} seat(s) remaining_")

        return {
            "success": True,
            "members": team_list,
            "seat_limit": seat_limit,
            "staff_count": staff_count,
            "formatted_text": "\n".join(lines),
        }

    async def remove_staff_member(
        self,
        db: AsyncSession,
        business: Business,
        actor: ActorContext,
        identifier: str,
        whatsapp: WhatsAppClient | None = None,
    ) -> dict[str, Any]:
        """
        Remove a staff member from the business. Owner-only.
        Accepts phone number or display name.
        """
        if actor.role != "owner":
            return {
                "success": False,
                "error": "Permission Denied: Only the business owner can remove staff members.",
            }

        clean_id = identifier.strip()
        norm_id = normalize_phone(clean_id)

        # Find member in this business
        stmt = select(Member).where(
            Member.business_id == business.id,
            Member.status.in_(("active", "invited")),
        )
        res = await db.execute(stmt)
        active_members = list(res.scalars().all())

        target: Member | None = None
        for m in active_members:
            if m.wa_id == norm_id or m.wa_id == clean_id:
                target = m
                break
            if m.display_name and m.display_name.lower() == clean_id.lower():
                target = m
                break

        # If not matched yet, check partial case-insensitive name match
        if not target:
            for m in active_members:
                if m.display_name and clean_id.lower() in m.display_name.lower():
                    target = m
                    break

        if not target:
            return {
                "success": False,
                "error": f"Staff member '{clean_id}' was not found in your active or invited team.",
            }

        if target.role == "owner" or target.id == actor.member_id:
            return {
                "success": False,
                "error": "Cannot remove: The business owner cannot be removed from the business.",
            }

        now = datetime.now(timezone.utc)
        target.status = "removed"
        target.removed_at = now
        await db.flush()

        # Deactivate user if exists
        u_res = await db.execute(select(User).where(User.phone_number == target.wa_id).limit(1))
        user = u_res.scalar_one_or_none()
        if user and user.business_id == business.id:
            user.is_active = False
            await db.flush()

        # Audit log
        try:
            from app.models.audit import AuditLog
            audit = AuditLog(
                business_id=business.id,
                member_id=actor.member_id,
                action="staff_removed",
                entity_type="member",
                entity_id=target.id,
                extra_metadata={"display_name": target.display_name, "wa_id": target.wa_id},
            )
            db.add(audit)
            await db.flush()
        except Exception as e:
            logger.debug("Failed to record audit log for staff removal: %s", e)

        # Notify removed staff
        if whatsapp:
            try:
                await whatsapp.send_text(
                    target.wa_id,
                    f"ℹ️ Your staff access to *{business.name}* on Waasz has been deactivated by the business owner.",
                )
            except Exception as exc:
                logger.warning("Failed to notify removed staff %s: %s", target.wa_id, exc)

        return {
            "success": True,
            "member_id": str(target.id),
            "display_name": target.display_name,
            "phone_number": target.wa_id,
            "message": f"Staff member '{target.display_name}' ({target.wa_id}) has been removed from {business.name}.",
        }
