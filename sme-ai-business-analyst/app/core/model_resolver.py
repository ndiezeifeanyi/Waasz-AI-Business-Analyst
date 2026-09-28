"""
Dynamic Live Model Resolver with In-Memory Caching, Mid-Run Self-Healing, and 24h Scheduled Refresh.

Resolves models dynamically from live provider APIs (Gemini, Groq, OpenAI) for:
- Chat / JSON extraction
- Embeddings
- Audio transcription

Selection Rules:
- Gemini:
    * Chat: Queries v1beta/models, filters for generateContent support, excludes TTS/vision/preview/experimental,
      ranks stable Flash models by version (e.g. gemini-3.6-flash, gemini-2.5-flash).
    * Embeddings: Queries v1beta/models, filters for embedContent support, prioritizes models/gemini-embedding-001.
- Groq:
    * Chat: Queries /models, excludes audio/safeguards/specialized, prioritizes [openai/gpt-oss-120b, openai/gpt-oss-20b, llama-3.3-70b-versatile, qwen/qwen3.8-27b].
    * Audio: Queries /models, filters for whisper, prioritizes [whisper-large-v3-turbo, whisper-large-v3].
- OpenAI:
    * Chat: Queries /models, prioritizes [gpt-4o-mini, gpt-4o, gpt-3.5-turbo].
    * Embeddings: Queries /models, prioritizes [text-embedding-3-small, text-embedding-3-large].
    * Audio: Queries /models, prioritizes [whisper-1].
"""

import asyncio
from datetime import UTC, datetime
import logging
import os
import re
from typing import Literal

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

Provider = Literal["gemini", "groq", "openai"]
Capability = Literal["chat", "embeddings", "audio", "vision", "image_gen"]

# Defaults to ensure safe startup/offline resilience
DEFAULT_MODELS: dict[tuple[Provider, Capability], str] = {
    ("gemini", "chat"): "gemini-3.8-flash",
    ("gemini", "vision"): "gemini-3.8-flash",
    ("gemini", "embeddings"): "models/gemini-embedding-001",
    ("gemini", "image_gen"): "gemini-3.1-flash-image",
    ("groq", "chat"): "openai/gpt-oss-120b",
    ("groq", "vision"): "",
    ("groq", "audio"): "whisper-large-v3-turbo",
    ("groq", "image_gen"): "",
    ("openai", "chat"): "gpt-4o-mini",
    ("openai", "vision"): "gpt-4o-mini",
    ("openai", "embeddings"): "text-embedding-3-small",
    ("openai", "audio"): "whisper-1",
    ("openai", "image_gen"): "gpt-image-1",
}

ENV_OVERRIDE_KEYS: dict[tuple[Provider, Capability], list[str]] = {
    ("gemini", "chat"): ["GEMINI_CHAT_MODEL", "GEMINI_MODEL"],
    ("gemini", "vision"): ["GEMINI_VISION_MODEL", "GEMINI_CHAT_MODEL", "GEMINI_MODEL"],
    ("gemini", "embeddings"): ["GEMINI_EMBEDDING_MODEL"],
    ("gemini", "image_gen"): ["GEMINI_IMAGE_GEN_MODEL", "GEMINI_IMAGE_MODEL"],
    ("groq", "chat"): ["GROQ_CHAT_MODEL", "GROQ_MODEL"],
    ("groq", "vision"): ["GROQ_VISION_MODEL"],
    ("groq", "audio"): ["GROQ_AUDIO_MODEL"],
    ("groq", "image_gen"): [],
    ("openai", "chat"): ["OPENAI_CHAT_MODEL", "OPENAI_FALLBACK_MODEL"],
    ("openai", "vision"): ["OPENAI_VISION_MODEL", "OPENAI_CHAT_MODEL", "OPENAI_FALLBACK_MODEL"],
    ("openai", "embeddings"): ["OPENAI_EMBEDDING_MODEL"],
    ("openai", "audio"): ["OPENAI_AUDIO_MODEL"],
    ("openai", "image_gen"): ["OPENAI_IMAGE_GEN_MODEL", "OPENAI_IMAGE_MODEL"],
}


