"""
Meta WhatsApp Utility Template Generator & Submitter for Automated IOUs
----------------------------------------------------------------------
This script provides the complete configuration and API submission for Waasz's
official automated credit IOU templates (v2 suite, starting with `iou_record_v2`).

Category: UTILITY (Ensures 24/7 delivery outside Meta's 24-hour customer window)
"""

import asyncio
import os
import sys
import httpx
sys.stdout.reconfigure(encoding='utf-8')
from dotenv import load_dotenv

# Ensure root is in sys.path
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)
load_dotenv(os.path.join(BASE_DIR, ".env"))

from app.core.config import settings
from app.services.template_service import TEMPLATES_V2, TemplateService

TEMPLATE_NAME = "iou_record_v2"
CATEGORY = "UTILITY"
LANGUAGE = "en"


def print_template_manual_instructions():
    print("=" * 70)
    print(" META WHATSAPP UTILITY TEMPLATES (V2) SPECIFICATION")
    print("=" * 70)
    for k, t in TEMPLATES_V2.items():
        print(f"\n--- {t['name']} ({t['category']}) ---")
        print(f"Body: {t['body']}")
        print(f"Footer: {t['footer']}")
        print(f"Sample: {t['sample_body']}")
    print("=" * 70)


async def check_or_create_template_via_api():
    if not settings.whatsapp_access_token or not settings.whatsapp_phone_number_id:
        print("❌ Error: WHATSAPP_ACCESS_TOKEN or WHATSAPP_PHONE_NUMBER_ID is missing from .env.")
        return

    print_template_manual_instructions()

    # Step 1: Query phone number details to discover WABA ID
    print("\n🔍 Querying Meta Graph API for WhatsApp Business Account...")
    headers = {"Authorization": f"Bearer {settings.whatsapp_access_token}"}
    async with httpx.AsyncClient(timeout=20) as client:
        phone_url = f"https://graph.facebook.com/{settings.whatsapp_api_version}/{settings.whatsapp_phone_number_id}?fields=id,display_phone_number,verified_name"
        p_res = await client.get(phone_url, headers=headers)
        if p_res.status_code == 200:
            print(f"✅ Active Phone: {p_res.json()}")
        else:
            print(f"⚠️ Could not inspect phone: {p_res.text}")


if __name__ == "__main__":
    asyncio.run(check_or_create_template_via_api())
