"""V1.5 – Status update API: all transitions through the atomic state machine."""
import logging
import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.core.state_machine import transition_commitment, validate_transition
from app.db.connection import get_supabase
from app.db.row_helpers import row_to_dict
from app.models.commitment import Commitment, CommitmentStatus

logger = logging.getLogger(__name__)
router = APIRouter()

_COLS = (
    "id, user_id, source_agent_id, target_agent_id, record_key, "
    "promise_text, due_condition, status, confidence, idempotency_key, created_at, "
    "action, object, recipient, source_type, classification"
)


class StatusUpdate(BaseModel):
    status: CommitmentStatus
    actor: str = "api"
    reason: str | None = None


@router.patch("/commitments/{commitment_id}")
async def update_status(commitment_id: uuid.UUID, body: StatusUpdate) -> Commitment:
    sb = get_supabase()

    # Verify commitment exists and get current status
    current_resp = await sb.table("commitments").select("status").eq(
        "id", str(commitment_id)
    ).execute()
    if not current_resp.data:
        raise HTTPException(status_code=404, detail="Commitment not found")

    current_status = current_resp.data[0]["status"]

    # Validate transition before hitting DB
    if not validate_transition(current_status, body.status):
        raise HTTPException(
            status_code=409,
            detail=f"Invalid transition from '{current_status}' to '{body.status}'",
        )

    try:
        commitment = await transition_commitment(
            commitment_id,
            body.status,
            actor=body.actor,
            data={"reason": body.reason} if body.reason else {},
        )
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except Exception as e:
        logger.error("Transition failed cid=%s: %s", commitment_id, e)
        raise HTTPException(status_code=500, detail="State transition failed")

    return commitment


@router.get("/commitments/{commitment_id}")
async def get_commitment(commitment_id: uuid.UUID) -> Commitment:
    sb = get_supabase()
    resp = await sb.table("commitments").select(_COLS).eq(
        "id", str(commitment_id)
    ).execute()
    if not resp.data:
        raise HTTPException(status_code=404, detail="Commitment not found")
    try:
        return Commitment.model_validate(row_to_dict(resp.data[0]))
    except Exception as e:
        logger.error("Failed to parse commitment: %s", e)
        raise HTTPException(status_code=500, detail="Failed to parse commitment")


class ApproveRequest(BaseModel):
    actor: str = "api"
    decision: str = "approved"
    resolution: str | None = None


@router.post("/commitments/{commitment_id}/approve")
async def approve_commitment(commitment_id: uuid.UUID, body: ApproveRequest) -> dict:
    """Approve a commitment that is pending_review — transitions it to open.
    Finds the most recent pending/assigned review for the commitment and accepts it.
    Returns JSON with the commitment id and new state.
    """
    sb = get_supabase()

    # Verify commitment exists and is in pending_review
    c_resp = await sb.table("commitments").select("id, status").eq(
        "id", str(commitment_id)
    ).execute()
    if not c_resp.data:
        raise HTTPException(status_code=404, detail="Commitment not found")

    current_status = c_resp.data[0]["status"]
    if current_status not in ("pending_review", "PENDING_REVIEW"):
        raise HTTPException(
            status_code=409,
            detail=f"Commitment is not pending_review (current: {current_status})",
        )

    # Find the open review
    rev_resp = await sb.table("human_reviews") \
        .select("id, status") \
        .eq("commitment_id", str(commitment_id)) \
        .in_("status", ["pending", "assigned"]) \
        .order("created_at", desc=True) \
        .limit(1) \
        .execute()

    import datetime as _dt
    now = _dt.datetime.now(_dt.timezone.utc).isoformat()

    if rev_resp.data:
        review_id = rev_resp.data[0]["id"]
        await sb.table("human_reviews").update({
            "status": "accepted",
            "resolved_at": now,
            "resolution": body.resolution or f"Approved by {body.actor}",
        }).eq("id", review_id).execute()

    # Transition commitment from pending_review → open
    try:
        await transition_commitment(
            commitment_id, "open",
            actor=body.actor,
            data={"decision": body.decision},
        )
    except Exception as e:
        logger.error("approve_commitment: state transition failed: %s", e)
        raise HTTPException(status_code=409, detail=f"State transition failed: {e}")

    return {"id": str(commitment_id), "state": "open", "status": "open"}
