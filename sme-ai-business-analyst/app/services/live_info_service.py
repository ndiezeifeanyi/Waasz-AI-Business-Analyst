"""
Live / Current Information Service backed by Gemini Google Search Grounding.
Executes an isolated, dedicated call directly to Gemini with Google Search Grounding enabled.
"""
from decimal import Decimal
import logging
from uuid import UUID

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.model_resolver import model_resolver
from app.services.cost_monitor import CostMonitor, estimate_tokens

logger = logging.getLogger(__name__)

GROUNDING_FAIL_MESSAGE = "I can't access live information right now. Please try again later."


class LiveInformationService:
    def __init__(self, cost_monitor: CostMonitor | None = None) -> None:
        self.cost_monitor = cost_monitor or CostMonitor()

    async def get_current_information(
        self,
        query: str,
        *,
        db: AsyncSession | None = None,
        business_id: UUID | None = None,
    ) -> str:
        """
        Execute an isolated Google Search grounded request directly to Gemini.
        Returns the grounded answer with citations or an honest degradation message.
        """
        if not settings.gemini_api_key or settings.gemini_api_key.startswith("placeholder"):
            return GROUNDING_FAIL_MESSAGE

        model = model_resolver.get_model("gemini", "chat") or "gemini-3.8-flash"
        clean_model = model.replace("models/", "")
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{clean_model}:generateContent?key={settings.gemini_api_key}"

        payload = {
            "contents": [
                {
                    "parts": [{"text": query}]
                }
            ],
            "tools": [{"google_search": {}}],
        }

        try:
            async with httpx.AsyncClient(timeout=25.0) as client:
                resp = await client.post(url, json=payload)
                if resp.status_code != 200:
                    logger.warning(
                        "Gemini Google Search Grounding returned HTTP %s: %s",
                        resp.status_code,
                        resp.text[:300],
                    )
                    return GROUNDING_FAIL_MESSAGE

                data = resp.json()

            candidates = data.get("candidates") or []
            if not candidates:
                return GROUNDING_FAIL_MESSAGE

            cand = candidates[0]
            content = cand.get("content") or {}
            parts = content.get("parts") or []
            text_result = "".join(p.get("text", "") for p in parts).strip()

            if not text_result:
                return GROUNDING_FAIL_MESSAGE

            # Extract search citations from groundingMetadata if present
            grounding_meta = cand.get("groundingMetadata") or {}
            chunks = grounding_meta.get("groundingChunks") or []
            sources = []
            for c in chunks:
                web = c.get("web") or {}
                uri = web.get("uri")
                title = web.get("title") or uri
                if uri and title:
                    sources.append(f"- [{title}]({uri})")

            final_text = text_result
            if sources:
                unique_sources = list(dict.fromkeys(sources))[:4]
                final_text += "\n\nSources:\n" + "\n".join(unique_sources)

            # Record cost: Google Search grounding standard data access charge $0.035 + token cost
            if db:
                in_tok = estimate_tokens(query)
                out_tok = estimate_tokens(final_text)
                await self.cost_monitor.record_cost(
                    db=db,
                    business_id=business_id,
                    provider="gemini",
                    model=clean_model,
                    operation="grounded_search",
                    input_tokens=in_tok,
                    output_tokens=out_tok,
                    estimated_cost_usd=Decimal("0.0350"),
                )

            return final_text

        except Exception as exc:
            logger.warning("Gemini live grounding call failed (%s: %s)", type(exc).__name__, exc)
            return GROUNDING_FAIL_MESSAGE
