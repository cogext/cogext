"""V1.7 – Human review API.
All review state changes are auditable via commitment_events.
Accepting a review triggers a state machine transition.
"""
import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.auth import Account, get_current_account
from app.core.ownership import get_owned_commitment
from app.core.state_machine import transition_commitment
from app.db.connection import get_supabase
from app.core.notifications import send_kill_switch_alert, verify_action_token
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


async def _get_owned_review(sb, review_id: str, account: Account) -> dict:
    """Return the review if the caller owns its commitment, else 404.

    Access to a review follows ownership of its commitment. reviewer_id is a
    free-form label with no link to an account, so it grants nothing. Another
    account's review gets the same 404 as a missing one.
    """
    row = await _get_review(sb, review_id)
    try:
        await get_owned_commitment(sb, row["commitment_id"], account, "id")
    except HTTPException:
        raise HTTPException(status_code=404, detail="Review not found") from None
    return row


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
async def create_review(
    body: CreateReviewRequest,
    account: Account = Depends(get_current_account),
) -> HumanReview:
    sb = get_supabase()

    # The caller must own the commitment (404 otherwise)
    c_row = await get_owned_commitment(
        sb, body.commitment_id, account,
        "id, status, promise_text, risk_score, risk_reasons, source_agent_id",
    )

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
    current = c_row["status"]
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

    # ── Kill Switch: Slack alert ──────────────────────────────────────
    try:
        risk_score = c_row.get("risk_score")
        risk_reasons = c_row.get("risk_reasons") or []
        await send_kill_switch_alert(
            review_id=str(rev_id),
            commitment_id=str(body.commitment_id),
            promise_text=c_row.get("promise_text", ""),
            risk_score=risk_score,
            risk_reasons=risk_reasons,
            status=c_row.get("status", "open"),
            agent_id=c_row.get("source_agent_id", ""),
        )
    except Exception as ks_err:
        logger.warning("Kill switch alert failed: %s", ks_err)
    # ─────────────────────────────────────────────────────────────────

    resp = await sb.table("human_reviews").select("*").eq("id", str(rev_id)).execute()
    return HumanReview.model_validate(resp.data[0])


@router.get("/reviews/{review_id}", response_model=HumanReview)
async def get_review(
    review_id: uuid.UUID,
    account: Account = Depends(get_current_account),
) -> HumanReview:
    sb = get_supabase()
    row = await _get_owned_review(sb, str(review_id), account)
    return HumanReview.model_validate(row)


@router.get("/reviews", response_model=list[HumanReview])
async def list_reviews(
    commitment_id: uuid.UUID | None = None,
    status: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    account: Account = Depends(get_current_account),
) -> list[HumanReview]:
    sb = get_supabase()
    # Only reviews of the caller's own commitments. A commitment_id filter for
    # another account's commitment returns [], the same as a missing one.
    owned = await sb.table("commitments").select("id").eq("user_id", account.account_id).execute()
    owned_ids = [r["id"] for r in (owned.data or [])]
    if commitment_id:
        owned_ids = [i for i in owned_ids if str(i) == str(commitment_id)]
    if not owned_ids:
        return []
    q = sb.table("human_reviews").select("*").in_("commitment_id", owned_ids).limit(limit)
    if status:
        q = q.eq("status", status)
    resp = await q.order("created_at", desc=True).execute()
    return [HumanReview.model_validate(r) for r in (resp.data or [])]


