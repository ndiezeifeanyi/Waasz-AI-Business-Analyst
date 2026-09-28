import logging
import re
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import SecurityError
from app.models.knowledge import UserKnowledgeChunk, UserKnowledgeDocument
from app.models.user import User
from app.services.embedding_service import EmbeddingService

logger = logging.getLogger(__name__)


def chunk_text(content: str, max_chars: int = 600, overlap: int = 80) -> list[str]:
    """
    Split text into semantically coherent overlapping chunks.
    Splits by paragraphs or sentences where possible.
    """
    if not content or not content.strip():
        return []

    text_clean = content.strip()
    if len(text_clean) <= max_chars:
        return [text_clean]

    # Split by newlines first
    paragraphs = re.split(r"\n\s*\n", text_clean)
    chunks = []
    current_chunk = ""

    for para in paragraphs:
        para = para.strip()
        if not para:
            continue

        if len(current_chunk) + len(para) <= max_chars:
            current_chunk = f"{current_chunk}\n\n{para}".strip() if current_chunk else para
        else:
            if current_chunk:
                chunks.append(current_chunk)
            # If paragraph itself exceeds max_chars, split by sentences
            if len(para) > max_chars:
                sentences = re.split(r"(?<=[.!?])\s+", para)
                sub_chunk = ""
                for sent in sentences:
                    if len(sub_chunk) + len(sent) <= max_chars:
                        sub_chunk = f"{sub_chunk} {sent}".strip() if sub_chunk else sent
                    else:
                        if sub_chunk:
                            chunks.append(sub_chunk)
                        sub_chunk = sent
                if sub_chunk:
                    current_chunk = sub_chunk
                else:
                    current_chunk = ""
            else:
                current_chunk = para

    if current_chunk:
        chunks.append(current_chunk)

    return chunks


