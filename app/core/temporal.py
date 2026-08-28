"""V1.6 – Temporal intelligence: resolve natural-language deadline expressions.

Strategy:
  1. Deterministic parsing via dateutil / regex (no LLM cost, fully testable)
  2. LLM fallback for ambiguous expressions
  3. Always anchor to source_timestamp (NOT server time)
  4. Always store in UTC
"""
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

from app.models.temporal import ResolutionMethod, TemporalResolution

logger = logging.getLogger(__name__)

# Mapping of weekday names → weekday index (Monday=0)
_WEEKDAYS = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
}

_UNITS = {
    "hour": timedelta(hours=1), "hours": timedelta(hours=1),
    "day": timedelta(days=1), "days": timedelta(days=1),
    "week": timedelta(weeks=1), "weeks": timedelta(weeks=1),
    "month": timedelta(days=30), "months": timedelta(days=30),
}


def resolve_deadline(
    raw_expression: str,
    anchor_timestamp: datetime,
    timezone_str: str = "UTC",
) -> TemporalResolution:
    """Resolve *raw_expression* relative to *anchor_timestamp*.

    Returns a :class:`TemporalResolution`.  ``resolved_deadline`` is UTC.
    ``anchor_timestamp`` must be tz-aware; if naive it is treated as UTC.
    """
    if anchor_timestamp.tzinfo is None:
        anchor_timestamp = anchor_timestamp.replace(tzinfo=timezone.utc)

    expr = raw_expression.strip().lower()
    resolved: Optional[datetime] = None
    method: ResolutionMethod = "deterministic"
    ambiguity: Optional[str] = None

    # --- "today" / "tonight" ---
    if re.search(r'\btoday\b|\btonight\b', expr):
        resolved = anchor_timestamp.replace(hour=23, minute=59, second=59, microsecond=0)

    # --- "tomorrow" ---
    elif re.search(r'\btomorrow\b', expr):
        d = anchor_timestamp + timedelta(days=1)
        resolved = d.replace(hour=23, minute=59, second=59, microsecond=0)

    # --- "end of day" / "eod" ---
    elif re.search(r'\bend of day\b|\beod\b', expr):
        resolved = anchor_timestamp.replace(hour=23, minute=59, second=59, microsecond=0)

    # --- "end of week" / "eow" ---
    elif re.search(r'\bend of week\b|\beow\b', expr):
        days_ahead = 4 - anchor_timestamp.weekday()   # Friday
        if days_ahead < 0:
            days_ahead += 7
        d = anchor_timestamp + timedelta(days=days_ahead)
        resolved = d.replace(hour=23, minute=59, second=59, microsecond=0)

    # --- "end of month" / "eom" ---
    elif re.search(r'\bend of month\b|\beom\b', expr):
        import calendar
        last_day = calendar.monthrange(anchor_timestamp.year, anchor_timestamp.month)[1]
        resolved = anchor_timestamp.replace(
            day=last_day, hour=23, minute=59, second=59, microsecond=0
        )

    # --- "by <weekday>" / "next <weekday>" ---
    else:
        wd_match = re.search(
            r'\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b', expr
        )
        if wd_match:
            target_wd = _WEEKDAYS[wd_match.group(1)]
            current_wd = anchor_timestamp.weekday()
            days_ahead = (target_wd - current_wd) % 7 or 7
            # "next <weekday>" always means ≥7 days
            if re.search(r'\bnext\b', expr):
                days_ahead = (target_wd - current_wd) % 7
                if days_ahead == 0:
                    days_ahead = 7
            d = anchor_timestamp + timedelta(days=days_ahead)
            # check for EOD / 5pm qualifier
            if re.search(r'\b(end of day|eod|5\s*pm|17:00)\b', expr):
                resolved = d.replace(hour=17, minute=0, second=0, microsecond=0)
            else:
                resolved = d.replace(hour=23, minute=59, second=59, microsecond=0)

    # --- "in N <unit>" ---
    if resolved is None:
        in_match = re.search(r'\bin\s+(\d+)\s+(\w+)', expr)
        if in_match:
            n = int(in_match.group(1))
            unit = in_match.group(2)
            delta = _UNITS.get(unit)
            if delta:
                resolved = anchor_timestamp + (delta * n)

    # --- "within N <unit>" ---
    if resolved is None:
        within_match = re.search(r'\bwithin\s+(\d+)\s+(\w+)', expr)
        if within_match:
            n = int(within_match.group(1))
            unit = within_match.group(2)
            delta = _UNITS.get(unit)
            if delta:
                resolved = anchor_timestamp + (delta * n)

    # --- ISO 8601 literal ---
    if resolved is None:
        iso_match = re.search(
            r'\d{4}-\d{2}-\d{2}(T\d{2}:\d{2}(:\d{2})?)?', raw_expression
        )
        if iso_match:
            try:
                resolved = datetime.fromisoformat(iso_match.group(0))
                if resolved.tzinfo is None:
                    resolved = resolved.replace(tzinfo=timezone.utc)
            except ValueError:
                pass

    # --- LLM fallback for truly ambiguous expressions ---
    if resolved is None:
        method = "llm_fallback"
        ambiguity = f"Could not deterministically parse: {raw_expression!r}"
        resolved = _llm_resolve(raw_expression, anchor_timestamp)

    # Ensure UTC
    if resolved and resolved.tzinfo is None:
        resolved = resolved.replace(tzinfo=timezone.utc)
    if resolved:
        resolved = resolved.astimezone(timezone.utc)

    return TemporalResolution(
        raw_expression=raw_expression,
        anchor_timestamp=anchor_timestamp,
        timezone=timezone_str,
        resolved_deadline=resolved,
        resolution_method=method,
        confidence=1.0 if method == "deterministic" else 0.7,
        ambiguity=ambiguity,
    )


def _llm_resolve(expression: str, anchor: datetime) -> Optional[datetime]:
    """Best-effort LLM resolution — returns None if LLM unavailable."""
    try:
        from app.llm.provider import extract_completion
        import json
        prompt = (
            f"Resolve this deadline expression to an ISO 8601 UTC datetime.\n"
            f"Expression: {expression!r}\n"
            f"Anchor time (UTC): {anchor.isoformat()}\n"
            f"Return JSON: {{\"resolved\": \"<ISO8601 or null>\"}}"
        )
        raw = extract_completion(prompt)
        data = json.loads(raw)
        val = data.get("resolved")
        if val:
            dt = datetime.fromisoformat(val)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
    except Exception as e:
        logger.warning("LLM temporal fallback failed: %s", e)
    return None
