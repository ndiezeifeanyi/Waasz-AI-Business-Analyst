import pytest
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4
from datetime import datetime, timezone

from app.models.business import Business
from app.models.member import Member
from app.models.user import User
from app.schemas.actor import ActorContext
from app.services.staff_service import StaffService
from app.services.agent_service import AgentService


@pytest.mark.asyncio
async def test_owner_can_invite_staff_member_within_seat_limit():
    service = StaffService()
    db = AsyncMock()
    whatsapp = AsyncMock()

    biz_id = uuid4()
    owner_id = uuid4()
    biz = Business(
        id=biz_id,
        name="Lekki Electronics",
        phone_number="2348011111111",
        subscription_plan="pilot",
    )
    actor = ActorContext(
        business_id=biz_id,
        member_id=owner_id,
        role="owner",
        wa_id="2348011111111",
        display_name="Chief Obi",
    )

    async def mock_execute(stmt, *args, **kwargs):
        s_str = str(stmt).lower()
        mock_res = MagicMock()
        if "system_settings" in s_str:
            mock_res.scalar.return_value = None
            return mock_res
        if "count" in s_str:
            mock_res.scalar.return_value = 0
            return mock_res
        mock_res.scalar_one_or_none.return_value = None
        mock_res.scalars.return_value.all.return_value = []
        return mock_res

    db.execute.side_effect = mock_execute

    res = await service.invite_staff_member(
        db=db,
        business=biz,
        actor=actor,
        display_name="Blessing",
        phone_number="08022223333",
        whatsapp=whatsapp,
    )

    assert res["success"] is True
    assert "Invitation sent to Blessing (2348022223333)" in res["message"]
    # Check WhatsApp message dispatched
    whatsapp.send_text.assert_called_once()
    call_args = whatsapp.send_text.call_args[0]
    assert call_args[0] == "2348022223333"
    assert "Chief Obi has invited you to join *Lekki Electronics*" in call_args[1]
    assert "reply *JOIN*" in call_args[1]


@pytest.mark.asyncio
async def test_staff_cannot_invite_staff():
    service = StaffService()
    db = AsyncMock()
    whatsapp = AsyncMock()

    biz_id = uuid4()
    staff_id = uuid4()
    biz = Business(id=biz_id, name="Lekki Electronics", phone_number="2348011111111")
    staff_actor = ActorContext(
        business_id=biz_id,
        member_id=staff_id,
        role="staff",
        wa_id="2348022223333",
        display_name="Blessing",
    )

    res = await service.invite_staff_member(
        db=db,
        business=biz,
        actor=staff_actor,
        display_name="Emeka",
        phone_number="08033334444",
        whatsapp=whatsapp,
    )

    assert res["success"] is False
    assert "Permission Denied: Only the business owner" in res["error"]
    whatsapp.send_text.assert_not_called()


@pytest.mark.asyncio
async def test_invite_fails_when_seat_limit_reached():
    service = StaffService()
    db = AsyncMock()
    whatsapp = AsyncMock()

    biz_id = uuid4()
    owner_id = uuid4()
    biz = Business(
        id=biz_id,
        name="Lekki Electronics",
        phone_number="2348011111111",
        subscription_plan="free",  # limit = 1
    )
    actor = ActorContext(
        business_id=biz_id,
        member_id=owner_id,
        role="owner",
        wa_id="2348011111111",
        display_name="Chief Obi",
    )

    async def mock_execute(stmt, *args, **kwargs):
        s_str = str(stmt).lower()
        mock_res = MagicMock()
        if "system_settings" in s_str:
            mock_res.scalar.return_value = None
            return mock_res
        if "count" in s_str:
            mock_res.scalar.return_value = 1
            return mock_res
        mock_res.scalar_one_or_none.return_value = None
        return mock_res

    db.execute.side_effect = mock_execute

    res = await service.invite_staff_member(
        db=db,
        business=biz,
        actor=actor,
        display_name="Emeka",
        phone_number="08033334444",
        whatsapp=whatsapp,
    )

    assert res["success"] is False
    assert "Seat limit reached" in res["error"]
    assert "free plan allows up to 1 staff member" in res["error"]
    whatsapp.send_text.assert_not_called()


