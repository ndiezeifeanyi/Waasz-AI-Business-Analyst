import re
from decimal import Decimal
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import CostLimitExceeded
from app.schemas.extraction import ExtractedRecord
from app.services.ai_client import AiClient
from app.services.prompt_loader import render_prompt
from app.utils.currency import parse_money_amount

UNIT_WORDS = {
    "bag",
    "bags",
    "carton",
    "cartons",
    "crate",
    "crates",
    "piece",
    "pieces",
    "pack",
    "packs",
    "bottle",
    "bottles",
    "kg",
    "kilo",
    "kilos",
    "litre",
    "litres",
    "liter",
    "liters",
}


def _clean_item_words(value: str) -> tuple[str | None, str | None]:
    words = [word for word in re.split(r"\s+", value.strip(" .,-")) if word and word != "of"]
    if not words:
        return None, None
    first = words[0].lower()
    if first in UNIT_WORDS and len(words) > 1:
        return " ".join(words[1:]).strip(), first
    return " ".join(words).strip(), None


def _extract_quantity_item(text: str) -> tuple[Decimal | None, str | None, str | None]:
    pattern = (
        r"\b(?:sold|sell|sale|bought|buy|paid for|received|restocked|stocked|added|add)\s+"
        r"(?P<qty>\d+(?:\.\d+)?)\s+(?P<item>.+?)(?:\s+(?:for|at|worth|cost)\b|$)"
    )
    match = re.search(pattern, text, flags=re.IGNORECASE)
    if not match:
        return None, None, None
    quantity = Decimal(match.group("qty"))
    item_name, unit = _clean_item_words(match.group("item"))
    return quantity, item_name, unit


def _record_type(text: str) -> str:
    lower = text.lower()
    inventory_keywords = (
        "received",
        "restocked",
        "stocked",
        "new stock",
        "added stock",
        "inventory",
        "remaining",
        "left in stock",
    )
    sale_keywords = ("sold", "sell", "sales", "customer buy", "customer bought", "we sell")
    expense_keywords = (
        "bought",
        "paid",
        "spent",
        "expense",
        "fuel",
        "rent",
        "salary",
        "transport",
        "repair",
        "electricity",
    )
    if any(keyword in lower for keyword in inventory_keywords):
        return "inventory_update"
    if any(keyword in lower for keyword in sale_keywords):
        return "sale"
    if any(keyword in lower for keyword in expense_keywords):
        return "expense"
    return "unknown"


class AiExtractionService:
    def __init__(self, ai_client: AiClient | None = None) -> None:
        self.ai_client = ai_client or AiClient()
        self.last_provider = "local_heuristic"
        self.last_model = "local-regex-v1"
        self.last_input_tokens = 0
        self.last_output_tokens = 0
        self.last_estimated_cost_usd = 0

    async def extract(
        self,
        text: str,
        *,
        db: AsyncSession | None = None,
        business_id: UUID | None = None,
    ) -> ExtractedRecord:
        ai_record = await self._extract_with_ai(text, db=db, business_id=business_id)
        if ai_record:
            return ai_record
        return self._extract_with_rules(text)

    async def _extract_with_ai(
        self,
        text: str,
        *,
        db: AsyncSession | None = None,
        business_id: UUID | None = None,
    ) -> ExtractedRecord | None:
        system_prompt, user_prompt = render_prompt("extraction", message=text)
        try:
            result = await self.ai_client.complete_json(
                system_prompt,
                user_prompt,
                operation="extraction",
                db=db,
                business_id=business_id,
            )
        except CostLimitExceeded:
            raise
        except Exception:
            return None
        if not result:
            return None
        try:
            record = ExtractedRecord.model_validate(result.content)
            self.last_provider = result.provider
            self.last_model = result.model
            self.last_input_tokens = result.input_tokens
            self.last_output_tokens = result.output_tokens
            self.last_estimated_cost_usd = result.estimated_cost_usd
            return record
        except Exception:
            return None

    def _extract_with_rules(self, text: str) -> ExtractedRecord:
        self.last_provider = "local_heuristic"
        self.last_model = "local-regex-v1"
        self.last_input_tokens = 0
        self.last_output_tokens = 0
        self.last_estimated_cost_usd = 0
        cleaned = re.sub(r"\s+", " ", text.strip())
        if not cleaned:
            return ExtractedRecord(
                record_type="unknown",
                confidence=0,
                needs_clarification=True,
                clarification_question="Please send the business record again.",
            )

        record_type = _record_type(cleaned)
        amount = parse_money_amount(cleaned)
        quantity, item_name, unit = _extract_quantity_item(cleaned)

        if record_type == "unknown":
            return ExtractedRecord(
                record_type="unknown",
                description=cleaned,
                confidence=0.15,
                needs_clarification=True,
                clarification_question=(
                    "I no understand the record well. Please send am like "
                    '"Sold 5 bags rice for ₦250000".'
                ),
            )

        if record_type in {"sale", "expense"} and amount is None:
            return ExtractedRecord(
                record_type=record_type,
                item_name=item_name,
                quantity=quantity,
                unit=unit,
                description=cleaned,
                confidence=0.45,
                needs_clarification=True,
                clarification_question="Please include the naira amount for this record.",
            )

        if item_name is None:
            item_name = _fallback_item_name(cleaned, record_type)

        return ExtractedRecord(
            record_type=record_type,
            item_name=item_name,
            quantity=quantity,
            unit=unit,
            amount=amount,
            description=cleaned,
            confidence=0.72 if item_name else 0.55,
            needs_clarification=False,
        )


def _fallback_item_name(text: str, record_type: str) -> str | None:
    lower = text.lower()
    prefixes = {
        "sale": ("sold", "sell"),
        "expense": ("bought", "paid for", "paid", "spent on", "spent"),
        "inventory_update": ("received", "restocked", "stocked", "added", "add"),
    }
    for prefix in prefixes.get(record_type, ()):
        if prefix in lower:
            after = lower.split(prefix, 1)[1]
            after = re.split(r"\b(?:for|at|worth|cost)\b", after, maxsplit=1)[0]
            after = re.sub(r"\b\d+(?:\.\d+)?\b", "", after)
            item_name, _ = _clean_item_words(after)
            return item_name
    return None
