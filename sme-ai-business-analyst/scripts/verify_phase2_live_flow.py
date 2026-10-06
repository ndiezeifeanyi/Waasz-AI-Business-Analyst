#!/usr/bin/env python3
"""
End-to-End Live Verification for Phase 2: Staff Invites & Onboarding.
Connects to the real live database and executes live verification of:
1. Dynamic plan seat limits (system_settings, plan defaults, and business settings).
2. Owner invites staff member -> Member.status='invited', invite message with JOIN instruction dispatched.
3. 1:1 Phone Mapping -> Another business attempting to invite the same phone is rejected.
4. Seat Limit Enforcement -> Exceeding max seats is cleanly rejected with commercial messaging.
5. Staff Onboarding Acceptance -> Staff accepts via JOIN -> Member.status='active', consent_accepted_at set, User created.
6. Role-Based Permissions -> Staff attempting to invite, list, or remove staff is rejected.
7. Team Listing -> Owner retrieves live team list with roles, statuses, and seat usage.
8. Staff Removal -> Owner removes staff -> Member.status='removed', User.is_active=False.
9. Database Cleanup -> Complete, safe teardown of test artifacts.
"""
import asyncio
from datetime import UTC, datetime
from pathlib import Path
import sys
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from sqlalchemy import select, text
from app.core.database import async_session_factory
from app.models.business import Business
from app.models.member import Member
from app.models.user import User
from app.schemas.actor import ActorContext
from app.services.staff_service import StaffService


class MockLiveWhatsApp:
    def __init__(self):
        self.sent_messages: list[tuple[str, str]] = []

    async def send_text(self, to_phone: str, text: str):
        self.sent_messages.append((to_phone, text))
        return {"status": "sent", "to": to_phone}


