from decimal import Decimal

from pydantic import BaseModel


class FounderMetric(BaseModel):
    label: str
    value: int | Decimal | str
