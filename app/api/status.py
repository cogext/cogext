import logging
import uuid
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.db.connection import get_supabase
from app.db.row_helpers import row_to_dict
from app.models.commitment import Commitment

logger = logging.getLogger(__name__)
router = APIRouter()

_TERMINAL = {"fulfilled", "expired", "contradicted"}
_ALLOWED_TARGETS = Literal["open", "fulfilled", "expired", "contradicted"]

_COLS = (
    "id, user_id, source_agent_id, target_agent_id, record_key, "
    "promise_text, due_condition, status, confidence, idempotency_key, created_at"
)


class StatusUpdate(BaseModel):
    status: _ALLOWED_TARGETS


@router.patch("/commitments/{commitment_id}")
async def update_status(commitment_id: uuid.UUID, body: StatusUpdate) -> Commitment:
    sb = get_supabase()

    # Check current status
    current_resp = await sb.table("commitments").select("status").eq("id", str(commitment_id)).execute()
    if not current_resp.data:
        raise HTTPException(status_code=404, detail="Commitment not found")

    current_status = current_resp.data[0]["status"]
    if current_status in _TERMINAL:
        raise HTTPException(
            status_code=409,
            detail=f"Cannot transition from terminal status '{current_status}'",
        )

    now = datetime.now(timezone.utc).isoformat()
    await sb.table("commitments").update({
        "status": body.status,
        "updated_at": now,
        "resolved_at": now if body.status != "open" else None,
    }).eq("id", str(commitment_id)).execute()

    row_resp = await sb.table("commitments").select(_COLS).eq("id", str(commitment_id)).execute()
    if not row_resp.data:
        raise HTTPException(status_code=500, detail="Commitment updated but not found")

    try:
        return Commitment.model_validate(row_to_dict(row_resp.data[0]))
    except Exception as e:
        logger.error("Failed to parse updated commitment: %s", e)
        raise HTTPException(status_code=500, detail="Commitment updated but response parse failed")
