"""
Template Service for Waasz Consent-Based Debt Recovery.
Defines Utility templates conforming to Meta's WhatsApp Business Messaging Policy.
Rules:
- Utility category
- No bank details or PDF attachments in template messages (live on secure details page)
- Do not start or end body with a variable
- Dynamic URL button to https://waaszai.com/r/{{1}}
- Quick reply buttons with opaque signed tokens
- Footer opt-out on all templates: "Reply STOP to stop these messages."
"""

import logging
from typing import Any
import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

FOOTER_TEXT = "Reply STOP to stop these messages."
BASE_DETAILS_URL = "https://waaszai.com/r/{{1}}"

TEMPLATES_V2: dict[str, dict[str, Any]] = {
    "iou_record_v2": {
        "name": "iou_record_v2",
        "category": "UTILITY",
        "language": "en",
        "body": "Hello {{1}}, {{2}} recorded a purchase on credit for you: {{3}}, on {{4}}. It is due on {{5}}. Tap below to see the details.",
        "sample_body": ["Emeka", "Mama Chinedu Stores", "2 bags of rice (₦50,000)", "05 Nov 2026", "20 Nov 2026"],
        "footer": FOOTER_TEXT,
        "buttons": [
            {"type": "URL", "text": "View details", "url": BASE_DETAILS_URL, "sample": ["token_sample_abc123"]},
            {"type": "QUICK_REPLY", "text": "This is not me"},
        ],
    },
    "iou_due_soon_v2": {
        "name": "iou_due_soon_v2",
        "category": "UTILITY",
        "language": "en",
        "body": "Hello {{1}}, a reminder from {{2}}. Your balance of {{3}} is due on {{4}}. If you have already paid, thank you.",
        "sample_body": ["Emeka", "Mama Chinedu Stores", "₦50,000", "20 Nov 2026"],
        "footer": FOOTER_TEXT,
        "buttons": [
            {"type": "URL", "text": "View details", "url": BASE_DETAILS_URL, "sample": ["token_sample_abc123"]},
            {"type": "QUICK_REPLY", "text": "I have paid"},
            {"type": "QUICK_REPLY", "text": "Need more time"},
        ],
    },
    "iou_due_today_v2": {
        "name": "iou_due_today_v2",
        "category": "UTILITY",
        "language": "en",
        "body": "Hello {{1}}, {{2}} is reminding you that {{3}} is due today.",
        "sample_body": ["Emeka", "Mama Chinedu Stores", "₦50,000"],
        "footer": FOOTER_TEXT,
        "buttons": [
            {"type": "URL", "text": "View details", "url": BASE_DETAILS_URL, "sample": ["token_sample_abc123"]},
            {"type": "QUICK_REPLY", "text": "I have paid"},
            {"type": "QUICK_REPLY", "text": "Need more time"},
        ],
    },
    "iou_overdue_v2": {
        "name": "iou_overdue_v2",
        "category": "UTILITY",
        "language": "en",
        "body": "Hello {{1}}, {{2}} has not yet received {{3}}, which was due on {{4}}. If you have paid, please tap below. If not, you can tell us when you will.",
        "sample_body": ["Emeka", "Mama Chinedu Stores", "₦50,000", "20 Nov 2026"],
        "footer": FOOTER_TEXT,
        "buttons": [
            {"type": "URL", "text": "View details", "url": BASE_DETAILS_URL, "sample": ["token_sample_abc123"]},
            {"type": "QUICK_REPLY", "text": "I have paid"},
            {"type": "QUICK_REPLY", "text": "Choose a date"},
        ],
    },
    "iou_final_notice_v2": {
        "name": "iou_final_notice_v2",
        "category": "UTILITY",
        "language": "en",
        "body": "Hello {{1}}, {{2}} has still not received {{3}}, due on {{4}}. If we do not hear from you by {{5}}, your credit account will be paused. If you have paid, please tap below.",
        "sample_body": ["Emeka", "Mama Chinedu Stores", "₦50,000", "20 Nov 2026", "25 Nov 2026"],
        "footer": FOOTER_TEXT,
        "buttons": [
            {"type": "URL", "text": "View details", "url": BASE_DETAILS_URL, "sample": ["token_sample_abc123"]},
            {"type": "QUICK_REPLY", "text": "I have paid"},
            {"type": "QUICK_REPLY", "text": "Choose a date"},
        ],
    },
    "iou_final_notice_soft_v2": {
        "name": "iou_final_notice_soft_v2",
        "category": "UTILITY",
        "language": "en",
        "body": "Hello {{1}}, {{2}} has still not received {{3}}, due on {{4}}. Please tap below to confirm payment or let us know when you will settle.",
        "sample_body": ["Emeka", "Mama Chinedu Stores", "₦50,000", "20 Nov 2026"],
        "footer": FOOTER_TEXT,
        "buttons": [
            {"type": "URL", "text": "View details", "url": BASE_DETAILS_URL, "sample": ["token_sample_abc123"]},
            {"type": "QUICK_REPLY", "text": "I have paid"},
            {"type": "QUICK_REPLY", "text": "Choose a date"},
        ],
    },
    "iou_paid_v2": {
        "name": "iou_paid_v2",
        "category": "UTILITY",
        "language": "en",
        "body": "Thank you, {{1}}. {{2}} has received your payment of {{3}}. Your balance is now {{4}}. Tap below to view your receipt.",
        "sample_body": ["Emeka", "Mama Chinedu Stores", "₦50,000", "₦0.00"],
        "footer": FOOTER_TEXT,
        "buttons": [
            {"type": "URL", "text": "View details", "url": BASE_DETAILS_URL, "sample": ["token_sample_abc123"]},
        ],
    },
    "merchant_escalation_v1": {
        "name": "merchant_escalation_v1",
        "category": "UTILITY",
        "language": "en",
        "body": "Hello {{1}}, debt {{2}} for customer {{3}} ({{4}}) is now 14 days overdue without payment. Automated reminders have stopped. Please review recovery options.",
        "sample_body": ["Mama Chinedu Stores", "WZ-001", "Emeka", "₦50,000"],
        "footer": FOOTER_TEXT,
        "buttons": [
            {"type": "URL", "text": "View details", "url": BASE_DETAILS_URL, "sample": ["token_sample_abc123"]},
        ],
    },
    "merchant_payment_alert_v1": {
        "name": "merchant_payment_alert_v1",
        "category": "UTILITY",
        "language": "en",
        "body": "Hello {{1}}, customer {{2}} has reported making a payment of {{3}} for credit record {{4}}. Please verify receipt in your account and confirm.",
        "sample_body": ["Mama Chinedu Stores", "Emeka", "₦50,000", "WZ-001"],
        "footer": FOOTER_TEXT,
        "buttons": [
            {"type": "URL", "text": "View details", "url": BASE_DETAILS_URL, "sample": ["token_sample_abc123"]},
        ],
    },
}


