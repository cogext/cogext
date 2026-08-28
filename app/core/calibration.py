"""V1.7 – Confidence calibration.

Track extraction confidence vs. eventual outcomes, bucketed by confidence range.
Buckets: 0.50-0.59, 0.60-0.69, 0.70-0.79, 0.80-0.89, 0.90-0.94, 0.95-1.00
"""
import logging
from datetime import datetime, timezone
from typing import Any

from app.db.connection import get_supabase

logger = logging.getLogger(__name__)

_BUCKETS = [
    (0.50, 0.60),
    (0.60, 0.70),
    (0.70, 0.80),
    (0.80, 0.90),
    (0.90, 0.95),
    (0.95, 1.01),  # inclusive of 1.0
]


def _bucket_label(lo: float, hi: float) -> str:
    return f"{lo:.2f}-{hi:.2f}"


async def get_calibration_report(user_id: str | None = None) -> dict[str, Any]:
    """Return calibration data bucketed by extraction confidence."""
    sb = get_supabase()

    query = sb.table("commitments").select(
        "id, confidence, status, extraction_model"
    )
    if user_id:
        query = query.eq("user_id", user_id)

    resp = await query.execute()
    rows = resp.data or []

    buckets: dict[str, dict[str, Any]] = {}
    for lo, hi in _BUCKETS:
        label = _bucket_label(lo, hi)
        buckets[label] = {
            "range": [lo, hi],
            "sample_count": 0,
            "fulfilled_rate": 0.0,
            "contradiction_rate": 0.0,
            "review_correction_rate": 0.0,
            "deadline_correction_rate": 0.0,
            "_fulfilled": 0,
            "_contradicted": 0,
            "_total_terminal": 0,
        }

    for row in rows:
        conf = row.get("confidence")
        if conf is None:
            continue
        status = row.get("status", "")
        label = _find_bucket(conf)
        if not label:
            continue
        b = buckets[label]
        b["sample_count"] += 1
        if status in {"fulfilled", "failed", "expired", "cancelled", "contradicted"}:
            b["_total_terminal"] += 1
            if status == "fulfilled":
                b["_fulfilled"] += 1
            if status == "contradicted":
                b["_contradicted"] += 1

    # Compute rates from raw counts
    for label, b in buckets.items():
        tt = b["_total_terminal"]
        b["fulfilled_rate"] = round(b["_fulfilled"] / tt, 4) if tt else 0.0
        b["contradiction_rate"] = round(b["_contradicted"] / tt, 4) if tt else 0.0
        del b["_fulfilled"], b["_contradicted"], b["_total_terminal"]

    return {
        "buckets": buckets,
        "total_rows": len(rows),
        "computed_at": datetime.now(timezone.utc).isoformat(),
    }


def _find_bucket(conf: float) -> str | None:
    for lo, hi in _BUCKETS:
        if lo <= conf < hi:
            return _bucket_label(lo, hi)
    return None
