import logging
from uuid import UUID

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_google_genai import ChatGoogleGenerativeAI
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.user import User
from app.services.goal_service import GoalService
from app.services.knowledge_service import KnowledgeService
from app.services.memory_service import MemoryService

logger = logging.getLogger(__name__)


NICHE_PERSONAS = {
    "sme_owner": (
        "You are an expert SME business advisor and financial analyst in Nigeria. "
        "Keep responses practical, concise, and focused on revenue, costs, stock turnover, and cash flow."
    ),
    "employee_9_to_5": (
        "You are an executive career coach and workplace productivity assistant. "
        "Help the user manage projects, prepare for reviews, track deliverables, and manage work-life balance."
    ),
    "freelancer": (
        "You are a professional freelance business manager. "
        "Focus on client communication, contract milestones, invoicing, proposal deadlines, and pipeline growth."
    ),
    "student": (
        "You are an encouraging academic study coach. "
        "Help the user prepare for exams, break down study schedules, track assignments, and maintain consistency."
    ),
    "personal": (
        "You are a versatile personal AI assistant. "
        "Help the user organize daily life, remember important commitments, and achieve personal goals."
    ),
}


class QaService:
    def __init__(
        self,
        memory: MemoryService | None = None,
        knowledge: KnowledgeService | None = None,
        goals: GoalService | None = None,
    ) -> None:
        self.memory = memory or MemoryService()
        self.knowledge = knowledge or KnowledgeService()
        self.goals = goals or GoalService()

    async def answer(self, db: AsyncSession, user: User, user_message: str) -> str:
        """
        Synthesize answer using Short-Term Turns + Long-Term Memories + Knowledge Chunks + Active Goals.
        Strictly isolated to user.id.
        """
        # 1. Fetch short-term turns
        turns = []
        try:
            turns = await self.memory.get_short_term_turns(db, user.id, limit=6)
        except Exception as exc:
            logger.warning("Failed to fetch short term turns for user %s: %s", user.id, exc)

        # 2. Fetch long-term semantic memories
        memories = []
        try:
            memories = await self.memory.retrieve_relevant_memories(db, user.id, user_message, top_k=3)
        except Exception as exc:
            logger.warning("Failed to retrieve memories for user %s: %s", user.id, exc)

        # 3. Fetch knowledge chunks (scoped to user and business_shared if user has business_id)
        chunks = []
        try:
            chunks = await self.knowledge.search_user_knowledge(
                db, user.id, user_message, business_id=user.business_id, top_k=3
            )
        except Exception as exc:
            logger.warning("Failed to search knowledge for user %s: %s", user.id, exc)

        # 4. Fetch active goals
        active_goals = []
        try:
            active_goals = await self.goals.get_active_goals(db, user.id)
        except Exception as exc:
            logger.warning("Failed to get active goals for user %s: %s", user.id, exc)

        # Construct persona based on niche
        niche_key = user.niche or "sme_owner"
        persona = NICHE_PERSONAS.get(niche_key, NICHE_PERSONAS["personal"])

        system_prompt = f"{persona}\n\n"
        if memories:
            memory_block = "\n".join(f"- {m['content']}" for m in memories)
            system_prompt += f"DURABLE USER MEMORY & PREFERENCES:\n{memory_block}\n\n"

        if chunks:
            chunk_block = "\n".join(f"- {c['content']}" for c in chunks)
            system_prompt += f"RELEVANT USER DOCUMENTS / NOTES:\n{chunk_block}\n\n"

        if active_goals:
            goal_block = "\n".join(f"- {g.title} (Progress: {g.current_value}/{g.target_value or 'target'})" for g in active_goals)
            system_prompt += f"ACTIVE USER GOALS:\n{goal_block}\n\n"

        system_prompt += (
            "CRITICAL GROUNDING RULES:\n"
            "1. Ground all answers strictly in the retrieved facts provided above (user notes, memories, goals).\n"
            "2. If asked about personal or business records and NO relevant facts or documents appear in the context above, "
            "state honestly that no matching records were found, and answer helpfully and generally using your expert reasoning "
            "WITHOUT inventing specific facts (names, numbers, prices, dates) or implying they came from user records.\n"
            "3. Answer directly, concisely, and naturally for WhatsApp chat. Use clear bullet points if listing items. Avoid fluff."
        )

        messages = [SystemMessage(content=system_prompt)]
        for turn in turns:
            if turn["role"] == "user":
                messages.append(HumanMessage(content=turn["content"]))
            else:
                messages.append(AIMessage(content=turn["content"]))
        messages.append(HumanMessage(content=user_message))

        # Query LLM Provider Chain: Gemini -> Groq -> OpenAI with live model resolution and self-healing
        from app.core.model_resolver import is_model_not_found_error, model_resolver

        # 1. Primary: Gemini
        if settings.gemini_api_key and settings.gemini_api_key != "placeholder_gemini_key":
            gemini_model = model_resolver.get_model("gemini", "chat")
            try:
                llm = ChatGoogleGenerativeAI(
                    model=gemini_model,
                    google_api_key=settings.gemini_api_key,
                    temperature=0.3,
                    max_retries=1,
                )
                response = await llm.ainvoke(messages)
                return str(response.content).strip()
            except Exception as exc:
                if is_model_not_found_error(exc):
                    did_heal, new_model = await model_resolver.handle_mid_run_failure("gemini", "chat", exc)
                    if did_heal:
                        try:
                            llm = ChatGoogleGenerativeAI(
                                model=new_model,
                                google_api_key=settings.gemini_api_key,
                                temperature=0.3,
                                max_retries=1,
                            )
                            response = await llm.ainvoke(messages)
                            return str(response.content).strip()
                        except Exception as retry_exc:
                            exc = retry_exc
                logger.warning("Gemini LLM call failed in QaService: %s. Trying Groq fallback.", exc)

        # 2. Secondary: Groq Fallback
        if settings.groq_api_key and settings.groq_api_key != "placeholder_groq_key":
            groq_model = model_resolver.get_model("groq", "chat")
            try:
                from langchain_groq import ChatGroq

                groq_llm = ChatGroq(
                    model=groq_model,
                    api_key=settings.groq_api_key,
                    temperature=0.3,
                    max_retries=1,
                )
                response = await groq_llm.ainvoke(messages)
                return str(response.content).strip()
            except Exception as exc:
                if is_model_not_found_error(exc):
                    did_heal, new_model = await model_resolver.handle_mid_run_failure("groq", "chat", exc)
                    if did_heal:
                        try:
                            groq_llm = ChatGroq(
                                model=new_model,
                                api_key=settings.groq_api_key,
                                temperature=0.3,
                                max_retries=1,
                            )
                            response = await groq_llm.ainvoke(messages)
                            return str(response.content).strip()
                        except Exception as retry_exc:
                            exc = retry_exc
                logger.warning("Groq LLM call failed in QaService: %s. Trying OpenAI fallback.", exc)

        # 3. Tertiary: OpenAI Fallback
        if settings.openai_api_key and settings.openai_api_key != "placeholder_openai_key":
            openai_model = model_resolver.get_model("openai", "chat")
            try:
                from langchain_openai import ChatOpenAI

                openai_llm = ChatOpenAI(
                    model=openai_model,
                    api_key=settings.openai_api_key,
                    temperature=0.3,
                    max_retries=1,
                )
                response = await openai_llm.ainvoke(messages)
                return str(response.content).strip()
            except Exception as exc:
                if is_model_not_found_error(exc):
                    did_heal, new_model = await model_resolver.handle_mid_run_failure("openai", "chat", exc)
                    if did_heal:
                        try:
                            openai_llm = ChatOpenAI(
                                model=new_model,
                                api_key=settings.openai_api_key,
                                temperature=0.3,
                                max_retries=1,
                            )
                            response = await openai_llm.ainvoke(messages)
                            return str(response.content).strip()
                        except Exception as retry_exc:
                            exc = retry_exc
                logger.warning("OpenAI LLM call failed in QaService: %s.", exc)

        # Honest failure degradation (never return fake canned text disguised as an answer)
        if chunks:
            return f"⚠️ AI service is temporarily degraded, but here is what I found in your saved notes:\n• {chunks[0]['content']}"
        if memories:
            return f"⚠️ AI service is temporarily degraded, but from what you noted earlier:\n• {memories[0]['content']}"
        return "⚠️ I'm sorry, I'm currently having trouble connecting to my reasoning services. Please try again in a few moments."