class TemplateService:
    """Manages template specifications and API registration with Meta."""

    def build_meta_payload(self, template_key: str) -> dict[str, Any]:
        """Builds Meta Graph API template creation payload."""
        tmpl = TEMPLATES_V2.get(template_key)
        if not tmpl:
            raise ValueError(f"Unknown template key: {template_key}")

        components: list[dict[str, Any]] = [
            {
                "type": "BODY",
                "text": tmpl["body"],
                "example": {
                    "body_text": [tmpl["sample_body"]]
                },
            },
            {
                "type": "FOOTER",
                "text": tmpl["footer"],
            },
        ]

        buttons = []
        for b in tmpl.get("buttons", []):
            if b["type"] == "URL":
                buttons.append({
                    "type": "URL",
                    "text": b["text"],
                    "url": b["url"],
                    "example": b.get("sample", ["sample_token"]),
                })
            elif b["type"] == "QUICK_REPLY":
                buttons.append({
                    "type": "QUICK_REPLY",
                    "text": b["text"],
                })

        if buttons:
            components.append({
                "type": "BUTTONS",
                "buttons": buttons,
            })

        return {
            "name": tmpl["name"],
            "category": tmpl["category"],
            "language": tmpl["language"],
            "components": components,
        }

    async def submit_template_to_meta(self, template_key: str) -> dict[str, Any]:
        """Submits a template definition to Meta via the Graph API."""
        waba_id = getattr(settings, "whatsapp_business_account_id", None)
        token = getattr(settings, "whatsapp_access_token", None)
        version = getattr(settings, "whatsapp_api_version", "v21.0")

        if not waba_id or not token:
            logger.warning("WhatsApp credentials not set; skipping live Meta template submission.")
            return {"status": "skipped", "reason": "unconfigured"}

        payload = self.build_meta_payload(template_key)
        url = f"https://graph.facebook.com/{version}/{waba_id}/message_templates"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

        async with httpx.AsyncClient(timeout=20.0) as client:
            try:
                res = await client.post(url, headers=headers, json=payload)
                data = res.json()
                if res.status_code in (200, 201):
                    logger.info("Successfully submitted template %s to Meta: %s", template_key, data)
                    return {"status": "submitted", "data": data}
                else:
                    logger.warning("Meta template submission failed for %s (%s): %s", template_key, res.status_code, data)
                    return {"status": "error", "code": res.status_code, "error": data}
            except Exception as exc:
                logger.exception("Failed to submit template to Meta")
                return {"status": "exception", "error": str(exc)}
