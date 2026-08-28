"""V1.6 – Reliability 2.0: per-agent reliability metrics."""
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from app.db.connection import get_supabase

logger = logging.getLogger(__name__)


async def get_reliability_metrics(
    user_id: uuid.UUID,
    source_agent_id: uuid.UUID | None = None,
    since: datetime | None = None,
) -> dict[str, Any]:
    """Compute reliability metrics for a user/agent.

    Metrics:
      fulfillment_rate       – fulfilled / (fulfilled + failed + expired)
      on_time_rate           – fulfilled_on_time / fulfilled
      evidence_quality       – avg evidence score for verified commitments
      deadline_accuracy      – % of deadlines that were accurate (not changed)
      contradiction_rate     – contradicted / total
      cancellation_rate      – cancelled / total
      trend                  – "improving" | "stable" | "declining" | "insufficient_data"
    """
    sb = get_supabase()

    query = sb.table("commitments").select(
        "id, status, confidence, created_at, resolved_at, due_condition"
    ).eq("user_id", str(user_id))

    if source_agent_id:
        query = query.eq("source_agent_id", str(source_agent_id))
    if since:
        query = query.gte("created_at", since.isoformat())

    resp = await query.execute()
    rows = resp.data or []

    total = len(rows)
    if total == 0:
        return _empty_metrics(user_id, source_agent_id)

    fulfilled = sum(1 for r in rows if r["status"] == "fulfilled")
    failed    = sum(1 for r in rows if r["status"] == "failed")
    expired   = sum(1 for r in rows if r["status"] == "expired")
    cancelled = sum(1 for r in rows if r["status"] == "cancelled")
    contradicted = sum(1 for r in rows if r["status"] == "contradicted")

    terminal_negative = fulfilled + failed + expired
    fulfillment_rate = fulfilled / terminal_negative if terminal_negative else 0.0

    # On-time rate: fulfilled commitments whose resolved_at <= deadline
    on_time = 0
    on_time_eligible = 0
    for r in rows:
        if r["status"] != "fulfilled":
            continue
        due = (r.get("due_condition") or {}).get("deadline")
        resolved = r.get("resolved_at")
        if due and resolved:
            on_time_eligible += 1
            try:
                d_dt = datetime.fromisoformat(due)
                r_dt = datetime.fromisoformat(resolved)
                if r_dt <= d_dt:
                    on_time += 1
            except ValueError:
                pass

    on_time_rate = on_time / on_time_eligible if on_time_eligible else None

    contradiction_rate  = contradicted / total
    cancellation_rate   = cancelled / total

    # Trend: compare first half vs second half fulfillment rate
    trend = _compute_trend(rows)

    return {
        "user_id": str(user_id),
        "source_agent_id": str(source_agent_id) if source_agent_id else None,
        "total_commitments": total,
        "fulfillment_rate": round(fulfillment_rate, 4),
        "on_time_rate": round(on_time_rate, 4) if on_time_rate is not None else None,
        "evidence_quality": None,  # requires join with evidence table
        "deadline_accuracy": None, # requires refinement history
        "contradiction_rate": round(contradiction_rate, 4),
        "cancellation_rate": round(cancellation_rate, 4),
        "trend": trend,
        "computed_at": datetime.now(timezone.utc).isoformat(),
    }


def _compute_trend(rows: list[dict]) -> str:
    if len(rows) < 6:
        return "insufficient_data"
    sorted_rows = sorted(rows, key=lambda r: r.get("created_at") or "")
    mid = len(sorted_rows) // 2
    first_half = sorted_rows[:mid]
    second_half = sorted_rows[mid:]

    def _rate(group: list) -> float:
        pos = sum(1 for r in group if r["status"] == "fulfilled")
        neg = sum(1 for r in group if r["status"] in {"failed", "expired"})
        return pos / (pos + neg) if (pos + neg) else 0.0

    r1, r2 = _rate(first_half), _rate(second_half)
    if r2 - r1 > 0.05:
        return "improving"
    if r1 - r2 > 0.05:
        return "declining"
    return "stable"


def _empty_metrics(user_id: uuid.UUID, agent_id: uuid.UUID | None) -> dict:
    return {
        "user_id": str(user_id),
        "source_agent_id": str(agent_id) if agent_id else None,
        "total_commitments": 0,
        "fulfillment_rate": 0.0,
        "on_time_rate": None,
        "evidence_quality": None,
        "deadline_accuracy": None,
        "contradiction_rate": 0.0,
        "cancellation_rate": 0.0,
        "trend": "insufficient_data",
        "computed_at": datetime.now(timezone.utc).isoformat(),
    }