@pytest.mark.asyncio
async def test_invite_enforces_1_to_1_phone_across_businesses():
    service = StaffService()
    db = AsyncMock()
    whatsapp = AsyncMock()

    biz_a = uuid4()
    biz_b = uuid4()
    owner_a = uuid4()
    biz = Business(id=biz_a, name="Lekki Electronics", phone_number="2348011111111", subscription_plan="pro")
    actor = ActorContext(
        business_id=biz_a,
        member_id=owner_a,
        role="owner",
        wa_id="2348011111111",
        display_name="Chief Obi",
    )

    # Phone already belongs to business B!
    existing_other_biz = Member(
        id=uuid4(),
        business_id=biz_b,
        wa_id="2348099999999",
        display_name="Chinedu",
        role="staff",
        status="active",
    )
    mock_res_existing = MagicMock()
    mock_res_existing.scalar_one_or_none.return_value = existing_other_biz

    db.execute.side_effect = [mock_res_existing]

    res = await service.invite_staff_member(
        db=db,
        business=biz,
        actor=actor,
        display_name="Chinedu",
        phone_number="08099999999",
        whatsapp=whatsapp,
    )

    assert res["success"] is False
    assert "already registered with another business on Waasz" in res["error"]
    whatsapp.send_text.assert_not_called()


@pytest.mark.asyncio
async def test_staff_onboarding_accept_invite():
    service = StaffService()
    db = AsyncMock()
    whatsapp = AsyncMock()

    biz_id = uuid4()
    member_id = uuid4()
    biz = Business(id=biz_id, name="Mega Store", phone_number="2348011111111")
    invited_mem = Member(
        id=member_id,
        business_id=biz_id,
        wa_id="2348022223333",
        display_name="Blessing",
        role="staff",
        status="invited",
    )

    # 1. Member query -> invited_mem
    mock_mem_res = MagicMock()
    mock_mem_res.scalar_one_or_none.return_value = invited_mem

    # 2. Business query -> biz
    mock_biz_res = MagicMock()
    mock_biz_res.scalar_one_or_none.return_value = biz

    # 3. User query -> None (new user account)
    mock_user_res = MagicMock()
    mock_user_res.scalar_one_or_none.return_value = None

    db.execute.side_effect = [
        mock_mem_res,
        mock_biz_res,
        mock_user_res,
    ]

    res = await service.accept_staff_invite(
        db=db,
        phone_number="08022223333",
        whatsapp=whatsapp,
    )

    assert res["success"] is True
    assert invited_mem.status == "active"
    assert invited_mem.consent_accepted_at is not None

    # Check WhatsApp welcome sent to staff and notice sent to owner
    assert whatsapp.send_text.call_count == 2
    calls = whatsapp.send_text.call_args_list

    # Call 1: Welcome to staff
    assert calls[0][0][0] == "2348022223333"
    assert "Welcome to *Mega Store* on Waasz, Blessing!" in calls[0][0][1]

    # Call 2: Notification to business owner
    assert calls[1][0][0] == "2348011111111"
    assert "Blessing (2348022223333) just accepted your invite" in calls[1][0][1]


