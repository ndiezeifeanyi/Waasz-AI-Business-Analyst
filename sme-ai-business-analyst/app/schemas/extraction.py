from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, field_serializer


class ExtractedRecord(BaseModel):
    record_type: Literal[
        "sale", "expense", "inventory_update", "task", "activity", "unknown"
    ]
    item_name: str | None = None
    description: str | None = None
    quantity: Decimal | None = None
    unit: str | None = None
    unit_price: Decimal | None = None
    amount: Decimal | None = None
    currency: str = "NGN"
    due_at: str | None = None
    category: str | None = None
    is_credit: bool = False
    customer_name: str | None = None
    due_date: str | None = None
    confidence: float = Field(default=0, ge=0, le=1)
    needs_clarification: bool = False
    clarification_question: str | None = None

    @field_serializer("amount", "unit_price", "quantity", mode="plain", when_used="json")
    def serialize_decimal_as_number(self, v: Decimal | None) -> float | int | None:
        if v is None:
            return None
        return int(v) if v % 1 == 0 else float(v)