class KnowledgeService:
    def __init__(self, embedding_service: EmbeddingService | None = None) -> None:
        self.embeddings = embedding_service or EmbeddingService()

    async def store_user_document(
        self,
        db: AsyncSession,
        user_id: UUID,
        title: str,
        content: str,
        source_type: str = "chat_upload",
        business_id: UUID | None = None,
        visibility: str = "private",
        extra_metadata: dict | None = None,
    ) -> UserKnowledgeDocument:
        """
        Store, chunk, and embed a document under a specific user_id.
        If visibility is 'business_shared' and business_id is set, it will be shared
        with staff under the same business_id (e.g., price lists, company catalogs).
        """
        doc = UserKnowledgeDocument(
            user_id=user_id,
            business_id=business_id,
            title=title,
            source_type=source_type,
            visibility=visibility,
            raw_content=content,
            extra_metadata=extra_metadata or {},
        )
        db.add(doc)
        await db.flush()

        chunks = chunk_text(content)
        if chunks:
            try:
                res = self.embeddings.embed_documents_with_meta(chunks)
                if hasattr(res, "__await__"):
                    vectors, is_fallback = await res
                else:
                    vectors = await self.embeddings.embed_documents(chunks)
                    is_fallback = False
            except (AttributeError, TypeError):
                vectors = await self.embeddings.embed_documents(chunks)
                is_fallback = False

            if is_fallback:
                logger.warning(
                    "Document %s stored with needs_reembedding=True (real embedding providers unavailable).",
                    doc.id,
                )
            for idx, (chunk_str, vector) in enumerate(zip(chunks, vectors)):
                chunk_rec = UserKnowledgeChunk(
                    document_id=doc.id,
                    user_id=user_id,
                    business_id=business_id,
                    visibility=visibility,
                    chunk_index=idx,
                    chunk_content=chunk_str,
                    embedding=vector,
                    needs_reembedding=is_fallback,
                    extra_metadata={
                        "source_title": title,
                        "source_type": source_type,
                        "needs_reembedding": is_fallback,
                    },
                )
                db.add(chunk_rec)
            await db.flush()

        logger.info(
            "Stored document %s (visibility=%s) with %d chunks for user %s (needs_reembedding=%s)",
            doc.id, visibility, len(chunks), user_id, is_fallback if chunks else False,
        )
        return doc

    async def reembed_flagged_chunks(self, db: AsyncSession, limit: int = 50) -> int:
        """
        Scan for chunks created with local hash pseudo-embeddings (needs_reembedding=True)
        and attempt to generate real semantic embeddings using an active provider.
        Returns the number of chunks successfully re-embedded.
        """
        stmt = (
            select(UserKnowledgeChunk)
            .where(UserKnowledgeChunk.needs_reembedding.is_(True))
            .limit(limit)
        )
        result = await db.execute(stmt)
        chunks = result.scalars().all()
        if not chunks:
            return 0

        texts = [c.chunk_content for c in chunks]
        try:
            res = self.embeddings.embed_documents_with_meta(texts)
            if hasattr(res, "__await__"):
                vectors, is_fallback = await res
            else:
                vectors = await self.embeddings.embed_documents(texts)
                is_fallback = False
        except (AttributeError, TypeError):
            vectors = await self.embeddings.embed_documents(texts)
            is_fallback = False

        if is_fallback:
            logger.info("Re-embedding attempted for %d chunks, but real providers are still offline.", len(chunks))
            return 0

        for chunk, vector in zip(chunks, vectors):
            chunk.embedding = vector
            chunk.needs_reembedding = False
            meta = dict(chunk.extra_metadata or {})
            meta["needs_reembedding"] = False
            chunk.extra_metadata = meta

        await db.commit()
        logger.info("Successfully re-embedded and cleared flags on %d chunks.", len(chunks))
        return len(chunks)

    async def search_user_knowledge(
        self,
        db: AsyncSession,
        user_id: UUID,
        query: str,
        business_id: UUID | None = None,
        top_k: int = 4,
        min_similarity: float = 0.4,
    ) -> list[dict]:
        """
        Search knowledge chunks strictly scoped to user_id and optionally business_shared
        chunks under the user's business_id.
        Guarantees that other users' private chunks and unrelated businesses are never retrieved.
        """
        if not query or not query.strip():
            return []

        if business_id:
            # Server-side verification: prevent forged/mismatched business_id from accessing shared data
            user_check = await db.execute(
                select(User.id).where(User.id == user_id, User.business_id == business_id)
            )
            if not user_check.scalar_one_or_none():
                logger.warning(
                    "Security violation: user %s does not belong to business %s (forged/mismatched business_id)",
                    user_id, business_id,
                )
                raise SecurityError(f"User {user_id} does not belong to business {business_id}")

        query_vec = await self.embeddings.embed_query(query)
        vec_literal = "[" + ",".join(str(f) for f in query_vec) + "]"

        if business_id:
            sql = text("""
                SELECT id, document_id, chunk_content, metadata,
                       1 - (embedding <=> CAST(:query_vec AS vector)) AS similarity
                FROM user_knowledge_chunks
                WHERE ((user_id = :user_id) OR (business_id = :business_id AND visibility = 'business_shared'))
                  AND (embedding IS NOT NULL)
                ORDER BY embedding <=> CAST(:query_vec AS vector)
                LIMIT :top_k
            """)
            params = {
                "user_id": user_id,
                "business_id": business_id,
                "query_vec": vec_literal,
                "top_k": top_k,
            }
        else:
            sql = text("""
                SELECT id, document_id, chunk_content, metadata,
                       1 - (embedding <=> CAST(:query_vec AS vector)) AS similarity
                FROM user_knowledge_chunks
                WHERE user_id = :user_id
                  AND (embedding IS NOT NULL)
                ORDER BY embedding <=> CAST(:query_vec AS vector)
                LIMIT :top_k
            """)
            params = {
                "user_id": user_id,
                "query_vec": vec_literal,
                "top_k": top_k,
            }

        try:
            result = await db.execute(sql, params)
            rows = result.fetchall()
            matches = []
            for row in rows:
                sim = float(row.similarity) if row.similarity is not None else 0.0
                if sim >= min_similarity:
                    matches.append({
                        "id": str(row.id),
                        "document_id": str(row.document_id),
                        "content": row.chunk_content,
                        "metadata": row.metadata,
                        "similarity": sim,
                    })
            return matches
        except Exception as exc:
            # If pgvector extension is not running in local test environment, fall back to SQL ILIKE search strictly by user_id
            logger.warning("Vector search fallback to keyword matching for user %s: %s", user_id, exc)
            if business_id:
                fallback_sql = (
                    select(UserKnowledgeChunk)
                    .where(
                        (UserKnowledgeChunk.user_id == user_id)
                        | (
                            (UserKnowledgeChunk.business_id == business_id)
                            & (UserKnowledgeChunk.visibility == "business_shared")
                        )
                    )
                    .limit(top_k)
                )
            else:
                fallback_sql = (
                    select(UserKnowledgeChunk)
                    .where(UserKnowledgeChunk.user_id == user_id)
                    .limit(top_k)
                )
            result = await db.execute(fallback_sql)
            chunks = result.scalars().all()
            return [
                {
                    "id": str(c.id),
                    "document_id": str(c.document_id),
                    "content": c.chunk_content,
                    "metadata": c.extra_metadata,
                    "similarity": 0.5,
                }
                for c in chunks
                if any(w in c.chunk_content.lower() for w in query.lower().split() if len(w) > 3)
            ]
