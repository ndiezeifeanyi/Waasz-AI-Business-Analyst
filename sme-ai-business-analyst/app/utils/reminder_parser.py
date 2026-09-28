"""
Natural-language reminder and scheduling parser.
Handles relative and absolute time expressions in English and Nigerian Pidgin,
resolves against local timezones (default: Africa/Lagos / WAT), and returns UTC datetime.
"""

from datetime import UTC, datetime, timedelta
import re
from zoneinfo import ZoneInfo
import dateparser

# Common Nigerian Pidgin and informal Nigerian English time expressions
PIDGIN_TIME_REPLACEMENTS = [
    (r"\bnext\s+tomor(?:row|o)\b", "in 2 days"),
    (r"\btomor(?:o|row)\b", "tomorrow"),
    (r"\bdis\s+evening\b", "this evening"),
    (r"\bdis\s+night\b", "tonight"),
    (r"\bdis\s+afternoon\b", "this afternoon"),
    (r"\bdis\s+morning\b", "this morning"),
    (r"\bfor\s+morning\b", "at 9am"),
    (r"\bfor\s+afternoon\b", "at 2pm"),
    (r"\bfor\s+evening\b", "at 6pm"),
    (r"\bfor\s+night\b", "at 8pm"),
    (r"\bsmall\s+time\b", "in 1 hour"),
    (r"\blater\s+today\b", "in 3 hours"),
]

# Patterns that indicate a temporal phrase is present
TEMPORAL_INDICATORS = [
    r"\b(?:today|tomorrow|tomoro|tonight|yesterday)\b",
    r"\b(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday|mon|tue|wed|thu|fri|sat|sun)\b",
    r"\b(?:january|february|march|april|may|june|july|august|september|october|november|december)\b",
    r"\b(?:jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec)\b",
    r"\b(?:in|after)\s+\d+\s*(?:sec|second|min|minute|hr|hour|day|week|month)s?\b",
    r"\b\d{1,2}(?::\d{2})?\s*(?:am|pm)\b",
    r"\b(?:at|by|around)\s+\d{1,2}(?::\d{2})?\b",
    r"\b(?:morning|afternoon|evening|night|noon|midnight)\b",
    r"\bnext\s+(?:week|month|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
    r"\b\d{1,2}(?:st|nd|rd|th)\b",
]


def clean_reminder_input(text: str) -> str:
    """Strip extraneous conversational filler and normalize pidgin phrasing."""
    cleaned = text.strip()
    # Remove leading conversational filler
    filler_patterns = [
        r"^(?:abeg|please|pls)\s*,?\s*",
        r"^(?:make\s+you\s+remind\s+me|remind\s+me(?:\s+for|\s+at|\s+by|\s+on)?)\s*",
        r"^(?:set\s+(?:a\s+)?reminder(?:\s+for|\s+at|\s+by|\s+on)?)\s*",
        r"^(?:i\s+want\s+make\s+you\s+remind\s+me(?:\s+for|\s+at|\s+by)?)\s*",
    ]
    for pattern in filler_patterns:
        cleaned = re.sub(pattern, "", cleaned, flags=re.IGNORECASE).strip()

    # Apply pidgin replacements
    for pattern, replacement in PIDGIN_TIME_REPLACEMENTS:
        cleaned = re.sub(pattern, replacement, cleaned, flags=re.IGNORECASE)

    return cleaned


def has_temporal_content(text: str) -> bool:
    """Check if the text actually contains any temporal indicators."""
    lower = text.lower()
    for pattern in TEMPORAL_INDICATORS:
        if re.search(pattern, lower):
            return True
    return False


def parse_reminder_time(
    text: str,
    base_time: datetime | None = None,
    timezone_name: str = "Africa/Lagos",
) -> datetime | None:
    """
    Parse a natural-language time string into a UTC datetime.
    
    Args:
        text: Natural language string (e.g. "in 2 hours", "tomorrow at 3pm", "next tomorrow by 4pm")
        base_time: Reference time for relative calculations (defaults to current time in specified timezone)
        timezone_name: Local timezone name for the business (default "Africa/Lagos")
        
    Returns:
        UTC datetime if parsed successfully and unambiguously, or None if ambiguous or unparseable.
    """
    if not text or not text.strip():
        return None

    cleaned = clean_reminder_input(text)
    if not cleaned:
        return None

    # Verify input has actual temporal intent (prevent guessing on "yes", "blue", "ok")
    if not has_temporal_content(cleaned):
        return None

    try:
        local_tz = ZoneInfo(timezone_name)
    except Exception:
        local_tz = ZoneInfo("Africa/Lagos")

    now_local = (base_time or datetime.now(UTC)).astimezone(local_tz)

    settings = {
        "PREFER_DATES_FROM": "future",
        "TIMEZONE": timezone_name,
        "TO_TIMEZONE": "UTC",
        "RETURN_AS_TIMEZONE_AWARE": True,
        "RELATIVE_BASE": now_local.replace(tzinfo=None),
    }

    parsed = dateparser.parse(cleaned, settings=settings)
    if not parsed:
        return None

    # Ensure UTC timezone awareness
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    else:
        parsed = parsed.astimezone(UTC)

    # Disallow parsed times that ended up in the past relative to now (with 30-second leeway)
    now_utc = (base_time or datetime.now(UTC)).astimezone(UTC)
    if parsed < (now_utc - timedelta(seconds=30)):
        # If user said e.g. "at 9am" and it's already 2pm today, dateparser might return past 9am.
        # Advance by 1 day if it was intended for the next occurrence:
        if (now_utc - parsed) < timedelta(hours=24):
            parsed = parsed + timedelta(days=1)
        else:
            return None

    return parsed


def format_confirmation_time(dt_utc: datetime, timezone_name: str = "Africa/Lagos") -> str:
    """
    Format a UTC datetime into a human-friendly confirmation string in the business's local timezone.
    Example output: 'Fri 26 Sep at 3:00 PM WAT'
    """
    try:
        local_tz = ZoneInfo(timezone_name)
    except Exception:
        local_tz = ZoneInfo("Africa/Lagos")

    local_dt = dt_utc.astimezone(local_tz)
    # Use standard format: Day Mon Date at HH:MM AM/PM TZ
    formatted_date = local_dt.strftime("%a %d %b at %I:%M %p")
    # Clean leading zeros on hour if present: e.g. "at 03:00 PM" -> "at 3:00 PM"
    formatted_date = re.sub(r"at 0(\d:\d{2})", r"at \1", formatted_date)
    
    tz_abbrev = "WAT" if "Lagos" in timezone_name else local_dt.strftime("%Z")
    return f"{formatted_date} {tz_abbrev}"
