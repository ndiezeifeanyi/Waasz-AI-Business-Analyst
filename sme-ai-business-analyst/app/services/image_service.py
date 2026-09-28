"""
Image Generation & Editing Service.
Supports primary Gemini native image generation/editing model with automatic
fallback to OpenAI's image model via live-discovery resolution from ModelResolver.
Enforces per-user daily image budget and cost logging in CostMonitor.
"""
import base64
from decimal import Decimal
import io
import logging
from uuid import UUID

import httpx
from PIL import Image
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import CostLimitExceeded
from app.core.model_resolver import model_resolver
from app.services.cost_monitor import CostMonitor

logger = logging.getLogger(__name__)


class ImageService:
    def __init__(self, cost_monitor: CostMonitor | None = None) -> None:
        self.cost_monitor = cost_monitor or CostMonitor()

    async def generate_image(
        self,
        prompt: str,
        *,
        db: AsyncSession | None = None,
        business_id: UUID | None = None,
    ) -> tuple[bytes, str]:
        """
        Generate a new image from a text prompt.
        Cascades: Gemini -> OpenAI -> raises exception if all providers fail.
        Returns: (image_bytes, mime_type).
        """
        if db and business_id:
            await self.cost_monitor.ensure_user_image_budget(db, business_id)

        # 1. Primary: Gemini image generation
        gemini_model = model_resolver.get_model("gemini", "image_gen") or "gemini-2.5-flash-image"
        if settings.gemini_image_api_key and not settings.gemini_image_api_key.startswith("placeholder"):
            if settings.is_using_dedicated_gemini_image_key:
                logger.info("Using dedicated image-gen key (GEMINI_IMAGE_API_KEY) for Gemini generate_image call.")
            else:
                logger.info("Falling back to shared key (GEMINI_API_KEY) for Gemini generate_image call.")
            try:
                img_bytes, mime = await self._call_gemini_generate(prompt, gemini_model)
                if img_bytes:
                    if db and business_id:
                        await self.cost_monitor.record_cost(
                            db=db,
                            business_id=business_id,
                            provider="gemini",
                            model=gemini_model,
                            operation="image_gen",
                            input_tokens=0,
                            output_tokens=0,
                            estimated_cost_usd=Decimal("0.0400"),
                        )
                    return img_bytes, mime
            except Exception as exc:
                logger.warning(
                    "Primary Gemini image generation failed with model %s (%s: %s). Cascading to OpenAI fallback.",
                    gemini_model,
                    type(exc).__name__,
                    exc,
                )

        # 2. Fallback: OpenAI image generation
        openai_model = model_resolver.get_model("openai", "image_gen") or "dall-e-3"
        if settings.openai_api_key and not settings.openai_api_key.startswith("placeholder"):
            try:
                img_bytes, mime = await self._call_openai_generate(prompt, openai_model)
                if img_bytes:
                    if db and business_id:
                        await self.cost_monitor.record_cost(
                            db=db,
                            business_id=business_id,
                            provider="openai",
                            model=openai_model,
                            operation="image_gen",
                            input_tokens=0,
                            output_tokens=0,
                            estimated_cost_usd=Decimal("0.0400"),
                        )
                    return img_bytes, mime
            except Exception as exc:
                logger.error(
                    "Fallback OpenAI image generation failed with model %s (%s: %s).",
                    openai_model,
                    type(exc).__name__,
                    exc,
                )

        raise RuntimeError("Image generation failed across all available providers (Gemini & OpenAI).")

    async def edit_image(
        self,
        instruction: str,
        reference_image_bytes: bytes,
        *,
        reference_mime_type: str = "image/jpeg",
        db: AsyncSession | None = None,
        business_id: UUID | None = None,
    ) -> tuple[bytes, str]:
        """
        Edit an existing reference image based on user modification instructions.
        Cascades: Gemini -> OpenAI -> raises exception if all providers fail.
        Returns: (image_bytes, mime_type).
        """
        if not reference_image_bytes:
            raise ValueError("Reference image bytes are required to perform an image edit.")

        if db and business_id:
            await self.cost_monitor.ensure_user_image_budget(db, business_id)

        # 1. Primary: Gemini multimodal image editing
        gemini_model = model_resolver.get_model("gemini", "image_gen") or "gemini-2.5-flash-image"
        if settings.gemini_image_api_key and not settings.gemini_image_api_key.startswith("placeholder"):
            if settings.is_using_dedicated_gemini_image_key:
                logger.info("Using dedicated image-gen key (GEMINI_IMAGE_API_KEY) for Gemini edit_image call.")
            else:
                logger.info("Falling back to shared key (GEMINI_API_KEY) for Gemini edit_image call.")
            try:
                img_bytes, mime = await self._call_gemini_edit(
                    instruction, reference_image_bytes, reference_mime_type, gemini_model
                )
                if img_bytes:
                    if db and business_id:
                        await self.cost_monitor.record_cost(
                            db=db,
                            business_id=business_id,
                            provider="gemini",
                            model=gemini_model,
                            operation="image_gen",
                            input_tokens=0,
                            output_tokens=0,
                            estimated_cost_usd=Decimal("0.0400"),
                        )
                    return img_bytes, mime
            except Exception as exc:
                logger.warning(
                    "Primary Gemini image editing failed with model %s (%s: %s). Cascading to OpenAI fallback.",
                    gemini_model,
                    type(exc).__name__,
                    exc,
                )

        # 2. Fallback: OpenAI image edits
        openai_model = model_resolver.get_model("openai", "image_gen") or "dall-e-2"
        if settings.openai_api_key and not settings.openai_api_key.startswith("placeholder"):
            try:
                img_bytes, mime = await self._call_openai_edit(
                    instruction, reference_image_bytes, openai_model
                )
                if img_bytes:
                    if db and business_id:
                        await self.cost_monitor.record_cost(
                            db=db,
                            business_id=business_id,
                            provider="openai",
                            model=openai_model,
                            operation="image_gen",
                            input_tokens=0,
                            output_tokens=0,
                            estimated_cost_usd=Decimal("0.0400"),
                        )
                    return img_bytes, mime
            except Exception as exc:
                logger.error(
                    "Fallback OpenAI image editing failed with model %s (%s: %s).",
                    openai_model,
                    type(exc).__name__,
                    exc,
                )

        raise RuntimeError("Image editing failed across all available providers (Gemini & OpenAI).")

    async def _call_gemini_generate(self, prompt: str, model: str) -> tuple[bytes, str]:
        clean_model = model.replace("models/", "")
        api_key = settings.gemini_image_api_key
        if "imagen" in clean_model.lower():
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{clean_model}:predict?key={api_key}"
            payload = {
                "instances": [{"prompt": prompt}],
                "parameters": {"sampleCount": 1},
            }
            async with httpx.AsyncClient(timeout=45.0) as client:
                resp = await client.post(url, json=payload)
                if resp.status_code != 200:
                    raise RuntimeError(f"Gemini Imagen API returned {resp.status_code}: {resp.text[:200]}")
                data = resp.json()
            predictions = data.get("predictions") or []
            for pred in predictions:
                b64_data = pred.get("bytesBase64Encoded")
                mime = pred.get("mimeType", "image/png")
                if b64_data:
                    return base64.b64decode(b64_data), mime
            raise RuntimeError(f"No bytesBase64Encoded in Gemini Imagen response: {str(data)[:200]}")

        url = f"https://generativelanguage.googleapis.com/v1beta/models/{clean_model}:generateContent?key={api_key}"
        payload = {
            "contents": [
                {
                    "parts": [{"text": prompt}]
                }
            ],
            "generationConfig": {
                "responseModalities": ["IMAGE"]
            }
        }
        async with httpx.AsyncClient(timeout=45.0) as client:
            resp = await client.post(url, json=payload)
            if resp.status_code != 200:
                raise RuntimeError(f"Gemini API returned {resp.status_code}: {resp.text[:200]}")
            data = resp.json()

        candidates = data.get("candidates") or []
        for cand in candidates:
            parts = cand.get("content", {}).get("parts", [])
            for p in parts:
                if "inlineData" in p:
                    b64_data = p["inlineData"].get("data", "")
                    mime = p["inlineData"].get("mimeType", "image/png")
                    return base64.b64decode(b64_data), mime

        raise RuntimeError(f"No inlineData image parts in Gemini response: {str(data)[:200]}")

    async def _call_gemini_edit(
        self, instruction: str, reference_bytes: bytes, mime_type: str, model: str
    ) -> tuple[bytes, str]:
        clean_model = model.replace("models/", "")
        api_key = settings.gemini_image_api_key
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{clean_model}:generateContent?key={api_key}"
        b64_ref = base64.b64encode(reference_bytes).decode("utf-8")
        payload = {
            "contents": [
                {
                    "parts": [
                        {
                            "inlineData": {
                                "mimeType": mime_type,
                                "data": b64_ref,
                            }
                        },
                        {"text": instruction},
                    ]
                }
            ],
            "generationConfig": {
                "responseModalities": ["IMAGE"]
            }
        }
        async with httpx.AsyncClient(timeout=45.0) as client:
            resp = await client.post(url, json=payload)
            if resp.status_code != 200:
                raise RuntimeError(f"Gemini API returned {resp.status_code}: {resp.text[:200]}")
            data = resp.json()

        candidates = data.get("candidates") or []
        for cand in candidates:
            parts = cand.get("content", {}).get("parts", [])
            for p in parts:
                if "inlineData" in p:
                    b64_data = p["inlineData"].get("data", "")
                    mime = p["inlineData"].get("mimeType", "image/png")
                    return base64.b64decode(b64_data), mime

        raise RuntimeError(f"No inlineData image parts in Gemini edit response: {str(data)[:200]}")

    async def _call_openai_generate(self, prompt: str, model: str) -> tuple[bytes, str]:
        headers = {
            "Authorization": f"Bearer {settings.openai_api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "prompt": prompt,
            "n": 1,
            "size": "1024x1024",
            "model": model,
        }
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                "https://api.openai.com/v1/images/generations",
                json=payload,
                headers=headers,
            )
            if resp.status_code != 200:
                raise RuntimeError(f"OpenAI API returned {resp.status_code}: {resp.text[:200]}")
            data = resp.json()

        items = data.get("data") or []
        if not items:
            raise RuntimeError(f"No image data returned from OpenAI: {str(data)[:200]}")

        first = items[0]
        if "b64_json" in first:
            return base64.b64decode(first["b64_json"]), "image/png"
        elif "url" in first:
            async with httpx.AsyncClient(timeout=30.0) as client:
                dl_resp = await client.get(first["url"])
                dl_resp.raise_for_status()
                return dl_resp.content, "image/png"

        raise RuntimeError(f"Unrecognized OpenAI image format: {first}")

    async def _call_openai_edit(
        self, instruction: str, reference_bytes: bytes, model: str
    ) -> tuple[bytes, str]:
        # Convert image to PNG RGBA format as required by OpenAI edits API
        try:
            image_obj = Image.open(io.BytesIO(reference_bytes)).convert("RGBA")
            buf = io.BytesIO()
            image_obj.save(buf, format="PNG")
            png_bytes = buf.getvalue()
        except Exception:
            png_bytes = reference_bytes

        headers = {"Authorization": f"Bearer {settings.openai_api_key}"}
        files = {
            "image": ("image.png", png_bytes, "image/png"),
        }
        data = {
            "prompt": instruction,
            "n": "1",
            "size": "1024x1024",
        }
        if "dall-e" in model.lower():
            data["model"] = model

        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                "https://api.openai.com/v1/images/edits",
                files=files,
                data=data,
                headers=headers,
            )
            if resp.status_code != 200:
                raise RuntimeError(f"OpenAI edit API returned {resp.status_code}: {resp.text[:200]}")
            result_data = resp.json()

        items = result_data.get("data") or []
        if not items:
            raise RuntimeError(f"No image data returned from OpenAI edits: {str(result_data)[:200]}")

        first = items[0]
        if "b64_json" in first:
            return base64.b64decode(first["b64_json"]), "image/png"
        elif "url" in first:
            async with httpx.AsyncClient(timeout=30.0) as client:
                dl_resp = await client.get(first["url"])
                dl_resp.raise_for_status()
                return dl_resp.content, "image/png"

        raise RuntimeError(f"Unrecognized OpenAI edit format: {first}")
