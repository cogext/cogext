import logging
import uuid
from typing import Literal

from fastapi import APIRouter, HTTPException, Query

from app.db.connection import get_pool
from app.db.row_helpers import row_to_dict
from app.models.commitment import Commitment

logger = logging.getLogger(__name__)
router = APIRouter()

_STATUS_VALUES = Literal["open", "fulfilled", "expired", "contradicted", "pending_review"]

_SELECT = """
    SELECT id, user_id, source_agent_id, target_agent_id, record_key,
           promise_text, due_condition, status, confidence,
           idempotency_key, created_at
    FROM commitments
"""


@router.get("/commitments")
async def get_commitments(
    user_id: uuid.UUID,
    source_agent_id: uuid.UUID | None = None,
    target_agent_id: uuid.UUID | None = None,
    record_key: str | None = None,
    status: _STATUS_VALUES = Query(default="open"),
    limit: int = Query(default=50, ge=1, le=200),
) -> dict:
    pool = get_pool()

    conditions = ["user_id = $1", "status = $2"]
    params: list = [user_id, status]
    idx = 3

    if source_agent_id is not None:
        conditions.append(f"source_agent_id = ${idx}")
        params.append(source_agent_id)
        idx += 1

    if target_agent_id is not None:
        conditions.append(f"target_agent_id = ${idx}")
        params.append(target_agent_id)
        idx += 1

    if record_key is not None:
        conditions.append(f"record_key = ${idx}")
        params.append(record_key)
        idx += 1

    where = " AND ".join(conditions)
    query = f"{_SELECT} WHERE {where} ORDER BY created_at DESC LIMIT ${idx}"
    params.append(limit)

    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(query, *params)
    except Exception as e:
        logger.error("commitments fetch failed: %s", e)
        raise HTTPException(status_code=500, detail="Failed to fetch commitments")

    commitments = []
    for row in rows:
        try:
            commitments.append(Commitment.model_validate(row_to_dict(row)))
        except Exception as e:
            logger.warning("Skipping unparseable row: %s", e)

    return {"commitments": [c.model_dump() for c in commitments]}
