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


def amount_to_words(amount: Decimal | int | float | None) -> str:
    """Converts a numerical Naira amount to formal capitalized words, e.g. 300000 -> 'THREE HUNDRED THOUSAND NAIRA ONLY'."""
    if amount is None:
        return "ZERO NAIRA ONLY"
    try:
        val = int(Decimal(str(amount)))
    except Exception:
        return "ZERO NAIRA ONLY"
    if val <= 0:
        return "ZERO NAIRA ONLY"

    ones = [
        "", "ONE", "TWO", "THREE", "FOUR", "FIVE", "SIX", "SEVEN", "EIGHT", "NINE",
        "TEN", "ELEVEN", "TWELVE", "THIRTEEN", "FOURTEEN", "FIFTEEN", "SIXTEEN",
        "SEVENTEEN", "EIGHTEEN", "NINETEEN",
    ]
    tens = ["", "", "TWENTY", "THIRTY", "FORTY", "FIFTY", "SIXTY", "SEVENTY", "EIGHTY", "NINETY"]

    def convert_below_thousand(n: int) -> str:
        if n == 0:
            return ""
        if n < 20:
            return ones[n]
        if n < 100:
            return tens[n // 10] + (" " + ones[n % 10] if n % 10 != 0 else "")
        return ones[n // 100] + " HUNDRED" + (" AND " + convert_below_thousand(n % 100) if n % 100 != 0 else "")

    parts = []
    billions = val // 1000000000
    if billions:
        parts.append(convert_below_thousand(billions) + " BILLION")
        val %= 1000000000

    millions = val // 1000000
    if millions:
        parts.append(convert_below_thousand(millions) + " MILLION")
        val %= 1000000

    thousands = val // 1000
    if thousands:
        parts.append(convert_below_thousand(thousands) + " THOUSAND")
        val %= 1000

    if val:
        parts.append(convert_below_thousand(val))

    res = " ".join(parts).strip()
    return f"({res} NAIRA ONLY)"

