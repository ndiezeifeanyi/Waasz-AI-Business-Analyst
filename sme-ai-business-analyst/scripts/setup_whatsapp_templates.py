"""
Meta WhatsApp Utility Template Generator & Submitter for Automated IOUs
----------------------------------------------------------------------
This script provides the complete configuration and API submission for Waasz's
official automated credit IOU template: `merchant_credit_iou_v1`.

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

TEMPLATE_NAME = "merchant_credit_iou_v1"
CATEGORY = "UTILITY"
LANGUAGE = "en"

# Exact body template complying with Meta Utility rules
TEMPLATE_BODY = (
    "🏛️ WAASZ COMMERCIAL REGISTRY — IOU Ref: #{{1}}\n\n"
    "Dear {{2}},\n"
    "This is an automated confirmation that a credit transaction of {{3}} from {{4}} has been formally registered.\n\n"
    "📅 Due Date: {{5}}\n"
    "💳 Settlement Details:\n{{6}}\n\n"
    "Please quote Ref #{{1}} when completing your transfer to the merchant account above.\n\n"
    "Waasz Commercial Registry — Protecting Verified Retail Trade in Nigeria."
)

SAMPLE_PARAMS = [
    "WZ-53D0E32B",             # {{1}} Ref Code
    "Nmesoma",                 # {{2}} Debtor Name
    "₦300,000",                # {{3}} Amount
    "Ndieze Store",            # {{4}} Business Name
    "Immediate / On Demand",   # {{5}} Due Date
    "Account: Contact merchant for designated bank details", # {{6}} Bank Info
]


def print_template_manual_instructions():
    print("=" * 70)
    print(" META WHATSAPP UTILITY TEMPLATE SPECIFICATION (COPY & PASTE)")
    print("=" * 70)
    print(f"1. Template Name : {TEMPLATE_NAME}")
    print(f"2. Category      : {CATEGORY}")
    print(f"3. Language      : English (en) / English (US) (en_US)")
    print(f"4. Header        : None")
    print("-" * 70)
    print("5. BODY TEXT (Copy below exactly into Meta Business Manager):")
    print("-" * 70)
    print(TEMPLATE_BODY)
    print("-" * 70)
    print("6. SAMPLE VALUES TO SUBMIT:")
    for idx, sample in enumerate(SAMPLE_PARAMS, 1):
        print(f"   - {{{{{idx}}}}} : {sample}")
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
