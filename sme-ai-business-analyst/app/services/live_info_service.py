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

GROUNDING_FAIL_MESSAGE = "I can't access live information for that right now. Currently, my real-time updates cover live exchange rates and major news headlines."


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
                        "Gemini Google Search Grounding returned HTTP %s: %s (attempting resilient fallback)",
                        resp.status_code,
                        resp.text[:300],
                    )
                    return await self._fallback_live_search(query, db=db, business_id=business_id)

                data = resp.json()

            candidates = data.get("candidates") or []
            if not candidates:
                return await self._fallback_live_search(query, db=db, business_id=business_id)

            cand = candidates[0]
            content = cand.get("content") or {}
            parts = content.get("parts") or []
            text_result = "".join(p.get("text", "") for p in parts).strip()

            if not text_result:
                return await self._fallback_live_search(query, db=db, business_id=business_id)

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
            logger.warning("Gemini live grounding call failed (%s: %s) - attempting fallback", type(exc).__name__, exc)
            return await self._fallback_live_search(query, db=db, business_id=business_id)

    async def _fallback_live_search(
        self,
        query: str,
        *,
        db: AsyncSession | None = None,
        business_id: UUID | None = None,
    ) -> str:
        """
        Resilient live search fallback when Google Search Grounding quota is exhausted.
        Fetches live data from public real-time APIs (forex/currency rates, Google News RSS),
        then synthesizes a factual, grounded answer with citations using normal LLM chat inference.
        """
        import urllib.parse
        import xml.etree.ElementTree as ET

        lower_query = query.lower()
        live_facts = ""
        sources = []

        # 1. Check if currency / exchange rate query
        forex_keywords = ("rate", "exchange", "usd", "ngn", "naira", "dollar", "currency", "forex", "gbp", "pounds", "euro", "eur", "cbn")
        if any(kw in lower_query for kw in forex_keywords):
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    f_resp = await client.get("https://open.er-api.com/v6/latest/USD")
                    if f_resp.status_code == 200:
                        data = f_resp.json()
                        rates = data.get("rates", {})
                        time_last = data.get("time_last_update_utc", "today")
                        ngn = rates.get("NGN")
                        eur = rates.get("EUR")
                        gbp = rates.get("GBP")
                        ghs = rates.get("GHS")
                        live_facts = (
                            f"Live Exchange Rates (Base USD) as of {time_last}:\n"
                            f"- 1 USD = ₦{ngn:,.2f} NGN\n"
                            f"- 1 USD = €{eur:.4f} EUR\n"
                            f"- 1 USD = £{gbp:.4f} GBP\n"
                            f"- 1 USD = {ghs:.2f} GHS\n"
                        )
                        if ngn and gbp:
                            live_facts += f"- 1 GBP ≈ ₦{(ngn / gbp):,.2f} NGN\n"
                        if ngn and eur:
                            live_facts += f"- 1 EUR ≈ ₦{(ngn / eur):,.2f} NGN\n"
                        sources.append("- [Open Exchange Rates API](https://open.er-api.com)")
            except Exception as e:
                logger.warning("Live forex fetch failed: %s", e)

        # 2. If not forex or forex fetch didn't yield text, query Google News RSS
        if not live_facts:
            try:
                encoded_query = urllib.parse.quote(query)
                rss_url = f"https://news.google.com/rss/search?q={encoded_query}&hl=en-NG&gl=NG&ceid=NG:en"
                async with httpx.AsyncClient(timeout=10.0) as client:
                    n_resp = await client.get(rss_url)
                    if n_resp.status_code == 200:
                        root = ET.fromstring(n_resp.text)
                        items = root.findall(".//item")[:4]
                        headlines = []
                        for it in items:
                            title = it.findtext("title")
                            pub = it.findtext("pubDate")
                            link = it.findtext("link")
                            if title:
                                headlines.append(f"- {title} ({pub or ''})")
                                if link:
                                    sources.append(f"- [{title[:50]}...]({link})")
                        if headlines:
                            live_facts = "Real-time News Headlines:\n" + "\n".join(headlines)
            except Exception as e:
                logger.warning("Live news RSS fetch failed: %s", e)

        if not live_facts:
            return GROUNDING_FAIL_MESSAGE

        # 3. Synthesize factual response via normal LLM inference (which does NOT require Google Search Grounding quota)
        try:
            from langchain_core.messages import HumanMessage, SystemMessage
            from app.services.ai_client import AiClient
            ai_client = AiClient(cost_monitor=self.cost_monitor)
            messages = [
                SystemMessage(
                    content=(
                        "You are a factual information assistant. Answer the user's question accurately and concisely using ONLY "
                        "the real-time live data provided below. Keep your answer brief, direct, and suitable for WhatsApp (1-3 sentences). "
                        "State the current numbers/facts clearly. Do not invent or extrapolate beyond what is in the data."
                    )
                ),
                HumanMessage(
                    content=f"Live Data:\n{live_facts}\n\nUser Question: {query}"
                ),
            ]
            agent_res = await ai_client.invoke_agent(
                messages=messages,
                tools=None,
                db=db,
                business_id=business_id,
                operation="fallback_live_search",
            )
            ans_text = ""
            if agent_res and agent_res.message:
                ans_text = str(getattr(agent_res.message, "content", "") or "").strip()
            if not ans_text:
                return f"Current live data:\n{live_facts}"

            if sources:
                unique_sources = list(dict.fromkeys(sources))[:3]
                ans_text += "\n\nSources:\n" + "\n".join(unique_sources)
            return ans_text
        except Exception as exc:
            logger.warning("LLM synthesis for fallback live search failed: %s", exc)
            return f"Current live data:\n{live_facts}"