@pytest.mark.asyncio
async def test_owner_list_and_remove_staff():
    service = StaffService()
    db = AsyncMock()
    whatsapp = AsyncMock()

    biz_id = uuid4()
    owner_id = uuid4()
    staff_id = uuid4()

    biz = Business(id=biz_id, name="Mega Store", phone_number="2348011111111", subscription_plan="pilot")
    owner_actor = ActorContext(
        business_id=biz_id,
        member_id=owner_id,
        role="owner",
        wa_id="2348011111111",
        display_name="Owner",
    )
    staff_actor = ActorContext(
        business_id=biz_id,
        member_id=staff_id,
        role="staff",
        wa_id="2348022223333",
        display_name="Blessing",
    )

    owner_mem = Member(id=owner_id, business_id=biz_id, wa_id="2348011111111", display_name="Owner", role="owner", status="active")
    staff_mem = Member(id=staff_id, business_id=biz_id, wa_id="2348022223333", display_name="Blessing", role="staff", status="active")

    # Staff cannot list staff
    staff_list_res = await service.list_staff_members(db, biz, staff_actor)
    assert staff_list_res["success"] is False
    assert "Permission Denied" in staff_list_res["error"]

    # Owner can list staff
    mock_members = MagicMock()
    mock_members.scalars.return_value.all.return_value = [owner_mem, staff_mem]
    db.execute.side_effect = [mock_members]

    owner_list_res = await service.list_staff_members(db, biz, owner_actor)
    assert owner_list_res["success"] is True
    assert "Team Members for Mega Store" in owner_list_res["formatted_text"]
    assert "Blessing" in owner_list_res["formatted_text"]
    assert "Owner" in owner_list_res["formatted_text"]

    # Staff cannot remove staff
    staff_rm_res = await service.remove_staff_member(db, biz, staff_actor, "Blessing", whatsapp=whatsapp)
    assert staff_rm_res["success"] is False
    assert "Permission Denied" in staff_rm_res["error"]

    # Owner cannot remove the owner
    mock_rm_q1 = MagicMock()
    mock_rm_q1.scalars.return_value.all.return_value = [owner_mem, staff_mem]
    db.execute.side_effect = [mock_rm_q1]
    rm_owner_res = await service.remove_staff_member(db, biz, owner_actor, "Owner", whatsapp=whatsapp)
    assert rm_owner_res["success"] is False
    assert "The business owner cannot be removed" in rm_owner_res["error"]

    # Owner removes staff member successfully
    mock_rm_q2 = MagicMock()
    mock_rm_q2.scalars.return_value.all.return_value = [owner_mem, staff_mem]
    mock_user_q = MagicMock()
    mock_user_q.scalar_one_or_none.return_value = None
    db.execute.side_effect = [mock_rm_q2, mock_user_q]

    rm_staff_res = await service.remove_staff_member(db, biz, owner_actor, "Blessing", whatsapp=whatsapp)
    assert rm_staff_res["success"] is True
    assert staff_mem.status == "removed"
    assert staff_mem.removed_at is not None
    # Check notification sent to staff phone
    whatsapp.send_text.assert_called_with("2348022223333", "ℹ️ Your staff access to *Mega Store* on Waasz has been deactivated by the business owner.")


@pytest.mark.asyncio
async def test_agent_service_tool_execution_multi_staff():
    mock_staff_service = AsyncMock()
    mock_staff_service.invite_staff_member.return_value = {
        "success": True,
        "message": "Staff invitation sent successfully.",
    }
    mock_staff_service.list_staff_members.return_value = {
        "success": True,
        "formatted_text": "Team members: Chief Obi (Owner), Blessing (Staff)",
    }
    mock_staff_service.remove_staff_member.return_value = {
        "success": True,
        "message": "Staff member Blessing removed successfully.",
    }

    agent_service = AgentService(staff=mock_staff_service)
    db = AsyncMock()
    biz = Business(id=uuid4(), name="Test Biz", phone_number="2348011111111")
    user = User(id=uuid4(), business_id=biz.id, phone_number="2348011111111", role="owner", display_name="Chief Obi")
    actor = ActorContext(business_id=biz.id, member_id=user.id, role="owner", wa_id=user.phone_number, display_name="Chief Obi")

    # 1. Execute invite_staff_member
    res_inv, _ = await agent_service._execute_tool(
        db, biz, user, "invite_staff_member",
        {"display_name": "Blessing", "phone_number": "08022223333"},
        "Add staff Blessing 08022223333",
        actor=actor,
    )
    assert "Staff invitation sent successfully" in res_inv
    mock_staff_service.invite_staff_member.assert_called_once()

    # 2. Execute list_staff_members
    res_list, _ = await agent_service._execute_tool(
        db, biz, user, "list_staff_members",
        {},
        "List staff",
        actor=actor,
    )
    assert "Team members: Chief Obi" in res_list
    mock_staff_service.list_staff_members.assert_called_once()

    # 3. Execute remove_staff_member
    res_rm, _ = await agent_service._execute_tool(
        db, biz, user, "remove_staff_member",
        {"identifier": "Blessing"},
        "Remove staff Blessing",
        actor=actor,
    )
    assert "Staff member Blessing removed successfully" in res_rm
    mock_staff_service.remove_staff_member.assert_called_once()

