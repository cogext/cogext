import logging
import uuid
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.db.connection import get_pool
from app.db.row_helpers import row_to_dict
from app.models.commitment import Commitment

logger = logging.getLogger(__name__)
router = APIRouter()

# Terminal states cannot be transitioned out of
_TERMINAL = {"fulfilled", "expired", "contradicted"}

# Allowed targets from PATCH (excludes pending_review — that's set by the system only)
_ALLOWED_TARGETS = Literal["open", "fulfilled", "expired", "contradicted"]


class StatusUpdate(BaseModel):
    status: _ALLOWED_TARGETS


@router.patch("/commitments/{commitment_id}")
async def update_status(commitment_id: uuid.UUID, body: StatusUpdate) -> Commitment:
    pool = get_pool()

    async with pool.acquire() as conn:
        current = await conn.fetchrow(
            "SELECT status FROM commitments WHERE id = $1",
            commitment_id,
        )
        if current is None:
            raise HTTPException(status_code=404, detail="Commitment not found")

        if current["status"] in _TERMINAL:
            raise HTTPException(
                status_code=409,
                detail=f"Cannot transition from terminal status '{current['status']}'",
            )

        row = await conn.fetchrow(
            """
            UPDATE commitments
               SET status = $1,
                   resolved_at = CASE WHEN $1 != 'open' THEN NOW() ELSE NULL END,
                   updated_at = NOW()
             WHERE id = $2
             RETURNING id, user_id, source_agent_id, target_agent_id, record_key,
                       promise_text, due_condition, status, confidence,
                       idempotency_key, created_at
            """,
            body.status,
            commitment_id,
        )

    try:
        return Commitment.model_validate(row_to_dict(row))
    except Exception as e:
        logger.error("Failed to parse updated commitment: %s", e)
        raise HTTPException(status_code=500, detail="Commitment updated but response parse failed")