def is_model_not_found_error(exc: Exception) -> bool:
    """
    Check if an exception is a 404-class error indicating that the requested model
    was not found, retired, deprecated, or decommissioned.
    """
    if exc is None:
        return False

    err_str = str(exc).lower()
    err_type = type(exc).__name__.lower()

    status_code = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    if status_code in (404, "404"):
        return True

    indicators = [
        "not_found",
        "not found",
        "404",
        "does not exist",
        "model_not_found",
        "model_decommissioned",
        "is not supported",
        "unsupported model",
        "no longer available",
        "is deprecated",
    ]
    if any(ind in err_str for ind in indicators):
        return True

    if "notfound" in err_type:
        return True

    return False


class ModelResolver:
    """
    Dynamic live model resolver.
    - Queries provider model list endpoints
    - Selects recommended flagship per capability
    - Caches in-memory, refreshed at startup and once every 24 hours
    - Supports env overrides
    - Self-heals on 404/not found errors mid-run
    """

    def __init__(self) -> None:
        self._cache: dict[tuple[Provider, Capability], str] = dict(DEFAULT_MODELS)
        self._candidates_cache: dict[tuple[Provider, Capability], list[str]] = {}
        self._last_refreshed: datetime | None = None
        self._provider_health: dict[Provider, str] = {
            "gemini": "unknown",
            "groq": "unknown",
            "openai": "unknown",
        }
        self._lock = asyncio.Lock()

    def get_override(self, provider: Provider, capability: Capability) -> str | None:
        keys = ENV_OVERRIDE_KEYS.get((provider, capability), [])
        for k in keys:
            val = os.getenv(k)
            if val and val.strip():
                return val.strip()
        return None

    def get_model(self, provider: Provider, capability: Capability) -> str:
        """Return the currently cached/overridden model name immediately without network call."""
        override = self.get_override(provider, capability)
        if override:
            return override
        return self._cache.get((provider, capability), DEFAULT_MODELS.get((provider, capability), ""))

    def get_candidate_models(self, provider: Provider, capability: Capability) -> list[str]:
        """Return the list of active/discovered candidate models in priority order for this capability."""
        override = self.get_override(provider, capability)
        if override:
            return [override]
        cached = self._candidates_cache.get((provider, capability))
        if cached:
            return list(cached)
        primary = self.get_model(provider, capability)
        return [primary] if primary else []

    async def resolve(
        self, provider: Provider, capability: Capability, force_refresh: bool = False
    ) -> str:
        """
        Resolve active model for (provider, capability).
        If force_refresh is True, bypasses cache and re-queries live provider.
        """
        override = self.get_override(provider, capability)
        if override:
            self._cache[(provider, capability)] = override
            return override

        if not force_refresh and (provider, capability) in self._cache:
            return self._cache[(provider, capability)]

        async with self._lock:
            if not force_refresh and (provider, capability) in self._cache:
                return self._cache[(provider, capability)]

            old_model = self._cache.get((provider, capability))
            new_model = await self._fetch_live_model(provider, capability)
            if new_model:
                self._cache[(provider, capability)] = new_model
                if new_model != old_model:
                    logger.info(
                        "Model dynamically resolved for %s/%s: %s (was: %s)",
                        provider, capability, new_model, old_model
                    )
                return new_model
            return self._cache.get((provider, capability), DEFAULT_MODELS.get((provider, capability), ""))

    async def handle_mid_run_failure(
        self, provider: Provider, capability: Capability, error: Exception
    ) -> tuple[bool, str]:
        """
        Check if error is a 404 model-not-found error.
        If yes, force re-resolves the model.
        Returns (did_self_heal, new_model).
        """
        if not is_model_not_found_error(error):
            return False, self.get_model(provider, capability)

        logger.warning(
            "Mid-run 404 error on %s/%s with model '%s': %s. Initiating self-healing re-resolution...",
            provider, capability, self.get_model(provider, capability), error
        )
        old_model = self._cache.get((provider, capability))
        new_model = await self.resolve(provider, capability, force_refresh=True)

        if new_model and new_model != old_model:
            logger.info(
                "Self-healing SUCCESS for %s/%s: switched from %s to %s",
                provider, capability, old_model, new_model
            )
            return True, new_model

        logger.warning(
            "Self-healing did not change model for %s/%s (remains %s). Cascading to next provider.",
            provider, capability, old_model
        )
        return False, new_model or old_model or ""

    async def refresh_all(self) -> dict[str, str]:
        """
        Refresh model discovery across all providers and capabilities.
        Called on startup and once every 24h.
        Never throws: catches provider failures gracefully and logs warnings.
        """
        logger.info("--- Starting Model Resolution Refresh (Gemini, Groq, OpenAI) ---")
        results = {}

        tasks: list[tuple[Provider, Capability]] = [
            ("gemini", "chat"),
            ("gemini", "vision"),
            ("gemini", "embeddings"),
            ("gemini", "image_gen"),
            ("groq", "chat"),
            ("groq", "vision"),
            ("groq", "audio"),
            ("openai", "chat"),
            ("openai", "vision"),
            ("openai", "embeddings"),
            ("openai", "audio"),
            ("openai", "image_gen"),
        ]

        for prov, cap in tasks:
            override = self.get_override(prov, cap)
            if override:
                self._cache[(prov, cap)] = override
                results[f"{prov}_{cap}"] = f"{override} (override)"
                logger.info("Active model [%s/%s]: %s (via .env override)", prov, cap, override)
                continue

            try:
                model = await self._fetch_live_model(prov, cap)
                if model:
                    self._cache[(prov, cap)] = model
                    self._provider_health[prov] = "healthy"
                    results[f"{prov}_{cap}"] = model
                    logger.info("Active model [%s/%s]: %s (live resolved)", prov, cap, model)
                else:
                    fallback = DEFAULT_MODELS.get((prov, cap), "")
                    self._cache[(prov, cap)] = fallback
                    results[f"{prov}_{cap}"] = f"{fallback} (default)"
                    logger.info("Active model [%s/%s]: %s (fallback default)", prov, cap, fallback)
            except Exception as exc:
                self._provider_health[prov] = "degraded"
                fallback = DEFAULT_MODELS.get((prov, cap), "")
                self._cache[(prov, cap)] = fallback
                results[f"{prov}_{cap}"] = f"{fallback} (default)"
                logger.warning(
                    "Model resolution failed for %s/%s: %s. Using fallback '%s'. Provider marked degraded.",
                    prov, cap, exc, fallback
                )

        self._last_refreshed = datetime.now(UTC)
        logger.info("--- Model Resolution Complete ---")
        return results

    async def _fetch_live_model(self, provider: Provider, capability: Capability) -> str | None:
        if provider == "gemini":
            return await self._resolve_gemini(capability)
        elif provider == "groq":
            return await self._resolve_groq(capability)
        elif provider == "openai":
            return await self._resolve_openai(capability)
        return None

    async def _resolve_gemini(self, capability: Capability) -> str | None:
        if not settings.gemini_api_key or settings.gemini_api_key.startswith("placeholder"):
            return None

        url = f"https://generativelanguage.googleapis.com/v1beta/models?key={settings.gemini_api_key}"
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url)
            if resp.status_code != 200:
                logger.warning("Gemini model list endpoint returned HTTP %s", resp.status_code)
                return None
            data = resp.json().get("models", [])

        if capability in ("chat", "vision"):
            candidates = []
            for m in data:
                methods = m.get("supportedGenerationMethods", [])
                name = m.get("name", "")
                if "generateContent" in methods and "flash" in name.lower():
                    # Filter out TTS, image-generation, and learnlm variants
                    if not any(x in name.lower() for x in ["tts", "imagen", "learnlm"]):
                        clean_name = name.replace("models/", "")
                        candidates.append(clean_name)

            if not candidates:
                return DEFAULT_MODELS[("gemini", capability)]

            # Prioritize flagship flash models by version (e.g. 3.8, 3.7, 3.6, 3.5)
            def _version_key(m_name: str) -> float:
                match = re.search(r"gemini-(\d+(?:\.\d+)?)", m_name)
                return float(match.group(1)) if match else 0.0

            candidates.sort(key=_version_key, reverse=True)
            self._candidates_cache[("gemini", capability)] = candidates
            return candidates[0]

        elif capability == "embeddings":
            candidates = [
                m.get("name", "")
                for m in data
                if "embedContent" in m.get("supportedGenerationMethods", [])
            ]
            for pref in ["models/gemini-embedding-001", "models/gemini-embedding-2"]:
                if pref in candidates:
                    return pref
            return candidates[0] if candidates else DEFAULT_MODELS[("gemini", "embeddings")]

        elif capability == "image_gen":
            candidates = []
            for m in data:
                name = m.get("name", "")
                methods = m.get("supportedGenerationMethods", [])
                # Verify model actually supports image generation or content generation for image models
                if ("generateContent" in methods or "imageGeneration" in methods) and "image" in name.lower():
                    if not any(x in name.lower() for x in ["tts", "preview-tts", "learnlm"]):
                        clean_name = name.replace("models/", "")
                        candidates.append(clean_name)

            if not candidates:
                return DEFAULT_MODELS[("gemini", "image_gen")]

            def _version_key(m_name: str) -> float:
                match = re.search(r"gemini-(\d+(?:\.\d+)?)", m_name)
                return float(match.group(1)) if match else 0.0

            candidates.sort(key=_version_key, reverse=True)
            self._candidates_cache[("gemini", "image_gen")] = candidates
            return candidates[0]

        return None

    async def _resolve_groq(self, capability: Capability) -> str | None:
        if not settings.groq_api_key or settings.groq_api_key.startswith("placeholder"):
            return None

        headers = {
            "Authorization": f"Bearer {settings.groq_api_key}",
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get("https://api.groq.com/openai/v1/models", headers=headers)
            if resp.status_code != 200:
                logger.warning("Groq model list endpoint returned HTTP %s", resp.status_code)
                return None
            models_data = resp.json().get("data", [])

        model_ids = [m.get("id") for m in models_data if m.get("active", True)]

        if capability == "chat":
            # Priority preference list of Groq's high-speed chat flagships
            priority_list = [
                "openai/gpt-oss-120b",
                "openai/gpt-oss-20b",
                "llama-3.3-70b-versatile",
                "qwen/qwen3.8-27b",
            ]
            for target in priority_list:
                if target in model_ids:
                    return target

            # Fallback to any active non-whisper, non-guard model
            for mid in model_ids:
                if not any(x in mid.lower() for x in ["whisper", "guard", "safeguard", "orpheus"]):
                    return mid
            return DEFAULT_MODELS[("groq", "chat")]

        elif capability == "vision":
            vision_candidates = [
                mid for mid in model_ids
                if any(v in mid.lower() for v in ["vision", "scout"])
            ]
            if vision_candidates:
                self._candidates_cache[("groq", "vision")] = vision_candidates
                return vision_candidates[0]
            return None

        elif capability == "audio":
            priority_list = [
                "whisper-large-v3-turbo",
                "whisper-large-v3",
                "distil-whisper-large-v3-en",
            ]
            for target in priority_list:
                if target in model_ids:
                    return target
            return DEFAULT_MODELS[("groq", "audio")]

        return None

    async def _resolve_openai(self, capability: Capability) -> str | None:
        if not settings.openai_api_key or settings.openai_api_key.startswith("placeholder"):
            return None

        headers = {
            "Authorization": f"Bearer {settings.openai_api_key}",
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get("https://api.openai.com/v1/models", headers=headers)
            if resp.status_code != 200:
                logger.warning("OpenAI model list endpoint returned HTTP %s", resp.status_code)
                return None
            models_data = resp.json().get("data", [])

        model_ids = [m.get("id") for m in models_data]

        if capability == "chat":
            priority_list = ["gpt-4o-mini", "gpt-4o", "gpt-3.5-turbo"]
            for target in priority_list:
                if target in model_ids:
                    return target
            return DEFAULT_MODELS[("openai", "chat")]

        elif capability == "vision":
            priority_list = ["gpt-4o-mini", "gpt-4o"]
            matched = [target for target in priority_list if target in model_ids]
            if matched:
                self._candidates_cache[("openai", "vision")] = matched
                return matched[0]
            return DEFAULT_MODELS[("openai", "vision")]

        elif capability == "embeddings":
            priority_list = ["text-embedding-3-small", "text-embedding-3-large", "text-embedding-ada-002"]
            for target in priority_list:
                if target in model_ids:
                    return target
            return DEFAULT_MODELS[("openai", "embeddings")]

        elif capability == "audio":
            priority_list = ["whisper-1"]
            for target in priority_list:
                if target in model_ids:
                    return target
            return DEFAULT_MODELS[("openai", "audio")]

        elif capability == "image_gen":
            priority_list = [
                "gpt-image-1",
                "gpt-image-1-mini",
                "gpt-image-2",
                "chatgpt-image-latest",
                "dall-e-3",
                "dall-e-2",
            ]
            matched = [target for target in priority_list if target in model_ids]
            if not matched:
                matched = [mid for mid in model_ids if any(x in mid.lower() for x in ["image", "dall"])]
            if matched:
                self._candidates_cache[("openai", "image_gen")] = matched
                return matched[0]
            return DEFAULT_MODELS[("openai", "image_gen")]

        return None


# Global singleton instance
model_resolver = ModelResolver()
