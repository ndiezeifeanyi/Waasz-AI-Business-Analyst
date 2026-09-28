from datetime import UTC, datetime, timedelta
import logging
from uuid import UUID

from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.activity import Activity
from app.models.business import Business
from app.models.goal import UserGoal
from app.models.knowledge import UserKnowledgeChunk, UserKnowledgeDocument
from app.models.memory import ConversationMemory
from app.models.message import WhatsAppMessage
from app.models.user import User
from app.services.embedding_service import EmbeddingService

logger = logging.getLogger(__name__)


class MemoryService:
    def __init__(self, embedding_service: EmbeddingService | None = None) -> None:
        self.embeddings = embedding_service or EmbeddingService()

    async def get_short_term_turns(
        self,
        db: AsyncSession,
        user_id: UUID,
        limit: int = 6,
        exclude_message_id: UUID | None = None,
    ) -> list[dict]:
        """
        Retrieve recent conversation turns strictly for the given user_id.
        Returns chronological list of {role: 'user'|'assistant', content: str}.
        """
        stmt = select(WhatsAppMessage).where(WhatsAppMessage.user_id == user_id)
        if exclude_message_id is not None:
            stmt = stmt.where(WhatsAppMessage.id != exclude_message_id)
        stmt = stmt.order_by(WhatsAppMessage.created_at.desc()).limit(limit)
        result = await db.execute(stmt)
        messages = list(reversed(result.scalars().all()))

        turns = []
        for msg in messages:
            role = "user" if msg.direction == "inbound" else "assistant"
            content = msg.body or msg.interactive_reply_id or ""
            if content:
                turns.append({"role": role, "content": content})
        return turns

    async def retrieve_relevant_memories(
        self,
        db: AsyncSession,
        user_id: UUID,
        query: str,
        top_k: int = 4,
        min_similarity: float = 0.4,
    ) -> list[dict]:
        """
        Retrieve long-term semantic facts and session summaries for the given user_id.
        Strictly isolated via parameterized SQL query.
        """
        if not query or not query.strip():
            return []

        query_vec = await self.embeddings.embed_query(query)
        vec_literal = "[" + ",".join(str(f) for f in query_vec) + "]"

        sql = text("""
            SELECT id, memory_type, content, metadata,
                   1 - (embedding <=> CAST(:query_vec AS vector)) AS similarity
            FROM conversation_memories
            WHERE user_id = :user_id
              AND (embedding IS NOT NULL)
            ORDER BY embedding <=> CAST(:query_vec AS vector)
            LIMIT :top_k
        """)

        try:
            result = await db.execute(
                sql,
                {"user_id": user_id, "query_vec": vec_literal, "top_k": top_k},
            )
            rows = result.fetchall()
            matches = []
            for row in rows:
                sim = float(row.similarity) if row.similarity is not None else 0.0
                if sim >= min_similarity:
                    matches.append({
                        "id": str(row.id),
                        "type": row.memory_type,
                        "content": row.content,
                        "similarity": sim,
                    })
            return matches
        except Exception as exc:
            logger.warning("Vector search fallback on memory for user %s: %s", user_id, exc)
            fallback_sql = select(ConversationMemory).where(
                ConversationMemory.user_id == user_id
            ).limit(top_k)
            result = await db.execute(fallback_sql)
            memories = result.scalars().all()
            return [
                {
                    "id": str(m.id),
                    "type": m.memory_type,
                    "content": m.content,
                    "similarity": 0.5,
                }
                for m in memories
            ]

    async def compact_user_conversation(
        self, db: AsyncSession, user_id: UUID, turns: list[dict], business_id: UUID | None = None
    ) -> ConversationMemory | None:
        """
        Compact turns into a durable memory summary with embeddings.
        """
        if not turns:
            return None

        # Build summary representation
        transcript = "\n".join(f"{t['role']}: {t['content']}" for t in turns)
        summary_content = f"Session summary of {len(turns)} turns:\n{transcript[:600]}"

        vector = await self.embeddings.embed_query(summary_content)

        memory = ConversationMemory(
            user_id=user_id,
            business_id=business_id,
            memory_type="session_summary",
            content=summary_content,
            embedding=vector,
            extra_metadata={"turns_count": len(turns)},
        )
        db.add(memory)

        # Update user's last_compacted_at
        user = await db.get(User, user_id)
        if user:
            user.last_compacted_at = datetime.now(UTC)

        await db.commit()
        return memory

    async def compact_idle_conversations(self, db: AsyncSession) -> int:
        """
        Scheduled job: Finds users idle >30 minutes with >=10 uncompacted turns,
        and compacts their recent conversation turns into a durable summary.
        """
        now = datetime.now(UTC)
        idle_cutoff = now - timedelta(minutes=30)

        stmt = select(User).where(
            User.is_active == True,
            User.last_inbound_at <= idle_cutoff,
            (User.last_compacted_at.is_(None) | (User.last_compacted_at < User.last_inbound_at)),
        )
        users = (await db.execute(stmt)).scalars().all()
        compacted_count = 0

        for u in users:
            turns_stmt = (
                select(WhatsAppMessage)
                .where(
                    WhatsAppMessage.user_id == u.id,
                    (WhatsAppMessage.created_at > u.last_compacted_at) if u.last_compacted_at else True,
                )
                .order_by(WhatsAppMessage.created_at.asc())
            )
            msgs = (await db.execute(turns_stmt)).scalars().all()
            if len(msgs) >= 10:
                turns = [
                    {
                        "role": "user" if m.direction == "inbound" else "assistant",
                        "content": m.body or m.interactive_reply_id or "",
                    }
                    for m in msgs
                ]
                await self.compact_user_conversation(db, u.id, turns, business_id=u.business_id)
                compacted_count += 1

        logger.info("Compacted idle conversations for %d users", compacted_count)
        return compacted_count

    async def purge_user_data(self, db: AsyncSession, user_id: UUID) -> dict:
        """
        Right-to-be-forgotten: Atomically delete all records, memories, embeddings,
        and activities belonging to user_id.
        """
        purged = {}
        r_mem = await db.execute(delete(ConversationMemory).where(ConversationMemory.user_id == user_id))
        r_chunks = await db.execute(delete(UserKnowledgeChunk).where(UserKnowledgeChunk.user_id == user_id))
        r_docs = await db.execute(delete(UserKnowledgeDocument).where(UserKnowledgeDocument.user_id == user_id))
        r_goals = await db.execute(delete(UserGoal).where(UserGoal.user_id == user_id))
        r_acts = await db.execute(delete(Activity).where(Activity.user_id == user_id))
        r_msgs = await db.execute(delete(WhatsAppMessage).where(WhatsAppMessage.user_id == user_id))

        await db.commit()

        purged["conversation_memories"] = r_mem.rowcount
        purged["knowledge_chunks"] = r_chunks.rowcount
        purged["knowledge_docs"] = r_docs.rowcount
        purged["goals"] = r_goals.rowcount
        purged["activities"] = r_acts.rowcount
        purged["messages"] = r_msgs.rowcount

        logger.info("Purged all data for user %s: %s", user_id, purged)
        return purged

    async def purge_expired_user_data(self, db: AsyncSession) -> int:
        """
        Enforce data retention policies:
        - business_shared activities and knowledge documents are purged based on Business.data_retention_days
        - private activities, knowledge documents, and inactive goals are purged based on User.data_retention_days
        - WhatsApp messages and conversation memories are purged based on User.data_retention_days
        - Deleting parent UserKnowledgeDocument cascades automatically to UserKnowledgeChunk
        - Active/in-progress user goals are preserved
        """
        now = datetime.now(UTC)
        total_purged = 0

        # 1. Purge business_shared data based on Business retention setting
        businesses_res = await db.execute(select(Business).where(Business.deleted_at.is_(None)))
        businesses = businesses_res.scalars().all()
        for b in businesses:
            if getattr(b, "is_provisional", False):
                # For provisional businesses, treat retention as the user's own data_retention_days
                u_res = await db.execute(
                    select(User).where(User.business_id == b.id, User.deleted_at.is_(None)).limit(1)
                )
                u = u_res.scalar_one_or_none()
                b_retention_days = getattr(u, "data_retention_days", 365) if u else 365
            else:
                b_retention_days = getattr(b, "data_retention_days", 730) or 730
            b_cutoff = now - timedelta(days=b_retention_days)

            # Purge business_shared activities older than business cutoff
            b_act_res = await db.execute(
                delete(Activity).where(
                    Activity.business_id == b.id,
                    Activity.visibility == "business_shared",
                    Activity.created_at < b_cutoff,
                )
            )
            # Purge business_shared knowledge documents older than business cutoff (cascades to chunks)
            b_doc_res = await db.execute(
                delete(UserKnowledgeDocument).where(
                    UserKnowledgeDocument.business_id == b.id,
                    UserKnowledgeDocument.visibility == "business_shared",
                    UserKnowledgeDocument.created_at < b_cutoff,
                )
            )
            total_purged += (b_act_res.rowcount or 0) + (b_doc_res.rowcount or 0)

        # 2. Purge personal/private user data based on User retention setting
        users_res = await db.execute(select(User).where(User.deleted_at.is_(None)))
        users = users_res.scalars().all()

        for u in users:
            u_retention_days = getattr(u, "data_retention_days", 365) or 365
            u_cutoff = now - timedelta(days=u_retention_days)

            # Purge raw WhatsApp messages older than user cutoff
            m_res = await db.execute(
                delete(WhatsAppMessage).where(
                    WhatsAppMessage.user_id == u.id,
                    WhatsAppMessage.created_at < u_cutoff,
                )
            )
            # Purge private activities older than user cutoff (business_shared handled above)
            a_res = await db.execute(
                delete(Activity).where(
                    Activity.user_id == u.id,
                    Activity.visibility == "private",
                    Activity.created_at < u_cutoff,
                )
            )
            # Purge private knowledge documents older than user cutoff (cascades to chunks)
            d_res = await db.execute(
                delete(UserKnowledgeDocument).where(
                    UserKnowledgeDocument.user_id == u.id,
                    UserKnowledgeDocument.visibility == "private",
                    UserKnowledgeDocument.created_at < u_cutoff,
                )
            )
            # Purge inactive / completed / abandoned goals older than user cutoff
            g_res = await db.execute(
                delete(UserGoal).where(
                    UserGoal.user_id == u.id,
                    UserGoal.status.in_(["achieved", "abandoned", "completed", "cancelled"]),
                    UserGoal.updated_at < u_cutoff,
                )
            )
            # Purge conversation memories older than user cutoff
            mem_res = await db.execute(
                delete(ConversationMemory).where(
                    ConversationMemory.user_id == u.id,
                    ConversationMemory.created_at < u_cutoff,
                )
            )
            total_purged += (
                (m_res.rowcount or 0)
                + (a_res.rowcount or 0)
                + (d_res.rowcount or 0)
                + (g_res.rowcount or 0)
                + (mem_res.rowcount or 0)
            )

        await db.commit()
        logger.info("Retention purge completed: purged %d total expired records", total_purged)
        return total_purged