async def main() -> None:
    print("=============================================================")
    print("PHASE 2 LIVE VERIFICATION: STAFF INVITES & ONBOARDING")
    print("=============================================================")

    staff_service = StaffService()
    whatsapp = MockLiveWhatsApp()

    # Dedicated test phone numbers
    test_owner_phone = "2348999990001"
    test_staff_phone_1 = "2348999990002"
    test_staff_phone_2 = "2348999990003"
    other_biz_owner_phone = "2348999990004"

    test_biz_ids = []

    async with async_session_factory() as session:
        try:
            # Step 0: Clean any previous test artifacts
            print("\n[Step 0] Cleaning any previous test artifacts...")
            all_test_phones = [test_owner_phone, test_staff_phone_1, test_staff_phone_2, other_biz_owner_phone, "2348999990099"]
            p_list = "('" + "', '".join(all_test_phones) + "')"
            await session.execute(text(f"DELETE FROM audit_logs WHERE metadata->>'wa_id' IN {p_list}"))
            await session.execute(text(f"DELETE FROM members WHERE wa_id IN {p_list}"))
            await session.execute(text(f"DELETE FROM users WHERE phone_number IN {p_list}"))
            await session.execute(text(f"DELETE FROM businesses WHERE phone_number IN {p_list}"))
            await session.commit()
            print(" -> Clean state prepared.")

            # Setup Test Business 1: "Live Pilot Store" (pilot plan, max 2 seats)
            biz1 = Business(
                id=uuid4(),
                name="Live Pilot Store",
                phone_number=test_owner_phone,
                owner_name="Chief Live Owner",
                subscription_plan="pilot",
            )
            session.add(biz1)
            test_biz_ids.append(biz1.id)

            owner_user1 = User(
                id=uuid4(),
                business_id=biz1.id,
                phone_number=test_owner_phone,
                display_name="Chief Live Owner",
                role="owner",
            )
            session.add(owner_user1)

            owner_member1 = Member(
                id=owner_user1.id,
                business_id=biz1.id,
                wa_id=test_owner_phone,
                display_name="Chief Live Owner",
                role="owner",
                status="active",
                consent_accepted_at=datetime.now(UTC),
            )
            session.add(owner_member1)
            await session.commit()

            owner_actor1 = ActorContext(
                business_id=biz1.id,
                member_id=owner_member1.id,
                role="owner",
                wa_id=test_owner_phone,
                display_name="Chief Live Owner",
            )

            # Step 1: Verify Dynamic Seat Limit
            print("\n[Step 1] Verifying dynamic plan seat limits against live database...")
            pilot_limit = await staff_service.get_plan_seat_limit(session, biz1)
            assert pilot_limit == 2, f"Expected 2 seats for pilot plan, got {pilot_limit}"
            print(f" -> Live Pilot Store seat limit: {pilot_limit} seats.")

            # Step 2: Owner invites Staff Member 1
            print(f"\n[Step 2] Owner inviting Staff Member 1 ({test_staff_phone_1})...")
            inv_res = await staff_service.invite_staff_member(
                db=session,
                business=biz1,
                actor=owner_actor1,
                display_name="Emeka Eze",
                phone_number=test_staff_phone_1,
                whatsapp=whatsapp,
            )
            await session.commit()
            assert inv_res["success"] is True, f"Invite failed: {inv_res}"
            print(f" -> Invite result: {inv_res['message']}")

            # Verify Member row in live DB
            mem1_q = await session.execute(
                select(Member).where(Member.wa_id == test_staff_phone_1, Member.business_id == biz1.id)
            )
            staff_mem1 = mem1_q.scalar_one_or_none()
            assert staff_mem1 is not None, "Member not found in database!"
            assert staff_mem1.status == "invited", f"Expected status 'invited', got {staff_mem1.status}"
            assert staff_mem1.invited_by == owner_member1.id, "invited_by not recorded correctly!"
            print(f" -> Verified live DB: Member {staff_mem1.id} has status='invited', invited_by={staff_mem1.invited_by}")

            # Verify WhatsApp invite message
            assert len(whatsapp.sent_messages) >= 1
            last_msg = whatsapp.sent_messages[-1]
            assert last_msg[0] == test_staff_phone_1
            assert "reply *JOIN*" in last_msg[1]
            print(f" -> Verified WhatsApp dispatch to {last_msg[0]}: prompt to reply JOIN verified.")

            # Step 3: Enforce 1:1 Phone Isolation Across Businesses
            print("\n[Step 3] Testing 1:1 Phone Mapping: Other business attempts to invite same staff phone...")
            biz2 = Business(
                id=uuid4(),
                name="Other Competitor Store",
                phone_number=other_biz_owner_phone,
                subscription_plan="free",
            )
            session.add(biz2)
            test_biz_ids.append(biz2.id)

            owner_member2 = Member(
                id=uuid4(),
                business_id=biz2.id,
                wa_id=other_biz_owner_phone,
                display_name="Other Owner",
                role="owner",
                status="active",
            )
            session.add(owner_member2)
            await session.commit()

            owner_actor2 = ActorContext(
                business_id=biz2.id,
                member_id=owner_member2.id,
                role="owner",
                wa_id=other_biz_owner_phone,
                display_name="Other Owner",
            )

            inv_conflict = await staff_service.invite_staff_member(
                db=session,
                business=biz2,
                actor=owner_actor2,
                display_name="Duplicate Attempt",
                phone_number=test_staff_phone_1,
                whatsapp=whatsapp,
            )
            assert inv_conflict["success"] is False, "Expected 1:1 phone conflict to be rejected!"
            assert "already registered with another business" in inv_conflict["error"]
            print(f" -> Correctly rejected 1:1 violation: '{inv_conflict['error']}'")

            # Step 4: Test Dynamic Seat Limit Rejection
            print("\n[Step 4] Testing seat limit rejection on Free Plan (max 1 seat)...")
            # Invite first staff for biz2 (allowed)
            inv_free_1 = await staff_service.invite_staff_member(
                db=session,
                business=biz2,
                actor=owner_actor2,
                display_name="First Staff",
                phone_number=test_staff_phone_2,
                whatsapp=whatsapp,
            )
            await session.commit()
            assert inv_free_1["success"] is True, f"First invite should succeed: {inv_free_1}"
            print(" -> First staff invited to Free Plan (1/1 seats used).")

            # Try to invite second staff for biz2 (must be rejected)
            inv_free_2 = await staff_service.invite_staff_member(
                db=session,
                business=biz2,
                actor=owner_actor2,
                display_name="Second Staff Exceeding Limit",
                phone_number="2348999990099",
                whatsapp=whatsapp,
            )
            assert inv_free_2["success"] is False, "Expected seat limit rejection!"
            assert "Seat limit reached" in inv_free_2["error"]
            print(f" -> Correctly rejected seat limit overflow: '{inv_free_2['error']}'")

            # Step 5: Staff Member 1 Accepts Invite
            print(f"\n[Step 5] Staff Member 1 ({test_staff_phone_1}) accepts invite via JOIN...")
            accept_res = await staff_service.accept_staff_invite(
                db=session,
                phone_number=test_staff_phone_1,
                whatsapp=whatsapp,
            )
            await session.commit()
            assert accept_res["success"] is True, f"Accept failed: {accept_res}"
            print(f" -> Staff accepted: {accept_res['display_name']} joined {accept_res['business_name']}")

            # Verify Member status transitioned to 'active' and consent_accepted_at set
            await session.refresh(staff_mem1)
            assert staff_mem1.status == "active", f"Expected active status, got {staff_mem1.status}"
            assert staff_mem1.consent_accepted_at is not None, "consent_accepted_at not stamped!"
            print(f" -> Live DB verified: Member {staff_mem1.id} is 'active' with consent stamped at {staff_mem1.consent_accepted_at}")

            # Verify User record exists with role='staff' and business_id
            u_chk = await session.execute(
                select(User).where(User.phone_number == test_staff_phone_1)
            )
            staff_user = u_chk.scalar_one_or_none()
            assert staff_user is not None, "User record was not created for staff member!"
            assert staff_user.role == "staff", f"Expected user role 'staff', got {staff_user.role}"
            assert staff_user.business_id == biz1.id, "Staff user not attached to correct business_id!"
            print(f" -> Live DB verified: User {staff_user.id} created with role='staff' attached to {biz1.id}")

            # Step 6: Permission Boundary Checks (Staff cannot invite/list/remove)
            print("\n[Step 6] Testing Permission Boundaries: Staff cannot manage staff...")
            staff_actor = ActorContext(
                business_id=biz1.id,
                member_id=staff_mem1.id,
                role="staff",
                wa_id=test_staff_phone_1,
                display_name="Emeka Eze",
            )
            p_inv = await staff_service.invite_staff_member(
                session, biz1, staff_actor, "Intruder", "2348999990088", whatsapp
            )
            assert p_inv["success"] is False and "Permission Denied" in p_inv["error"]
            print(f" -> Staff invite attempt denied: '{p_inv['error']}'")

            p_list = await staff_service.list_staff_members(session, biz1, staff_actor)
            assert p_list["success"] is False and "Permission Denied" in p_list["error"]
            print(f" -> Staff list attempt denied: '{p_list['error']}'")

            p_remove = await staff_service.remove_staff_member(session, biz1, staff_actor, "Emeka Eze", whatsapp)
            assert p_remove["success"] is False and "Permission Denied" in p_remove["error"]
            print(f" -> Staff remove attempt denied: '{p_remove['error']}'")

            # Step 7: Owner Lists Team
            print("\n[Step 7] Owner lists team members...")
            team_res = await staff_service.list_staff_members(session, biz1, owner_actor1)
            assert team_res["success"] is True, f"List failed: {team_res}"
            print(" -> Team list output:\n" + team_res["formatted_text"])
            assert "Emeka Eze" in team_res["formatted_text"]
            assert "Chief Live Owner" in team_res["formatted_text"]
            assert "1/2" in team_res["formatted_text"]  # 1 staff member out of 2 seats

            # Step 8: Owner Removes Staff
            print("\n[Step 8] Owner removes staff member 'Emeka Eze'...")
            rm_res = await staff_service.remove_staff_member(
                session, biz1, owner_actor1, "Emeka Eze", whatsapp=whatsapp
            )
            await session.commit()
            assert rm_res["success"] is True, f"Remove failed: {rm_res}"
            print(f" -> Remove result: {rm_res['message']}")

            # Verify Member row status is 'removed'
            await session.refresh(staff_mem1)
            assert staff_mem1.status == "removed"
            assert staff_mem1.removed_at is not None
            print(f" -> Live DB verified: Member {staff_mem1.id} status='removed' at {staff_mem1.removed_at}")

            # Verify User is deactivated
            await session.refresh(staff_user)
            assert staff_user.is_active is False
            print(f" -> Live DB verified: User {staff_user.id} is_active=False")

            print("\n=============================================================")
            print("ALL 8 PHASE 2 LIVE VERIFICATION STEPS PASSED SUCCESSFULLY! ✅")
            print("=============================================================")

        finally:
            # Step 9: Teardown and Cleanup
            print("\n[Step 9] Cleaning up live test artifacts...")
            all_phones = [test_owner_phone, test_staff_phone_1, test_staff_phone_2, other_biz_owner_phone, "2348999990099"]
            p_clean = "('" + "', '".join(all_phones) + "')"
            await session.execute(text(f"DELETE FROM audit_logs WHERE metadata->>'wa_id' IN {p_clean}"))
            await session.execute(text(f"DELETE FROM members WHERE wa_id IN {p_clean}"))
            await session.execute(text(f"DELETE FROM users WHERE phone_number IN {p_clean}"))
            if test_biz_ids:
                biz_clean = "('" + "', '".join(str(b) for b in test_biz_ids) + "')"
                await session.execute(text(f"DELETE FROM businesses WHERE id IN {biz_clean}"))
            await session.commit()
            print(" -> Live database fully cleaned up.")


if __name__ == "__main__":
    asyncio.run(main())
