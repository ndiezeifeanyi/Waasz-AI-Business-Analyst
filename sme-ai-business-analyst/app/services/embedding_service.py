import logging
from typing import Sequence

import httpx
from langchain_google_genai import GoogleGenerativeAIEmbeddings

from app.core.config import settings
from app.core.model_resolver import is_model_not_found_error, model_resolver

logger = logging.getLogger(__name__)


class EmbeddingService:
    def __init__(self, api_key: str | None = None, model: str | None = None) -> None:
        self.api_key = api_key or settings.gemini_api_key
        self._custom_model = model
        self.dimension = 768

    @property
    def gemini_model(self) -> str:
        return self._custom_model or model_resolver.get_model("gemini", "embeddings")

    @property
    def openai_model(self) -> str:
        return model_resolver.get_model("openai", "embeddings")

    async def embed_query(self, text: str) -> list[float]:
        """Embed a single query string into a 768-dim vector."""
        vectors = await self.embed_documents([text])
        return vectors[0] if vectors else [0.0] * self.dimension

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed a batch of document texts into 768-dim vectors."""
        vectors, _ = await self.embed_documents_with_meta(texts)
        return vectors

    async def embed_documents_with_meta(self, texts: Sequence[str]) -> tuple[list[list[float]], bool]:
        """
        Embed a batch of document texts into 768-dim vectors.
        Returns (vectors, is_fallback), where is_fallback=True indicates that
        neither Gemini nor OpenAI succeeded and local deterministic pseudo-embeddings
        were generated.
        """
        if not texts:
            return [], False

        # 1. Primary: Gemini Embeddings
        if self.api_key and self.api_key != "placeholder_gemini_key":
            cur_gemini_model = self.gemini_model
            try:
                embeddings = GoogleGenerativeAIEmbeddings(
                    model=cur_gemini_model,
                    google_api_key=self.api_key,
                )
                raw_vectors = await embeddings.aembed_documents(list(texts))
                return [self._format_dimension(v) for v in raw_vectors], False
            except Exception as exc:
                if is_model_not_found_error(exc):
                    did_heal, new_model = await model_resolver.handle_mid_run_failure("gemini", "embeddings", exc)
                    if did_heal:
                        try:
                            embeddings = GoogleGenerativeAIEmbeddings(
                                model=new_model,
                                google_api_key=self.api_key,
                            )
                            raw_vectors = await embeddings.aembed_documents(list(texts))
                            return [self._format_dimension(v) for v in raw_vectors], False
                        except Exception as retry_exc:
                            exc = retry_exc
                logger.warning("Gemini embedding API call failed: %s. Trying OpenAI fallback.", exc)

        # 2. Secondary: OpenAI Embeddings fallback
        if settings.openai_api_key and settings.openai_api_key != "placeholder_openai_key":
            cur_openai_model = self.openai_model
            try:
                from langchain_openai import OpenAIEmbeddings

                openai_emb = OpenAIEmbeddings(
                    model=cur_openai_model,
                    api_key=settings.openai_api_key,
                    dimensions=self.dimension,
                    max_retries=1,
                )
                raw_vectors = await openai_emb.aembed_documents(list(texts))
                return [self._format_dimension(v) for v in raw_vectors], False
            except Exception as exc:
                if is_model_not_found_error(exc):
                    did_heal, new_model = await model_resolver.handle_mid_run_failure("openai", "embeddings", exc)
                    if did_heal:
                        try:
                            openai_emb = OpenAIEmbeddings(
                                model=new_model,
                                api_key=settings.openai_api_key,
                                dimensions=self.dimension,
                                max_retries=1,
                            )
                            raw_vectors = await openai_emb.aembed_documents(list(texts))
                            return [self._format_dimension(v) for v in raw_vectors], False
                        except Exception as retry_exc:
                            exc = retry_exc
                logger.warning("OpenAI embedding API call failed: %s. Using local fallback vector.", exc)

        # 3. Tertiary: Fallback deterministic pseudo-embeddings for tests or offline environments
        logger.warning(
            "Using tertiary local hash pseudo-embeddings. Chunks should be flagged with needs_reembedding=True."
        )
        return [self._fallback_embedding(t) for t in texts], True

    def _format_dimension(self, vector: list[float]) -> list[float]:
        """Ensure vector is exactly self.dimension (768) elements long and normalized."""
        import math

        if len(vector) > self.dimension:
            vector = vector[: self.dimension]
        elif len(vector) < self.dimension:
            vector = vector + [0.0] * (self.dimension - len(vector))

        norm = math.sqrt(sum(x * x for x in vector))
        if norm > 0:
            return [x / norm for x in vector]
        return vector

    def _fallback_embedding(self, text: str) -> list[float]:
        """Generate a deterministic 768-dim float vector based on word frequencies."""
        import hashlib
        import math

        vector = [0.0] * self.dimension
        words = text.lower().split()
        if not words:
            return vector

        for word in words:
            h = int(hashlib.sha256(word.encode("utf-8")).hexdigest()[:8], 16)
            idx = h % self.dimension
            vector[idx] += 1.0

        # Normalize to unit length
        norm = math.sqrt(sum(x * x for x in vector))
        if norm > 0:
            vector = [x / norm for x in vector]
        return vector
