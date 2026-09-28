from enum import Enum
import re
from typing import Literal

IntentType = Literal[
    "transaction",
    "reminder",
    "goal",
    "knowledge_store",
    "forget_me",
    "report",
    "general_qa",
]


class IntentRouter:
    """
    Classifies incoming WhatsApp messages into actionable intents:
    - transaction: sales, expenses, inventory (SME flow)
    - reminder: scheduling follow-ups / tasks
    - goal: setting or progressing milestones
    - knowledge_store: saving notes, documents, price lists
    - forget_me: right-to-be-forgotten deletion
    - report: periodic report request
    - general_qa: questions answered via memory & knowledge retrieval
    """

    @classmethod
    def classify(cls, text: str) -> IntentType:
        if not text:
            return "general_qa"

        cleaned = text.strip()
        lower = cleaned.lower()

        # 1. Forget-me / Privacy Purge
        if lower in {"delete my data", "clear my data", "forget me", "delete all my records"} or lower == "confirm delete":
            return "forget_me"

        # 2. Report request
        if "weekly report" in lower or "send report" in lower or "my progress" in lower or "progress report" in lower:
            return "report"

        # 3. Knowledge Base storage triggers
        if (
            lower.startswith("note:")
            or lower.startswith("save note:")
            or lower.startswith("price list:")
            or lower.startswith("document:")
            or lower.startswith("remember that:")
            or lower.startswith("save:")
        ):
            return "knowledge_store"

        # 4. Reminder / Scheduling
        if (
            re.search(r"\b(?:remind me|set (?:a )?reminder|wake me)\b", lower)
            or re.search(r"\b(?:what time should i remind you|remind)\b", lower)
        ):
            return "reminder"

        # 5. Goal / Target tracking
        if (
            re.search(r"\b(?:my goal is|set goal|new goal|target is|milestone|aim to)\b", lower)
            or (re.search(r"\b(?:goal|target)\b", lower) and re.search(r"\b(?:progress|update|completed)\b", lower))
        ):
            return "goal"

        # 6. Transaction (Sales / Expenses / Inventory)
        transaction_indicators = [
            r"\b(?:sold|sale|bought|buy|expense|paid|payment|spent|purchased|restock|cost|revenue)\b",
            r"(?:₦|\bngn\b|\$|\bkes\b|\bghs\b)\s*\d+",
            r"\d+\s*(?:bags?|cartons?|pieces?|pcs?|bottles?|units?|kg)\b",
        ]
        score = sum(1 for pattern in transaction_indicators if re.search(pattern, lower))
        if score >= 1 and re.search(r"\d+", lower):
            return "transaction"

        # 7. Default to General Q&A (answered via RAG + memory)
        return "general_qa"
