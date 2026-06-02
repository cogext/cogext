import logging
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException

from app.db.connection import get_supabase

logger = logging.getLogger(__name__)
router = APIRouter()


async def mark_expired_commitments() -> int:
    sb = get_supabase()
    now = datetime.now(timezone.utc).isoformat()

    # Find open, time-based commitments whose deadline has passed.
    # PostgREST JSONB text-extraction syntax: due_condition->>key
    candidates = (
        await sb.table("commitments")
        .select("id")
        .eq("status", "open")
        .filter("due_condition->>type", "eq", "time")
        .lt("due_condition->>deadline", now)
        .execute()
    )

    ids = [row["id"] for row in candidates.data]
    if not ids:
        return 0

    await sb.table("commitments").update({
        "status": "expired",
        "resolved_at": now,
        "updated_at": now,
    }).in_("id", ids).execute()

    logger.info("mark_expired_commitments: %d row(s) expired", len(ids))
    return len(ids)


@router.post("/admin/run-expiry")
async def run_expiry() -> dict:
    try:
        expired_count = await mark_expired_commitments()
    except Exception as e:
        logger.error("run-expiry failed: %s", e)
        raise HTTPException(status_code=500, detail="Expiry job failed")
    return {"expired_count": expired_count}
