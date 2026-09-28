#!/usr/bin/env python3
"""
Test runner for live inbound WhatsApp webhook processing.
Executes test messages through the full live pipeline (Agent, ImageService, LiveInfo, WhatsApp client).
"""
import asyncio
from datetime import datetime, UTC
from pathlib import Path
import sys
from uuid import uuid4

# Add project root to Python search path so imports resolve cleanly across IDEs and terminals
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.database import async_session_factory
from app.schemas.whatsapp import ParsedWhatsAppMessage
from app.services.webhook_processor import WhatsAppWebhookProcessor


async def run_live_tests(target_phone: str = "2347065015924") -> None:
    processor = WhatsAppWebhookProcessor()
    
    # 1. Test live information tool via inbound webhook
    print(f"\n--- TEST 1: Live Information Query ({target_phone}) ---")
    msg1 = ParsedWhatsAppMessage(
        message_id=f"wam_test_live_{uuid4().hex[:10]}",
        from_phone=target_phone,
        to_phone_id="phone_id_test",
        timestamp=datetime.now(UTC),
        message_type="text",
        body="What happened in the news today?",
    )
    async with async_session_factory() as session:
        await processor.process_message(session, msg1, None)
        await session.commit()
    print("Test 1 completed.")

    # 2. Test image generation tool via inbound webhook
    print(f"\n--- TEST 2: Image Generation Request ({target_phone}) ---")
    msg2 = ParsedWhatsAppMessage(
        message_id=f"wam_test_img_{uuid4().hex[:10]}",
        from_phone=target_phone,
        to_phone_id="phone_id_test",
        timestamp=datetime.now(UTC),
        message_type="text",
        body="Generate an image of a basket of fresh bread",
    )
    async with async_session_factory() as session:
        await processor.process_message(session, msg2, None)
        await session.commit()
    print("Test 2 completed.")


if __name__ == "__main__":
    phone = sys.argv[1] if len(sys.argv) > 1 else "2347065015924"
    asyncio.run(run_live_tests(phone))
