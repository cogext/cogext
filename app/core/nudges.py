"""V1.6 – Proactive lifecycle nudges.

Nudge windows: 24 h before deadline, 4 h before deadline, overdue.
Each nudge is:
  - Advisory only (does NOT change status)
  - Event-backed (nudge_sent event inserted)
  - Idempotent (deduped by commitment_id + deadline_window)
"""
import logging
import uuid
from datetime import datetime, timedelta, timezone

from app.db.connection import get_supabase

logger = logging.getLogger(__name__)

# Nudge windows: (label, timedelta_before_deadline_or_None_for_overdue)
_WINDOWS: list[tuple[str, timedelta | None]] = [
    ("24h",     timedelta(hours=24)),
    ("4h",      timedelta(hours=4)),
    ("overdue", None),
]


async def run_nudges() -> int:
    """Scan open commitments and emit due nudge events.  Returns nudge count."""
    sb = get_supabase()
    now = datetime.now(timezone.utc)
    nudge_count = 0

    # Only time-based open/due commitments
    resp = await sb.table("commitments").select(
        "id, due_condition, status"
    ).in_("status", ["open", "due"]).filter(
        "due_condition->>type", "eq", "time"
    ).execute()

    for row in resp.data or []:
        cid = row["id"]
        due_cond = row.get("due_condition") or {}
        deadline_str = due_cond.get("deadline")
        if not deadline_str:
            continue

        try:
            deadline = datetime.fromisoformat(deadline_str)
            if deadline.tzinfo is None:
                deadline = deadline.replace(tzinfo=timezone.utc)
        except ValueError:
            continue

        for window_label, delta in _WINDOWS:
            idem_key = f"nudge:{cid}:{window_label}"

            # Check if already sent
            existing = await sb.table("commitment_events").select("id").eq(
                "commitment_id", cid
            ).eq("event_type", "nudge_sent").eq(
                "data->>nudge_window", window_label
            ).execute()
            if existing.data:
                continue

            # Check if this window applies
            if delta is None:
                # overdue window
                if now <= deadline:
                    continue
            else:
                window_start = deadline - delta
                if not (window_start <= now <= deadline):
                    continue

            # Insert nudge event
            try:
                await sb.table("commitment_events").insert({
                    "id": str(uuid.uuid4()),
                    "commitment_id": cid,
                    "event_type": "nudge_sent",
                    "actor": "nudge_engine",
                    "data": {
                        "nudge_window": window_label,
                        "deadline": deadline_str,
                        "idempotency_key": idem_key,
                    },
                    "occurred_at": now.isoformat(),
                    "recorded_at": now.isoformat(),
                }).execute()
                nudge_count += 1
                logger.info("Nudge sent commitment=%s window=%s", cid, window_label)
            except Exception as e:
                logger.warning("Nudge insert failed cid=%s: %s", cid, e)

    return nudge_count
