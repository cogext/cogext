"""V1.7 – Human review API.
All review state changes are auditable via commitment_events.
Accepting a review triggers a state machine transition.
"""
import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query

from app.core.state_machine import transition_commitment
from app.db.connection import get_supabase
from app.models.review import (
    AcceptReviewRequest,
    AssignReviewRequest,
    CreateReviewRequest,
    EditReviewRequest,
    HumanReview,
    RejectReviewRequest,
)

logger = logging.getLogger(__name__)
router = APIRouter()


async def _get_review(sb, review_id: str) -> dict:
    resp = await sb.table("human_reviews").select("*").eq("id", review_id).execute()
    if not resp.data:
        raise HTTPException(status_code=404, detail="Review not found")
    return resp.data[0]


async def _insert_event(sb, commitment_id: str, event_type: str, actor: str, data: dict):
    now = datetime.now(timezone.utc).isoformat()
    try:
        await sb.table("commitment_events").insert({
            "id": str(uuid.uuid4()),
            "commitment_id": commitment_id,
            "event_type": event_type,
            "actor": actor,
            "data": data,
            "occurred_at": now,
            "recorded_at": now,
        }).execute()
    except Exception as e:
        logger.warning("Review event insert failed: %s", e)


@router.post("/reviews", response_model=HumanReview)
async def create_review(body: CreateReviewRequest) -> HumanReview:
    sb = get_supabase()

    # Verify commitment exists
    c_resp = await sb.table("commitments").select("id, status").eq(
        "id", str(body.commitment_id)
    ).execute()
    if not c_resp.data:
        raise HTTPException(status_code=404, detail="Commitment not found")

    now = datetime.now(timezone.utc).isoformat()
    rev_id = uuid.uuid4()

    await sb.table("human_reviews").insert({
        "id": str(rev_id),
        "commitment_id": str(body.commitment_id),
        "status": "pending",
        "reason_code": body.reason_code,
        "reason": body.reason,
        "proposed_changes": body.metadata,
        "metadata": body.metadata,
        "created_at": now,
    }).execute()

    # Transition commitment to pending_review if currently open/detected
    current = c_resp.data[0]["status"]
    if current == "detected":  # only detected→pending_review is a valid transition
        try:
            await transition_commitment(
                body.commitment_id, "pending_review",
                actor="review_api",
                data={"review_id": str(rev_id), "reason_code": body.reason_code},
            )
        except Exception as e:
            logger.warning("Could not transition to pending_review: %s", e)

    await _insert_event(sb, str(body.commitment_id), "review_created", "review_api",
                        {"review_id": str(rev_id), "reason_code": body.reason_code})

    resp = await sb.table("human_reviews").select("*").eq("id", str(rev_id)).execute()
    return HumanReview.model_validate(resp.data[0])


@router.get("/reviews/{review_id}", response_model=HumanReview)
async def get_review(review_id: uuid.UUID) -> HumanReview:
    sb = get_supabase()
    row = await _get_review(sb, str(review_id))
    return HumanReview.model_validate(row)


@router.get("/reviews", response_model=list[HumanReview])
async def list_reviews(
    commitment_id: uuid.UUID | None = None,
    status: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
) -> list[HumanReview]:
    sb = get_supabase()
    q = sb.table("human_reviews").select("*").limit(limit)
    if commitment_id:
        q = q.eq("commitment_id", str(commitment_id))
    if status:
        q = q.eq("status", status)
    resp = await q.order("created_at", desc=True).execute()
    return [HumanReview.model_validate(r) for r in (resp.data or [])]


@router.post("/reviews/{review_id}/assign", response_model=HumanReview)
async def assign_review(review_id: uuid.UUID, body: AssignReviewRequest) -> HumanReview:
    sb = get_supabase()
    row = await _get_review(sb, str(review_id))
    if row["status"] != "pending":
        raise HTTPException(status_code=409, detail="Review is not in pending state")
    now = datetime.now(timezone.utc).isoformat()
    await sb.table("human_reviews").update({
        "reviewer_id": str(body.reviewer_id),
        "status": "assigned",
        "assigned_at": now,
    }).eq("id", str(review_id)).execute()
    updated = await _get_review(sb, str(review_id))
    await _insert_event(sb, row["commitment_id"], "status_changed",
                        str(body.reviewer_id), {"review_id": str(review_id), "action": "assigned"})
    return HumanReview.model_validate(updated)


@router.post("/reviews/{review_id}/accept", response_model=HumanReview)
async def accept_review(review_id: uuid.UUID, body: AcceptReviewRequest) -> HumanReview:
    sb = get_supabase()
    row = await _get_review(sb, str(review_id))
    if row["status"] not in {"pending", "assigned"}:
        raise HTTPException(status_code=409, detail="Review cannot be accepted in its current state")
    now = datetime.now(timezone.utc).isoformat()
    await sb.table("human_reviews").update({
        "status": "accepted",
        "resolved_at": now,
        "resolution": body.resolution,
        "proposed_changes": body.proposed_changes,
    }).eq("id", str(review_id)).execute()

    # Transition commitment from pending_review → open (via state machine)
    try:
        await transition_commitment(
            uuid.UUID(row["commitment_id"]), "open",
            actor="review_accepted",
            data={"review_id": str(review_id), "resolution": body.resolution},
        )
    except Exception as e:
        logger.error("accept_review: commitment state transition failed: %s", e)
        raise HTTPException(status_code=409, detail=f"Cannot accept: commitment state transition failed: {e}")

    await _insert_event(sb, row["commitment_id"], "review_accepted", "reviewer",
                        {"review_id": str(review_id)})
    updated = await _get_review(sb, str(review_id))
    return HumanReview.model_validate(updated)


@router.post("/reviews/{review_id}/reject", response_model=HumanReview)
async def reject_review(review_id: uuid.UUID, body: RejectReviewRequest) -> HumanReview:
    sb = get_supabase()
    row = await _get_review(sb, str(review_id))
    if row["status"] not in {"pending", "assigned"}:
        raise HTTPException(status_code=409, detail="Review cannot be rejected in its current state")
    now = datetime.now(timezone.utc).isoformat()
    await sb.table("human_reviews").update({
        "status": "rejected",
        "resolved_at": now,
        "resolution": body.resolution,
    }).eq("id", str(review_id)).execute()
    await _insert_event(sb, row["commitment_id"], "review_rejected", "reviewer",
                        {"review_id": str(review_id), "resolution": body.resolution})
    updated = await _get_review(sb, str(review_id))
    return HumanReview.model_validate(updated)


@router.post("/reviews/{review_id}/edit", response_model=HumanReview)
async def edit_review(review_id: uuid.UUID, body: EditReviewRequest) -> HumanReview:
    sb = get_supabase()
    row = await _get_review(sb, str(review_id))
    now = datetime.now(timezone.utc).isoformat()
    await sb.table("human_reviews").update({
        "status": "edited",
        "proposed_changes": body.proposed_changes,
        "resolution": body.resolution,
        "resolved_at": now,
    }).eq("id", str(review_id)).execute()
    updated = await _get_review(sb, str(review_id))
    return HumanReview.model_validate(updated)
