"""V1.5/V1.6 – Lifecycle management: expiry, due-date transitions, nudges.

All status changes go through the atomic state machine — no direct UPDATEs.
"""
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException

from app.db.connection import get_supabase

logger = logging.getLogger(__name__)
router = APIRouter()


async def mark_expired_commitments() -> int:
    """Transition open/overdue time-based commitments past their deadline to 'expired'.

    Uses the state machine for each transition (idempotent).
    """
    from app.core.state_machine import transition_commitment, validate_transition

    sb = get_supabase()
    now = datetime.now(timezone.utc).isoformat()

    candidates = (
        await sb.table("commitments")
        .select("id, status")
        .in_("status", ["open", "due", "overdue"])
        .filter("due_condition->>type", "eq", "time")
        .lt("due_condition->>deadline", now)
        .execute()
    )

    count = 0
    for row in candidates.data or []:
        cid = row["id"]
        current = row["status"]
        # Only expire if the transition is valid
        if not validate_transition(current, "expired"):
            continue
        try:
            await transition_commitment(
                __import__("uuid").UUID(cid),
                "expired",
                actor="expiry_job",
            )
            count += 1
        except Exception as e:
            logger.warning("Failed to expire commitment %s: %s", cid, e)

    logger.info("mark_expired_commitments: %d row(s) expired", count)
    return count


async def advance_due_commitments() -> int:
    """Transition open commitments that are approaching their deadline to 'due'."""
    from datetime import timedelta

    sb = get_supabase()
    now = datetime.now(timezone.utc)
    lookahead = (now + timedelta(hours=24)).isoformat()
    now_iso = now.isoformat()

    candidates = await sb.table("commitments").select("id, status").eq(
        "status", "open"
    ).filter("due_condition->>type", "eq", "time").lt(
        "due_condition->>deadline", lookahead
    ).gte("due_condition->>deadline", now_iso).execute()

    from app.core.state_machine import transition_commitment
    count = 0
    for row in candidates.data or []:
        try:
            await transition_commitment(
                __import__("uuid").UUID(row["id"]),
                "due",
                actor="lifecycle_job",
            )
            count += 1
        except Exception as e:
            logger.warning("Failed to advance commitment %s to due: %s", row["id"], e)

    return count


@router.post("/admin/run-expiry")
async def run_expiry() -> dict:
    try:
        expired_count = await mark_expired_commitments()
    except Exception as e:
        logger.error("run-expiry failed: %s", e)
        raise HTTPException(status_code=500, detail="Expiry job failed")
    return {"expired_count": expired_count}


@router.post("/admin/run-nudges")
async def run_nudges_endpoint() -> dict:
    try:
        from app.core.nudges import run_nudges
        nudge_count = await run_nudges()
    except Exception as e:
        logger.error("run-nudges failed: %s", e)
        raise HTTPException(status_code=500, detail="Nudge job failed")
    return {"nudge_count": nudge_count}
