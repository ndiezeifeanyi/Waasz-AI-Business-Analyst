from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import CostLimitExceeded
from app.models.business import Business
from app.models.cost import AiCostEvent

PRICE_PER_1K_TOKENS_USD = {
    "gemini": Decimal("0.0002"),
    "groq": Decimal("0.0008"),
    "openai": Decimal("0.0020"),
    "google_vision": Decimal("0.0015"),
    "tesseract": Decimal("0"),
    "whisper": Decimal("0.0060"),
    "local_heuristic": Decimal("0"),
}


PRICE_PER_OPERATION_USD = {
    "image_gen": Decimal("0.0400"),  # $0.04 per generated/edited image
    "grounded_search": Decimal("0.0350"),  # $35 per 1k Google Search queries = $0.035 per search
}


class CostMonitor:
    def ensure_daily_budget(self, current_spend_usd: Decimal, next_cost_usd: Decimal) -> None:
        limit = Decimal(str(settings.daily_ai_spend_limit_usd))
        if current_spend_usd + next_cost_usd > limit:
            raise CostLimitExceeded("Daily AI spend limit exceeded")

    async def ensure_business_daily_budget(
        self,
        db: AsyncSession,
        business: Business,
        next_cost_usd: Decimal,
    ) -> None:
        current_spend = await self.current_daily_spend(db, business.id)
        limit = Decimal(str(business.daily_ai_spend_limit_usd))
        if current_spend + next_cost_usd > limit:
            raise CostLimitExceeded("Business daily AI spend limit exceeded")

    async def ensure_user_image_budget(
        self,
        db: AsyncSession,
        business_id: UUID,
        limit: int | None = None,
    ) -> None:
        """Enforce strict per-user/business daily cap specifically for image generations."""
        max_images = limit if limit is not None else settings.daily_user_image_limit
        count = await self.current_daily_image_count(db, business_id)
        if count >= max_images:
            raise CostLimitExceeded(
                f"Daily image generation limit reached ({max_images} images per day). Please try again tomorrow."
            )

    async def current_daily_image_count(self, db: AsyncSession, business_id: UUID) -> int:
        result = await db.execute(
            select(func.count(AiCostEvent.id)).where(
                AiCostEvent.business_id == business_id,
                AiCostEvent.operation == "image_gen",
                func.date(AiCostEvent.created_at) == func.current_date(),
            )
        )
        return int(result.scalar_one() or 0)

    async def current_daily_spend(self, db: AsyncSession, business_id: UUID) -> Decimal:
        result = await db.execute(
            select(func.coalesce(func.sum(AiCostEvent.estimated_cost_usd), 0)).where(
                AiCostEvent.business_id == business_id,
                func.date(AiCostEvent.created_at) == func.current_date(),
            )
        )
        return Decimal(result.scalar_one() or 0)

    async def record_cost(
        self,
        db: AsyncSession,
        business_id: UUID | None,
        provider: str,
        model: str,
        operation: str,
        input_tokens: int,
        output_tokens: int,
        estimated_cost_usd: Decimal,
    ) -> AiCostEvent:
        valid_operations = {
            "extraction",
            "confirmation",
            "correction",
            "report",
            "ocr",
            "voice",
            "image_gen",
            "grounded_search",
        }
        db_operation = operation if operation in valid_operations else "extraction"
        metadata = {"original_operation": operation} if db_operation != operation else {}

        # Default flat-rate pricing if not explicitly supplied
        if estimated_cost_usd <= 0 and operation in PRICE_PER_OPERATION_USD:
            estimated_cost_usd = PRICE_PER_OPERATION_USD[operation]

        event = AiCostEvent(
            business_id=business_id,
            provider=provider,
            model=model,
            operation=db_operation,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            estimated_cost_usd=estimated_cost_usd,
            extra_metadata=metadata,
        )
        db.add(event)
        await db.flush()
        return event



def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def estimate_cost_usd(provider: str, input_text: str, output_text: str = "") -> Decimal:
    tokens = Decimal(estimate_tokens(input_text) + estimate_tokens(output_text))
    rate = PRICE_PER_1K_TOKENS_USD.get(provider, Decimal("0.0020"))
    return (tokens / Decimal("1000")) * rate