@router.post("/reviews/{review_id}/assign", response_model=HumanReview)
async def assign_review(
    review_id: uuid.UUID,
    body: AssignReviewRequest,
    account: Account = Depends(get_current_account),
) -> HumanReview:
    sb = get_supabase()
    row = await _get_owned_review(sb, str(review_id), account)
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
async def accept_review(
    review_id: uuid.UUID,
    body: AcceptReviewRequest,
    account: Account = Depends(get_current_account),
) -> HumanReview:
    sb = get_supabase()
    row = await _get_owned_review(sb, str(review_id), account)
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
async def reject_review(
    review_id: uuid.UUID,
    body: RejectReviewRequest,
    account: Account = Depends(get_current_account),
) -> HumanReview:
    sb = get_supabase()
    row = await _get_owned_review(sb, str(review_id), account)
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
async def edit_review(
    review_id: uuid.UUID,
    body: EditReviewRequest,
    account: Account = Depends(get_current_account),
) -> HumanReview:
    sb = get_supabase()
    await _get_owned_review(sb, str(review_id), account)
    # Existence guard: _get_review raises 404 when the review is missing.
    await _get_review(sb, str(review_id))
    now = datetime.now(timezone.utc).isoformat()
    await sb.table("human_reviews").update({
        "status": "edited",
        "proposed_changes": body.proposed_changes,
        "resolution": body.resolution,
        "resolved_at": now,
    }).eq("id", str(review_id)).execute()
    updated = await _get_review(sb, str(review_id))
    return HumanReview.model_validate(updated)


# ── Public: one-click action from Slack button ───────────────────────────────

from fastapi.responses import HTMLResponse

@router.get("/reviews/{review_id}/action", include_in_schema=False)
async def review_action(
    review_id: uuid.UUID,
    action: str,
    token: str,
) -> HTMLResponse:
    """Public endpoint called by Slack action buttons — no API key required.
    Verifies HMAC token, executes approve or cancel, returns simple HTML.
    """
    if action not in ("approve", "cancel"):
        return HTMLResponse("<h2>Invalid action.</h2>", status_code=400)

    if not verify_action_token(token, str(review_id), action):
        return HTMLResponse("<h2>Token invalid or expired.</h2>", status_code=403)

    sb = get_supabase()
    try:
        row = await _get_review(sb, str(review_id))
    except HTTPException:
        return HTMLResponse("<h2>Review not found.</h2>", status_code=404)

    now = datetime.now(timezone.utc).isoformat()

    if action == "approve":
        if row["status"] not in ("pending", "assigned"):
            return HTMLResponse(f"<h2>Review already {row['status']}.</h2>")
        await sb.table("human_reviews").update({
            "status": "accepted",
            "resolved_at": now,
            "resolution": "Approved via Slack kill-switch",
        }).eq("id", str(review_id)).execute()
        try:
            from app.core.state_machine import transition_commitment
            await transition_commitment(
                uuid.UUID(row["commitment_id"]), "open",
                actor="slack_kill_switch",
                data={"review_id": str(review_id), "action": "approved"},
            )
        except Exception as e:
            logger.warning("Kill switch approve transition failed: %s", e)
        await _insert_event(sb, row["commitment_id"], "review_accepted", "slack_kill_switch",
                            {"review_id": str(review_id)})
        return HTMLResponse("<h2>✅ Commitment approved.</h2><p>You can close this tab.</p>")

    else:  # cancel
        if row["status"] not in ("pending", "assigned"):
            return HTMLResponse(f"<h2>Review already {row['status']}.</h2>")
        await sb.table("human_reviews").update({
            "status": "rejected",
            "resolved_at": now,
            "resolution": "Cancelled via Slack kill-switch",
        }).eq("id", str(review_id)).execute()
        try:
            from app.core.state_machine import transition_commitment
            await transition_commitment(
                uuid.UUID(row["commitment_id"]), "cancelled",
                actor="slack_kill_switch",
                data={"review_id": str(review_id), "action": "cancelled"},
            )
        except Exception as e:
            logger.warning("Kill switch cancel transition failed: %s", e)
        await _insert_event(sb, row["commitment_id"], "review_rejected", "slack_kill_switch",
                            {"review_id": str(review_id)})
        return HTMLResponse("<h2>🛑 Commitment cancelled.</h2><p>You can close this tab.</p>")
