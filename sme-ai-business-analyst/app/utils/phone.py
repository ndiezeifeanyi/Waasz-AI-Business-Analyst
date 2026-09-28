import re


def normalize_phone(phone: str) -> str:
    digits = re.sub(r"\D+", "", phone)
    if digits.startswith("0"):
        digits = "234" + digits[1:]
    return digits
