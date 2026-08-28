"""V1.6 – Unit tests for temporal resolution (no DB, no LLM required)."""
from datetime import datetime, timedelta, timezone

import pytest

from app.core.temporal import resolve_deadline


_ANCHOR = datetime(2026, 8, 28, 9, 0, 0, tzinfo=timezone.utc)  # Friday


def test_today_resolves_to_end_of_anchor_day():
    r = resolve_deadline("today", _ANCHOR)
    assert r.resolved_deadline is not None
    assert r.resolved_deadline.date() == _ANCHOR.date()
    assert r.resolution_method == "deterministic"


def test_tomorrow_resolves_to_next_day():
    r = resolve_deadline("tomorrow", _ANCHOR)
    expected = (_ANCHOR + timedelta(days=1)).date()
    assert r.resolved_deadline.date() == expected


def test_eod_resolves_to_23_59():
    r = resolve_deadline("end of day", _ANCHOR)
    assert r.resolved_deadline.hour == 23
    assert r.resolved_deadline.minute == 59


def test_friday_with_5pm():
    r = resolve_deadline("by Friday at 5pm", _ANCHOR)
    # Anchor IS Friday — next Friday is 7 days ahead
    assert r.resolved_deadline is not None
    assert r.resolved_deadline.weekday() == 4  # Friday
    assert r.resolved_deadline.hour == 17


def test_tuesday_from_friday():
    r = resolve_deadline("by Tuesday end of day", _ANCHOR)
    assert r.resolved_deadline is not None
    assert r.resolved_deadline.weekday() == 1  # Tuesday


def test_in_3_days():
    r = resolve_deadline("in 3 days", _ANCHOR)
    expected = (_ANCHOR + timedelta(days=3)).date()
    assert r.resolved_deadline.date() == expected


def test_in_2_weeks():
    r = resolve_deadline("in 2 weeks", _ANCHOR)
    expected = (_ANCHOR + timedelta(weeks=2)).date()
    assert r.resolved_deadline.date() == expected


def test_iso_literal():
    r = resolve_deadline("2026-09-15", _ANCHOR)
    assert r.resolved_deadline is not None
    assert r.resolved_deadline.year == 2026
    assert r.resolved_deadline.month == 9
    assert r.resolved_deadline.day == 15
    assert r.resolution_method == "deterministic"


def test_result_is_always_utc():
    r = resolve_deadline("tomorrow", _ANCHOR)
    assert r.resolved_deadline.tzinfo is not None
    assert r.resolved_deadline.tzinfo == timezone.utc


def test_anchor_timestamp_is_preserved():
    r = resolve_deadline("tomorrow", _ANCHOR)
    assert r.anchor_timestamp == _ANCHOR


def test_end_of_week():
    r = resolve_deadline("end of week", _ANCHOR)
    # From Friday, end of week = same day (Friday) or next Friday
    assert r.resolved_deadline is not None
    assert r.resolved_deadline.weekday() == 4  # Friday


def test_ambiguous_returns_llm_fallback_method():
    """A truly unparseable expression falls back to LLM (or returns None)."""
    r = resolve_deadline("sometime in the indefinite future", _ANCHOR)
    # Either resolved via LLM fallback or unresolved
    assert r.resolution_method in ("llm_fallback", "deterministic")


# ── Regression tests for "next <weekday>" bug fix ──────────────────────────

def test_next_tuesday_from_wednesday():
    """'next Tuesday' from Wednesday must be 6 days ahead (next week), not same week."""
    anchor = datetime(2026, 8, 26, 9, 0, tzinfo=timezone.utc)  # Wednesday
    result = resolve_deadline("next tuesday", anchor)
    delta = (result.resolved_deadline - anchor).days
    assert delta >= 7, f"Expected ≥7 days for 'next tuesday' from Wednesday, got {delta}"


def test_next_monday_from_friday():
    """'next Monday' from Friday: Monday is 3 days ahead in same week → must jump to 10 days."""
    anchor = datetime(2026, 8, 28, 9, 0, tzinfo=timezone.utc)  # Friday
    result = resolve_deadline("next monday", anchor)
    delta = (result.resolved_deadline - anchor).days
    assert delta >= 7, f"Expected ≥7 days for 'next monday' from Friday, got {delta}"


def test_next_friday_from_friday():
    """'next Friday' from Friday (same day): must be exactly 7 days ahead."""
    anchor = datetime(2026, 8, 28, 9, 0, tzinfo=timezone.utc)  # Friday
    result = resolve_deadline("next friday", anchor)
    delta = (result.resolved_deadline - anchor).days
    assert delta == 7, f"Expected 7 days for 'next friday' from Friday, got {delta}"


def test_by_weekday_without_next_uses_nearest():
    """'by Tuesday' (no 'next') from Friday resolves to coming Tuesday (4 days)."""
    anchor = datetime(2026, 8, 28, 9, 0, tzinfo=timezone.utc)  # Friday
    result = resolve_deadline("by tuesday", anchor)
    delta = (result.resolved_deadline - anchor).days
    assert delta == 4, f"Expected 4 days for 'by tuesday' from Friday, got {delta}"
