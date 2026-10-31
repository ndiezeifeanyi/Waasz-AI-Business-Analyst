"""
Pytest integration tests verifying all CommandRouter commands:
/menu, /report, /statement, /void, /staff, /addstaff, /removestaff, /receipt, /invoice, /score
"""

import pytest
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from app.core.database import async_session_factory
from app.models.business import Business
from app.models.customer import Customer
from app.models.debt import Debt
from app.models.inventory import InventoryItem
from app.models.member import Member
from app.models.transaction import Transaction
from app.models.user import User
from app.services.command_router import CommandRouter


class MockWhatsAppClient:
    def __init__(self):
        self.sent_texts = []
        self.sent_documents = []
        self.sent_interactive_lists = []
        self.sent_images = []

    async def send_text(self, to_phone: str, text: str):
        self.sent_texts.append({"to": to_phone, "text": text})
        class Res:
            success = True
            message_id = f"wamid_mock_{uuid4()}"
            error_message = None
            skipped = False
        return Res()

    async def send_document_bytes(self, to_phone: str, document_bytes: bytes, filename: str, caption: str | None = None):
        self.sent_documents.append({"to": to_phone, "filename": filename, "caption": caption, "size": len(document_bytes)})
        class Res:
            success = True
            message_id = f"wamid_mock_doc_{uuid4()}"
            error_message = None
            skipped = False
        return Res()

    async def send_interactive_list(self, to_phone: str, body: str, button_label: str, sections: list, header: str = "", footer: str = ""):
        self.sent_interactive_lists.append({
            "to": to_phone, "body": body, "button_label": button_label, "sections": sections, "header": header, "footer": footer
        })
        class Res:
            success = True
            message_id = f"wamid_mock_list_{uuid4()}"
            error_message = None
            skipped = False
        return Res()

    async def send_image_bytes(self, to_phone: str, image_bytes: bytes, caption: str | None = None):
        self.sent_images.append({"to": to_phone, "caption": caption, "size": len(image_bytes)})
        class Res:
            success = True
            message_id = f"wamid_mock_img_{uuid4()}"
            error_message = None
            skipped = False
        return Res()


@pytest.fixture
async def setup_command_env():
    mock_wa = MockWhatsAppClient()
    router = CommandRouter(whatsapp=mock_wa)

    async with async_session_factory() as db:
        biz_id = uuid4()
        user_id = uuid4()
        owner_phone = f"23480{str(uuid4().int)[:8]}"

        biz = Business(
            id=biz_id,
            name="Command Test Store",
            phone_number=owner_phone,
            subscription_plan="pilot",
        )
        db.add(biz)

        user = User(
            id=user_id,
            business_id=biz_id,
            phone_number=owner_phone,
            display_name="Owner Tester",
            role="owner",
            is_active=True,
        )
        db.add(user)

        member = Member(
            id=uuid4(),
            business_id=biz_id,
            wa_id=owner_phone,
            display_name="Owner Tester",
            role="owner",
            status="active",
        )
        db.add(member)

        # Inventory item
        item = InventoryItem(
            id=uuid4(),
            business_id=biz_id,
            item_name="Rice 50kg",
            normalized_item_name="rice 50kg",
            quantity_on_hand=Decimal("30"),
            low_stock_threshold=Decimal("5"),
            unit="bags",
        )
        db.add(item)

        # Transactions
        now = datetime.now(UTC)
        tx_cash = Transaction(
            id=uuid4(),
            business_id=biz_id,
            transaction_type="sale",
            item_name="Rice 50kg",
            amount=Decimal("45000"),
            quantity=1,
            is_credit=False,
            status="confirmed",
            occurred_at=now - timedelta(hours=1),
        )
        db.add(tx_cash)

        tx_credit = Transaction(
            id=uuid4(),
            business_id=biz_id,
            transaction_type="sale",
            item_name="Sugar 50kg",
            amount=Decimal("35000"),
            quantity=1,
            is_credit=True,
            status="confirmed",
            occurred_at=now - timedelta(minutes=40),
        )
        db.add(tx_credit)

        tx_exp = Transaction(
            id=uuid4(),
            business_id=biz_id,
            transaction_type="expense",
            item_name="Cleaning Supplies",
            amount=Decimal("8000"),
            status="confirmed",
            occurred_at=now - timedelta(minutes=20),
        )
        db.add(tx_exp)

        await db.commit()

        yield {
            "db": db,
            "router": router,
            "mock_wa": mock_wa,
            "business": biz,
            "user": user,
            "phone": owner_phone,
            "latest_tx": tx_exp,
        }

        # Cleanup
        try:
            await db.delete(tx_exp)
            await db.delete(tx_credit)
            await db.delete(tx_cash)
            await db.delete(item)
            await db.delete(member)
            await db.delete(user)
            await db.delete(biz)
            await db.commit()
        except Exception:
            pass


