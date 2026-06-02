import logging
import uuid
from typing import Literal

from fastapi import APIRouter, HTTPException, Query

from app.db.connection import get_supabase
from app.db.row_helpers import row_to_dict
from app.models.commitment import Commitment

logger = logging.getLogger(__name__)
router = APIRouter()

_STATUS_VALUES = Literal["open", "fulfilled", "expired", "contradicted", "pending_review"]

_COLS = (
    "id, user_id, source_agent_id, target_agent_id, record_key, "
    "promise_text, due_condition, status, confidence, idempotency_key, created_at"
)


@router.get("/commitments")
async def get_commitments(
    user_id: uuid.UUID,
    source_agent_id: uuid.UUID | None = None,
    target_agent_id: uuid.UUID | None = None,
    record_key: str | None = None,
    status: _STATUS_VALUES = Query(default="open"),
    limit: int = Query(default=50, ge=1, le=200),
) -> dict:
    sb = get_supabase()

    query = (
        sb.table("commitments")
        .select(_COLS)
        .eq("user_id", str(user_id))
        .eq("status", status)
    )

    if source_agent_id is not None:
        query = query.eq("source_agent_id", str(source_agent_id))
    if target_agent_id is not None:
        query = query.eq("target_agent_id", str(target_agent_id))
    if record_key is not None:
        query = query.eq("record_key", record_key)

    query = query.order("created_at", desc=True).limit(limit)

    try:
        resp = await query.execute()
    except Exception as e:
        logger.error("commitments fetch failed: %s", e)
        raise HTTPException(status_code=500, detail="Failed to fetch commitments")

    commitments = []
    for row in resp.data:
        try:
            commitments.append(Commitment.model_validate(row_to_dict(row)))
        except Exception as e:
            logger.warning("Skipping unparseable row: %s", e)

    return {"commitments": [c.model_dump() for c in commitments]}
