import hashlib
import re

VALID_NG_MOBILE_PREFIXES = (
    # MTN, Airtel, Glo, 9mobile prefixes (first 2 digits of the 10-digit national number)
    "70",
    "80",
    "81",
    "90",
    "91",
)


def normalize_phone(phone: str) -> str:
    """Returns digits starting with 234 for Nigerian numbers, or bare digits."""
    if not phone:
        return ""
    digits = re.sub(r"\D+", "", phone)
    if digits.startswith("0") and len(digits) == 11:
        digits = "234" + digits[1:]
    return digits


def normalize_e164_phone(phone: str | None) -> str:
    """
    Normalizes a phone number to standard E.164 format (+234...).
    Accepts 080..., 234..., +234..., or international formats.
    """
    if not phone:
        return ""
    raw = str(phone).strip()
    digits = re.sub(r"\D+", "", raw)
    if not digits:
        return ""

    if digits.startswith("0") and len(digits) == 11:
        return "+234" + digits[1:]
    if digits.startswith("234") and len(digits) == 13:
        return "+" + digits
    if len(digits) == 10 and digits[:2] in VALID_NG_MOBILE_PREFIXES:
        return "+234" + digits
    if raw.startswith("+"):
        return "+" + digits
    return "+" + digits


def validate_nigerian_phone(phone: str | None) -> tuple[bool, str, str | None]:
    """
    Strictly validates a Nigerian debtor phone number for E.164 (+234...).
    Returns (is_valid, e164_phone, error_message).
    Valid Nigerian mobile numbers must have 10 national digits starting with 70, 80, 81, 90, 91.
    """
    if not phone:
        return False, "", "Phone number is required."
    raw = str(phone).strip()
    digits = re.sub(r"\D+", "", raw)

    if not digits:
        return False, "", f"Invalid phone format: '{phone}' contains no digits."

    national_digits: str | None = None
    if digits.startswith("0") and len(digits) == 11:
        national_digits = digits[1:]
    elif digits.startswith("234") and len(digits) == 13:
        national_digits = digits[3:]
    elif len(digits) == 10:
        national_digits = digits
    else:
        return (
            False,
            "",
            f"Invalid Nigerian phone length for '{phone}'. Expected 11 digits (e.g. 08031234567) or 13 with 234.",
        )

    prefix = national_digits[:2]
    if prefix not in VALID_NG_MOBILE_PREFIXES:
        return (
            False,
            "",
            f"Invalid mobile operator prefix '{prefix}' in '{phone}'. Valid Nigerian mobile prefixes start with 070, 080, 081, 090, 091.",
        )

    e164 = f"+234{national_digits}"
    return True, e164, None


import hmac


def legacy_unkeyed_hash_phone(phone: str | None) -> str:
    """
    Computes legacy unkeyed SHA-256 hash for backward compatibility.
    Used during lookup so existing suppressed debtor phones hashed before the migration are still honored.
    """
    norm = normalize_e164_phone(phone)
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()


def hash_phone_for_suppression(phone: str | None, secret_key: str | None = None) -> str:
    """
    Computes a keyed HMAC-SHA256 hash of the normalized E.164 phone number
    using a dedicated secret key from the environment (settings.suppression_secret_key).
    Prevents rainbow table and dictionary inversion of suppressed debtor phones.
    Refuses to execute if no dedicated key is configured.
    """
    from app.core.config import settings

    key_str = (
        secret_key
        or getattr(settings, "suppression_secret_key", None)
    )
    if not key_str:
        raise RuntimeError(
            "SUPPRESSION_SECRET_KEY is required and cannot be empty. "
            "Refusing to hash debtor phone without dedicated suppression key."
        )
    norm = normalize_e164_phone(phone)
    return hmac.new(
        key_str.encode("utf-8"), norm.encode("utf-8"), hashlib.sha256
    ).hexdigest()
