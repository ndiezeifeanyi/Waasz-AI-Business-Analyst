import re
from decimal import Decimal


def format_naira(amount: Decimal | int | float | None) -> str:
    if amount is None:
        return "unknown amount"
    value = Decimal(str(amount))
    if value == value.to_integral_value():
        return f"₦{value:,.0f}"
    return f"₦{value:,.2f}"


def parse_money_amount(text: str) -> Decimal | None:
    normalized = text.lower().replace(",", "")
    patterns = [
        r"(?:₦|ngn|naira|n)\s*(?P<amount>\d+(?:\.\d+)?)(?P<thousand>k)?\b",
        r"\b(?:for|paid|spent|cost|worth|at)\s+(?:₦|ngn|naira|n)?\s*(?P<amount>\d+(?:\.\d+)?)(?P<thousand>k)?\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, normalized, flags=re.IGNORECASE)
        if match:
            amount = Decimal(match.group("amount"))
            if match.groupdict().get("thousand"):
                amount *= Decimal("1000")
            return amount

    numbers = [
        Decimal(value)
        for value in re.findall(r"\b\d+(?:\.\d+)?\b", normalized)
        if Decimal(value) >= Decimal("100")
    ]
    return numbers[-1] if numbers else None
