from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo
import pytest

from app.utils.reminder_parser import (
    clean_reminder_input,
    format_confirmation_time,
    parse_reminder_time,
)


def test_relative_time_parsing():
    base = datetime(2026, 9, 24, 10, 0, 0, tzinfo=UTC)  # 11:00 AM WAT
    
    # "in 2 hours" -> 12:00 UTC
    parsed = parse_reminder_time("in 2 hours", base_time=base)
    assert parsed is not None
    assert parsed == base + timedelta(hours=2)

    # "in 30 minutes"
    parsed = parse_reminder_time("in 30 mins", base_time=base)
    assert parsed is not None
    assert parsed == base + timedelta(minutes=30)


def test_absolute_time_parsing():
    # 2026-09-24 10:00 UTC is 11:00 WAT
    base = datetime(2026, 9, 24, 10, 0, 0, tzinfo=UTC)
    
    # "tomorrow at 3pm" -> 2026-09-25 15:00 WAT -> 14:00 UTC
    parsed = parse_reminder_time("tomorrow at 3pm", base_time=base)
    assert parsed is not None
    assert parsed == datetime(2026, 9, 25, 14, 0, 0, tzinfo=UTC)

    # "Dec 5 at 3pm" -> 2026-12-05 15:00 WAT -> 14:00 UTC
    parsed = parse_reminder_time("Dec 5 at 3pm", base_time=base)
    assert parsed is not None
    assert parsed == datetime(2026, 12, 5, 14, 0, 0, tzinfo=UTC)


def test_nigerian_pidgin_parsing():
    base = datetime(2026, 9, 24, 8, 0, 0, tzinfo=UTC)  # 9:00 AM WAT
    
    # "next tomorrow by 2pm" -> 2 days ahead, 14:00 WAT -> 13:00 UTC
    parsed = parse_reminder_time("next tomorrow by 2pm", base_time=base)
    assert parsed is not None
    assert parsed == datetime(2026, 9, 26, 13, 0, 0, tzinfo=UTC)

    # "small time" -> "in 1 hour"
    parsed = parse_reminder_time("small time", base_time=base)
    assert parsed is not None
    assert parsed == base + timedelta(hours=1)

    # "make you remind me tomoro by 4pm"
    parsed = parse_reminder_time("make you remind me tomoro by 4pm", base_time=base)
    assert parsed is not None
    assert parsed == datetime(2026, 9, 25, 15, 0, 0, tzinfo=UTC)


def test_ambiguous_and_invalid_inputs_rejected():
    base = datetime(2026, 9, 24, 10, 0, 0, tzinfo=UTC)
    
    assert parse_reminder_time("", base_time=base) is None
    assert parse_reminder_time("   ", base_time=base) is None
    assert parse_reminder_time("yes", base_time=base) is None
    assert parse_reminder_time("ok", base_time=base) is None
    assert parse_reminder_time("blue", base_time=base) is None
    assert parse_reminder_time("my friend called me", base_time=base) is None
    assert parse_reminder_time("chicken and chips 5000", base_time=base) is None


def test_midnight_and_timezone_boundary():
    # 23:30 WAT on Sep 24 is 22:30 UTC on Sep 24
    base = datetime(2026, 9, 24, 22, 30, 0, tzinfo=UTC)
    
    # "in 1 hour" will cross midnight in WAT (00:30 WAT on Sep 25, 23:30 UTC on Sep 24)
    parsed = parse_reminder_time("in 1 hour", base_time=base)
    assert parsed is not None
    assert parsed == datetime(2026, 9, 24, 23, 30, 0, tzinfo=UTC)
    
    # Formatted confirmation should show Sep 25 in WAT
    formatted = format_confirmation_time(parsed, "Africa/Lagos")
    assert "25 Sep" in formatted
    assert "WAT" in formatted


def test_ambiguous_time_expressions():
    from app.utils.reminder_parser import is_ambiguous_time_expression

    # Ambiguous phrases without specific time must return True
    assert is_ambiguous_time_expression("remind me to call supplier later today") is True
    assert is_ambiguous_time_expression("remind me sometime today") is True
    assert is_ambiguous_time_expression("call Ngozi later on") is True
    assert is_ambiguous_time_expression("remind me later") is True
    assert is_ambiguous_time_expression("sometime later please") is True

    # Phrases with specific time must return False (not ambiguous)
    assert is_ambiguous_time_expression("remind me later today at 5pm") is False
    assert is_ambiguous_time_expression("remind me in 30 mins") is False
    assert is_ambiguous_time_expression("tomorrow at 9am") is False
    assert is_ambiguous_time_expression("by 3pm") is False
