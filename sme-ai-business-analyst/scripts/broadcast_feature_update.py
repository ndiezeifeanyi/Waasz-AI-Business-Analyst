import asyncio
import argparse
from datetime import datetime, timezone
import logging
import time

from app.core.database import async_session_factory
from sqlalchemy import text
from app.services.whatsapp_client import WhatsAppClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("waasz_broadcast")

MESSAGE = """Dear Valued Merchant, 👋

Thank you for being part of the Waasz AI business pilot. We are constantly working to make managing your store faster, protecting your working capital, and getting your business ready for bank loans.

We have rolled out four powerful new upgrades to your Waasz WhatsApp assistant today:

1️⃣ ⚖️ Autonomous Debt Recovery & Digital IOUs
When a customer buys on credit, you no longer have to chase them awkwardly. Simply include their phone number when logging the credit sale:
👉 Example: "Emeka bought 2 bags of rice ₦140,000 to pay Friday 08012345678"
Waasz will automatically issue a formal Digital IOU directly to their WhatsApp with your bank account details for direct settlement.

2️⃣ 🏆 Your Live Waasz Credit Score (300–850)
Every day you record sales, expenses, and credit collections, you build your financial score.
👉 How to check: Just text "credit score" or "my score" to see your current rating and your 30-day active record streak.

3️⃣ 📄 Bank-Ready Business Health Statement (PDF)
Need a loan or applying for SMEDAN/government business grants? You no longer need an expensive accountant.
👉 How to get it: Text "health profile" or "statement". Waasz will instantly generate a certified, 2-page audited financial profile PDF with a verification QR code ready for bank loan officers.

4️⃣ 🎙️ Voice Notes & Receipt Photos (Zero Typing)
Too busy to type during market rush? 
👉 Just press and hold the mic to send a voice note in Nigerian Pidgin or English, or snap a photo of your paper sales ledger. Waasz records it all automatically.

Everything works right here inside WhatsApp—no new apps to install or update.

Try texting "credit score" or logging your latest sale today to test it out! 🚀

— The Waasz AI Team"""


async def get_active_merchants():
    async with async_session_factory() as db:
        res = await db.execute(
            text(
                "SELECT id, name, phone_number FROM businesses "
                "WHERE deleted_at IS NULL AND onboarding_status = 'active' "
                "ORDER BY created_at ASC"
            )
        )
        return res.fetchall()


async def run_broadcast(dry_run: bool = False):
    merchants = await get_active_merchants()
    logger.info(f"Loaded {len(merchants)} active merchants for feature broadcast.")
    
    client = WhatsAppClient()
    success_count = 0
    fail_count = 0
    
    for biz in merchants:
        biz_id, biz_name, phone = biz
        logger.info(f"Target: {biz_name} ({phone})")
        
        if dry_run:
            logger.info(f"[DRY RUN] Would send to {phone}")
            success_count += 1
            continue
            
        try:
            res = await client.send_text(to_phone=phone, body=MESSAGE)
            if res.success:
                logger.info(f"✅ Successfully sent to {biz_name} ({phone}) - MsgId: {res.message_id}")
                success_count += 1
            else:
                logger.error(f"❌ Failed to send to {biz_name} ({phone}): {res.error_message}")
                fail_count += 1
        except Exception as e:
            logger.exception(f"❌ Exception sending to {biz_name} ({phone}): {e}")
            fail_count += 1
            
        # Brief pause between dispatches to comply with Meta rate limits
        await asyncio.sleep(1.2)
        
    logger.info(f"Broadcast complete! Success: {success_count}, Failed: {fail_count}, Total: {len(merchants)}")


def wait_until_target(target_utc_hour: int = 19, target_utc_minute: int = 0):
    """Waits until target UTC time (8:00 PM WAT = 19:00 UTC)."""
    while True:
        now = datetime.now(timezone.utc)
        if now.hour > target_utc_hour or (now.hour == target_utc_hour and now.minute >= target_utc_minute):
            logger.info(f"Current time {now.strftime('%H:%M:%S UTC')} has reached or passed target {target_utc_hour:02d}:{target_utc_minute:02d} UTC. Triggering now!")
            break
        remaining_seconds = (datetime(now.year, now.month, now.day, target_utc_hour, target_utc_minute, 0, tzinfo=timezone.utc) - now).total_seconds()
        logger.info(f"Waiting for 8:00 PM WAT ({target_utc_hour:02d}:{target_utc_minute:02d} UTC)... {int(remaining_seconds)}s remaining ({int(remaining_seconds / 60)} mins).")
        # Sleep up to 60s at a time
        sleep_chunk = min(60, max(5, int(remaining_seconds)))
        time.sleep(sleep_chunk)


def main():
    parser = argparse.ArgumentParser(description="Broadcast feature update to Waasz merchants")
    parser.add_argument("--dry-run", action="store_true", help="Simulate without sending actual WhatsApp messages")
    parser.add_argument("--wait-until-8pm", action="store_true", help="Sleep until 8:00 PM WAT (19:00 UTC) before sending")
    args = parser.parse_args()
    
    if args.wait_until_8pm:
        logger.info("Scheduler enabled: Waiting until 8:00 PM WAT (19:00:00 UTC)...")
        wait_until_target(19, 0)
        
    asyncio.run(run_broadcast(dry_run=args.dry_run))


if __name__ == "__main__":
    main()
