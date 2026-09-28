"""
WhatsApp Group Chat Support Module.

PLATFORM CONSTRAINT / OBA REQUIREMENT:
WhatsApp Cloud API group messaging strictly requires Meta Official Business Account (OBA)
status (green checkmark badge) and caps groups at 8 participants with no direct-add endpoint.

Current Status: INACTIVE (Ready-to-enable once OBA status is granted).

HOW TO ACTIVATE ONCE OBA IS GRANTED:
1. Verify OBA status via WhatsAppClient.verify_waba_oba_status().
2. Ensure webhook subscription includes 'messages' field with group messaging enabled in Meta App Dashboard.
3. Set ENABLE_GROUP_MESSAGING=true in .env / Cloud Run environment.
"""

import logging
import re
from uuid import UUID

from app.schemas.whatsapp import ParsedWhatsAppMessage
from app.services.whatsapp_client import WhatsAppClient

logger = logging.getLogger(__name__)


class GroupChatHandler:
    def __init__(self, whatsapp: WhatsAppClient | None = None) -> None:
        self.whatsapp = whatsapp or WhatsAppClient()
        self.bot_mentions = ["@assistant", "@bot", "/ai", "@sme", "@ai"]

    def is_bot_mentioned(self, text: str) -> bool:
        """Check if message explicitly mentions the bot."""
        if not text:
            return False
        lower = text.lower()
        return any(mention in lower for mention in self.bot_mentions)

    def extract_clean_prompt(self, text: str) -> str:
        """Strip bot mention handles from text."""
        cleaned = text
        for mention in self.bot_mentions:
            cleaned = re.sub(re.escape(mention), "", cleaned, flags=re.IGNORECASE)
        return cleaned.strip()

    async def is_group_messaging_active(self) -> bool:
        """
        Check if group messaging is enabled in config AND verified with Meta OBA status.
        Only activates when both settings.enable_group_messaging is True and OBA verification succeeds.
        """
        from app.core.config import settings

        if not settings.enable_group_messaging:
            return False

        # Live verification against Meta Graph API
        oba_status = await self.whatsapp.verify_waba_oba_status()
        if not oba_status.get("is_oba_verified", False):
            logger.warning(
                "Group messaging enabled in settings but blocked: WABA account is not Meta OBA verified (%s)",
                oba_status.get("error") or oba_status.get("code_verification_status"),
            )
            return False

        return True

    async def handle_group_message(
        self,
        parsed: ParsedWhatsAppMessage,
        group_id: str,
        sender_phone: str,
    ) -> str | None:
        """
        Process group message respecting privacy rules:
        1. Only respond if group messaging is actively enabled & verified with OBA.
        2. Only respond if explicitly mentioned. Never respond proactively.
        3. Strictly isolate context: NEVER access or reveal the sender's private
           1-on-1 memories, private financial transactions, or private notes.
        """
        if not await self.is_group_messaging_active():
            return None

        body = parsed.body or ""
        if not self.is_bot_mentioned(body):
            # Ignored: bot was not mentioned
            return None

        clean_prompt = self.extract_clean_prompt(body)
        if not clean_prompt:
            return "Hello! How can I assist this group today?"

        # Safe group reply strictly using thread context (no private personal memories)
        logger.info("Processing group mention in group %s from %s", group_id, sender_phone)
        return f"Group Assistant: I received '{clean_prompt}'. Note: Group messaging is active."
