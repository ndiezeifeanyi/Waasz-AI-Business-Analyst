import asyncio
import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# Ensure root is in sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))
load_dotenv(BASE_DIR / ".env")

from app.core.config import settings
from app.services.whatsapp_client import WhatsAppClient

async def test_iou_dispatch():
    client = WhatsAppClient()
    test_phone = "2349155716041" # User's live WhatsApp phone

    print(f"Testing live template dispatch to {test_phone}...")
    print(f"Phone ID: {settings.whatsapp_phone_number_id}")
    print(f"API Version: {settings.whatsapp_api_version}")

    # Standard components for iou_record_v2
    debtor_name = "Chidi Okafor"
    biz_name = "Ndieze Store"
    item_and_amt = "2 bags of rice (₦45,000)"
    sale_date = "05 Nov 2026"
    due_date = "20 Nov 2026"
    sample_token = "token_sample_abc123"

    components = [
        {
            "type": "body",
            "parameters": [
                {"type": "text", "text": debtor_name},
                {"type": "text", "text": biz_name},
                {"type": "text", "text": item_and_amt},
                {"type": "text", "text": sale_date},
                {"type": "text", "text": due_date},
            ],
        },
        {
            "type": "button",
            "sub_type": "url",
            "index": "0",
            "parameters": [
                {"type": "text", "text": sample_token}
            ],
        },
    ]

    # Test v2 utility template names
    candidate_templates = [
        "iou_record_v2",
        "iou_due_soon_v2",
        "iou_due_today_v2",
        "iou_overdue_v2",
        "iou_final_notice_v2",
        "iou_paid_v2",
    ]

    for tmpl in candidate_templates:
        for lang in ["en", "en_US"]:
            print(f"\nAttempting template='{tmpl}', language='{lang}'...")
            res = await client.send_template(
                to_phone=test_phone,
                template_name=tmpl,
                language_code=lang,
                components=components,
            )
            print(f"Result: success={res.success}, message_id={res.message_id}, error={res.error_message}")
            if res.success:
                print(f"\n[SUCCESS] Template '{tmpl}' ({lang}) accepted by Meta! Message ID: {res.message_id}")
                return

if __name__ == "__main__":
    asyncio.run(test_iou_dispatch())
