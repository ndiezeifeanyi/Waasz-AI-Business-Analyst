import json
import logging
import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from uuid import UUID

from langchain_core.messages import HumanMessage, SystemMessage
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import CostLimitExceeded
from app.core.model_resolver import Capability, is_model_not_found_error, model_resolver
from app.models.business import Business
from app.services.cost_monitor import CostMonitor, estimate_cost_usd, estimate_tokens

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ProviderConfig:
    provider: str
    model: str
    api_key: str


@dataclass(frozen=True)
class AiJsonResult:
    provider: str
    model: str
    content: dict
    input_tokens: int
    output_tokens: int
    estimated_cost_usd: Decimal


@dataclass(frozen=True)
class AiAgentResult:
    provider: str
    model: str
    message: Any
    input_tokens: int
    output_tokens: int
    estimated_cost_usd: Decimal


class AiClient:
    """LangChain-backed provider chain: OpenAI, Groq, then Gemini with dynamic model resolution and credit-balancing."""

    _chat_counter: int = 0

    def __init__(self, cost_monitor: CostMonitor | None = None) -> None:
        self.cost_monitor = cost_monitor or CostMonitor()

    def provider_chain(
        self, capability: Capability = "chat", operation: str = "chat"
    ) -> list[ProviderConfig]:
        """
        Builds the provider fallback chain with smart credit-balancing protection.
        Default priority order: OpenAI -> Groq -> Gemini.

        Balancing & Credit Conservation:
        1. Multimodal Vision: Groq lacks tool-enabled vision; routes OpenAI -> Gemini.
        2. Utility / JSON operations (e.g. complete_json, extraction, classification):
           Prioritizes Groq -> OpenAI -> Gemini so routine background tasks do not
           burn paid OpenAI credits, keeping the OpenAI balance reserved for users.
        3. Conversational User Chat ('agent_chat', 'chat'):
           - If ai_balance_mode == 'smart_balanced': Balances between OpenAI and Groq
             (1:1 round-robin), cutting OpenAI burn rate by ~50% while guaranteeing that if either
             provider hits rate limits/errors, the other steps in immediately as fallback,
             with Gemini as tertiary safety net.
           - If ai_balance_mode == 'openai_first': Strictly puts OpenAI first: OpenAI -> Groq -> Gemini.
        """
        openai_configs: list[ProviderConfig] = []
        if settings.openai_api_key and settings.openai_api_key != "placeholder_openai_key":
            models = model_resolver.get_candidate_models("openai", capability)
            if not models:
                primary = model_resolver.get_model("openai", capability)
                if primary:
                    models = [primary]
            for m in models:
                openai_configs.append(ProviderConfig("openai", m, settings.openai_api_key))

        groq_configs: list[ProviderConfig] = []
        if settings.groq_api_key and settings.groq_api_key != "placeholder_groq_key":
            groq_model = model_resolver.get_model("groq", capability)
            if groq_model:
                groq_configs.append(ProviderConfig("groq", groq_model, settings.groq_api_key))

        gemini_configs: list[ProviderConfig] = []
        if settings.gemini_api_key and settings.gemini_api_key != "placeholder_gemini_key":
            models = model_resolver.get_candidate_models("gemini", capability)
            if not models:
                primary = model_resolver.get_model("gemini", capability)
                if primary:
                    models = [primary]
            for m in models:
                gemini_configs.append(ProviderConfig("gemini", m, settings.gemini_api_key))

        provider_map = {
            "openai": openai_configs,
            "groq": groq_configs,
            "gemini": gemini_configs,
        }

        # 1. Vision Capability (Groq lacks vision tool-calling)
        if capability == "vision":
            return openai_configs + gemini_configs + groq_configs

        # 2. Utility / JSON Extraction tasks -> Preserve paid credits via Groq first
        is_utility_task = operation in (
            "complete_json",
            "extract_record",
            "classify_intent",
            "qa_intent",
            "fallback_live_search",
            "niche_analysis",
            "historical_summary",
        )
        if is_utility_task and groq_configs:
            return groq_configs + openai_configs + gemini_configs

        # 3. Conversational Chat / General Operations
        balance_mode = getattr(settings, "ai_balance_mode", "smart_balanced").lower()

        if balance_mode == "smart_balanced" and openai_configs and groq_configs:
            AiClient._chat_counter += 1
            if AiClient._chat_counter % 2 == 1:
                # Turn A: OpenAI primary, Groq fallback, Gemini tertiary
                return openai_configs + groq_configs + gemini_configs
            else:
                # Turn B: Groq primary, OpenAI fallback, Gemini tertiary
                return groq_configs + openai_configs + gemini_configs

        # 4. Standard / Configured Order (default: OpenAI -> Groq -> Gemini)
        configured_order = [
            p.strip().lower()
            for p in getattr(settings, "ai_provider_order", "openai,groq,gemini").split(",")
            if p.strip().lower() in provider_map
        ]
        if not configured_order:
            configured_order = ["openai", "groq", "gemini"]

        ordered: list[ProviderConfig] = []
        for p in configured_order:
            ordered.extend(provider_map.get(p, []))
        return ordered

    async def complete_json(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        operation: str,
        db: AsyncSession | None = None,
        business_id: UUID | None = None,
    ) -> AiJsonResult | None:
        providers = self.provider_chain(capability="chat", operation=operation)
        if not providers:
            return None

        for provider in providers:
            current_provider = provider
            input_text = f"{system_prompt}\n\n{user_prompt}"
            estimated_cost = estimate_cost_usd(current_provider.provider, input_text)
            try:
                if db and business_id:
                    business = await self._get_business(db, business_id)
                    if business:
                        await self.cost_monitor.ensure_business_daily_budget(
                            db, business, estimated_cost
                        )
                try:
                    content_text = await self._call_provider(current_provider, system_prompt, user_prompt)
                except Exception as call_exc:
                    # Check for mid-run 404 (model deprecated/retired) to self-heal
                    if is_model_not_found_error(call_exc):
                        did_heal, new_model = await model_resolver.handle_mid_run_failure(
                            current_provider.provider, "chat", call_exc
                        )
                        if did_heal:
                            current_provider = ProviderConfig(
                                current_provider.provider, new_model, current_provider.api_key
                            )
                            content_text = await self._call_provider(
                                current_provider, system_prompt, user_prompt
                            )
                        else:
                            raise call_exc
                    else:
                        raise call_exc

                parsed = _parse_json_object(content_text)
                output_tokens = estimate_tokens(content_text)
                actual_cost = estimate_cost_usd(current_provider.provider, input_text, content_text)
                if db:
                    await self.cost_monitor.record_cost(
                        db=db,
                        business_id=business_id,
                        provider=current_provider.provider,
                        model=current_provider.model,
                        operation=operation,
                        input_tokens=estimate_tokens(input_text),
                        output_tokens=output_tokens,
                        estimated_cost_usd=actual_cost,
                    )
                return AiJsonResult(
                    provider=current_provider.provider,
                    model=current_provider.model,
                    content=parsed,
                    input_tokens=estimate_tokens(input_text),
                    output_tokens=output_tokens,
                    estimated_cost_usd=actual_cost,
                )
            except CostLimitExceeded:
                raise
            except Exception as exc:
                logger.warning(
                    "AI provider %s (%s) failed (%s: %s); cascading to next provider in fallback chain.",
                    current_provider.provider, current_provider.model, type(exc).__name__, exc
                )
        return None

    async def invoke_agent(
        self,
        messages: list[Any],
        tools: list[Any] | None = None,
        *,
        operation: str = "agent_chat",
        db: AsyncSession | None = None,
        business_id: UUID | None = None,
    ) -> AiAgentResult | None:
        has_images = any(
            isinstance(getattr(m, "content", ""), list)
            for m in messages
        )
        capability: Capability = "vision" if has_images else "chat"
        providers = self.provider_chain(capability=capability, operation=operation)
        if not providers:
            return None

        prompt_repr = " ".join(
            str(getattr(m, "content", "")) for m in messages if isinstance(getattr(m, "content", ""), str)
        )

        for provider in providers:
            current_provider = provider
            estimated_cost = estimate_cost_usd(current_provider.provider, prompt_repr)
            try:
                if db and business_id:
                    business = await self._get_business(db, business_id)
                    if business:
                        await self.cost_monitor.ensure_business_daily_budget(
                            db, business, estimated_cost
                        )
                try:
                    response_msg = await self._call_agent_provider(current_provider, messages, tools)
                except Exception as call_exc:
                    if is_model_not_found_error(call_exc):
                        did_heal, new_model = await model_resolver.handle_mid_run_failure(
                            current_provider.provider, capability, call_exc
                        )
                        if did_heal:
                            current_provider = ProviderConfig(
                                current_provider.provider, new_model, current_provider.api_key
                            )
                            response_msg = await self._call_agent_provider(current_provider, messages, tools)
                        else:
                            raise call_exc
                    else:
                        raise call_exc

                content_str = str(getattr(response_msg, "content", "") or "")
                out_tokens = estimate_tokens(content_str)
                in_tokens = estimate_tokens(prompt_repr)
                actual_cost = estimate_cost_usd(current_provider.provider, prompt_repr, content_str)
                if db:
                    await self.cost_monitor.record_cost(
                        db=db,
                        business_id=business_id,
                        provider=current_provider.provider,
                        model=current_provider.model,
                        operation=operation,
                        input_tokens=in_tokens,
                        output_tokens=out_tokens,
                        estimated_cost_usd=actual_cost,
                    )
                return AiAgentResult(
                    provider=current_provider.provider,
                    model=current_provider.model,
                    message=response_msg,
                    input_tokens=in_tokens,
                    output_tokens=out_tokens,
                    estimated_cost_usd=actual_cost,
                )
            except CostLimitExceeded:
                raise
            except Exception as exc:
                logger.warning(
                    "AI provider %s (%s) failed in agent invocation (%s: %s); cascading to next provider in fallback chain.",
                    current_provider.provider, current_provider.model, type(exc).__name__, exc
                )
        return None

    async def _call_agent_provider(
        self, provider: ProviderConfig, messages: list[Any], tools: list[Any] | None = None
    ) -> Any:
        model = self._chat_model(provider)
        if tools:
            model = model.bind_tools(tools)
        return await model.ainvoke(messages)


    async def _get_business(self, db: AsyncSession, business_id: UUID) -> Business | None:
        result = await db.execute(select(Business).where(Business.id == business_id).limit(1))
        return result.scalar_one_or_none()

    async def _call_provider(
        self, provider: ProviderConfig, system_prompt: str, user_prompt: str
    ) -> str:
        model = self._chat_model(provider)
        response = await model.ainvoke(
            [SystemMessage(content=system_prompt), HumanMessage(content=user_prompt)]
        )
        return str(response.content)

    def _chat_model(self, provider: ProviderConfig):
        if provider.provider == "gemini":
            from langchain_google_genai import ChatGoogleGenerativeAI

            return ChatGoogleGenerativeAI(
                model=provider.model,
                google_api_key=provider.api_key,
                temperature=0,
                max_retries=1,
            )
        if provider.provider == "groq":
            from langchain_groq import ChatGroq

            return ChatGroq(
                model=provider.model,
                api_key=provider.api_key,
                temperature=0,
                max_retries=1,
            )
        if provider.provider == "openai":
            from langchain_openai import ChatOpenAI

            return ChatOpenAI(
                model=provider.model,
                api_key=provider.api_key,
                temperature=0,
                max_retries=1,
            )
        raise ValueError(f"Unsupported provider: {provider.provider}")


def _parse_json_object(content: str) -> dict:
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", content, flags=re.DOTALL)
        if not match:
            raise
        return json.loads(match.group(0))