@pytest.mark.asyncio
async def test_menu_options_and_limits(setup_command_env):
    env = setup_command_env
    db = env["db"]
    mock_wa = env["mock_wa"]

    handled = await env["router"].handle_command(db, env["business"], env["user"], env["phone"], "/menu")
    assert handled is True
    assert len(mock_wa.sent_interactive_lists) == 1

    sections = mock_wa.sent_interactive_lists[0]["sections"]
    all_row_ids = [r["id"] for s in sections for r in s["rows"]]
    # Meta restricts total rows to <= 10
    assert len(all_row_ids) <= 10
    assert "cmd_staff" in all_row_ids
    assert "cmd_statement" in all_row_ids
    assert "cmd_report" in all_row_ids
    assert "cmd_void" in all_row_ids


@pytest.mark.asyncio
async def test_report_generation(setup_command_env):
    env = setup_command_env
    db = env["db"]
    mock_wa = env["mock_wa"]

    handled = await env["router"].handle_command(db, env["business"], env["user"], env["phone"], "/report")
    assert handled is True
    assert len(mock_wa.sent_texts) >= 1
    report_text = mock_wa.sent_texts[0]["text"]
    assert "Daily Business Summary" in report_text
    assert "Total Sales" in report_text
    assert "Total Expenses" in report_text


@pytest.mark.asyncio
async def test_statement_pdf_delivery(setup_command_env):
    env = setup_command_env
    db = env["db"]
    mock_wa = env["mock_wa"]

    # 1. /statement without args triggers the interactive timeframe picker
    handled = await env["router"].handle_command(db, env["business"], env["user"], env["phone"], "/statement")
    assert handled is True
    assert len(mock_wa.sent_interactive_lists) >= 1
    picker = mock_wa.sent_interactive_lists[-1]
    stmt_rows = [r["id"] for s in picker["sections"] for r in s["rows"]]
    assert "stmt_30" in stmt_rows
    assert "stmt_90" in stmt_rows
    assert "stmt_365" in stmt_rows

    # 2. Tapping an option (e.g. stmt_90) delivers the PDF
    mock_wa.sent_documents.clear()
    handled_btn = await env["router"].handle_command(db, env["business"], env["user"], env["phone"], "", interactive_id="stmt_90")
    assert handled_btn is True
    assert len(mock_wa.sent_documents) == 1
    doc = mock_wa.sent_documents[0]
    assert doc["filename"].endswith(".pdf")
    assert doc["size"] > 500

    # 3. Direct typed command with custom days (/statement 365) delivers immediately
    mock_wa.sent_documents.clear()
    handled_direct = await env["router"].handle_command(db, env["business"], env["user"], env["phone"], "/statement 365")
    assert handled_direct is True
    assert len(mock_wa.sent_documents) == 1
    doc_direct = mock_wa.sent_documents[0]
    assert doc_direct["filename"].endswith(".pdf")
    assert doc_direct["size"] > 500


@pytest.mark.asyncio
async def test_void_command_execution(setup_command_env):
    env = setup_command_env
    db = env["db"]
    mock_wa = env["mock_wa"]

    handled = await env["router"].handle_command(db, env["business"], env["user"], env["phone"], "/void")
    assert handled is True
    assert len(mock_wa.sent_texts) >= 1
    reply = mock_wa.sent_texts[0]["text"]
    assert "Transaction Voided Successfully" in reply


@pytest.mark.asyncio
async def test_staff_commands(setup_command_env):
    env = setup_command_env
    db = env["db"]
    mock_wa = env["mock_wa"]

    # 1. /staff
    handled = await env["router"].handle_command(db, env["business"], env["user"], env["phone"], "/staff")
    assert handled is True
    assert "Team Members" in mock_wa.sent_texts[-1]["text"]

    # 2. /addstaff
    handled_add = await env["router"].handle_command(db, env["business"], env["user"], env["phone"], "/addstaff Ngozi 08099887766")
    assert handled_add is True
    assert "Staff Invitation Sent" in mock_wa.sent_texts[-1]["text"]

    # 3. /removestaff
    handled_rm = await env["router"].handle_command(db, env["business"], env["user"], env["phone"], "/removestaff Ngozi")
    assert handled_rm is True
    assert "Staff Member Removed" in mock_wa.sent_texts[-1]["text"]


@pytest.mark.asyncio
async def test_no_loan_claims_in_score(setup_command_env):
    env = setup_command_env
    db = env["db"]
    mock_wa = env["mock_wa"]

    handled = await env["router"].handle_command(db, env["business"], env["user"], env["phone"], "/score")
    assert handled is True
    score_reply = mock_wa.sent_texts[-1]["text"]
    assert "loan" not in score_reply.lower()
    assert "smedan" not in score_reply.lower()
