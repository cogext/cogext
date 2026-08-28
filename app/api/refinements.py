"""V1.7 – Commitment refinement API (append-only change records)."""
import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException

from app.db.connection import get_supabase
from app.models.refinement import (
    ApplyRefinementRequest,
    CommitmentChange,
    RefinementHistory,
)

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/commitments/{commitment_id}/refinements", response_model=RefinementHistory)
async def apply_refinement(
    commitment_id: uuid.UUID,
    body: ApplyRefinementRequest,
) -> RefinementHistory:
    """Apply a refinement to a commitment.

    Each field change is appended as an immutable CommitmentChange record.
    Historical values never disappear.
    """
    sb = get_supabase()

    # Verify commitment exists and fetch current values
    c_resp = await sb.table("commitments").select("*").eq(
        "id", str(commitment_id)
    ).execute()
    if not c_resp.data:
        raise HTTPException(status_code=404, detail="Commitment not found")

    c_row = c_resp.data[0]
    now = datetime.now(timezone.utc).isoformat()
    changes: list[CommitmentChange] = []
    db_updates: dict = {}

    for ch in body.changes:
        field = ch.get("field")
        new_value = ch.get("new_value")
        change_type = ch.get("change_type", "refines")
        reason = ch.get("reason")

        if not field:
            continue

        previous_value = c_row.get(field)
        ch_id = uuid.uuid4()

        await sb.table("commitment_changes").insert({
            "id": str(ch_id),
            "commitment_id": str(commitment_id),
            "field": field,
            "previous_value": str(previous_value) if previous_value is not None else None,
            "new_value": str(new_value) if new_value is not None else None,
            "change_type": change_type,
            "actor": body.actor,
            "timestamp": now,
            "reason": reason,
        }).execute()

        db_updates[field] = new_value
        changes.append(CommitmentChange(
            id=ch_id,
            commitment_id=commitment_id,
            field=field,
            previous_value=previous_value,
            new_value=new_value,
            change_type=change_type,
            actor=body.actor,
            timestamp=datetime.fromisoformat(now),
            reason=reason,
        ))

    # Apply field updates to commitment
    if db_updates:
        db_updates["updated_at"] = now
        await sb.table("commitments").update(db_updates).eq(
            "id", str(commitment_id)
        ).execute()

    # Handle supersession link
    if body.supersedes:
        from app.core.state_machine import transition_commitment
        try:
            await sb.table("commitments").update({
                "superseded_by": str(commitment_id),
                "updated_at": now,
            }).eq("id", str(commitment_id)).execute()
            # Mark original as superseded
            await transition_commitment(
                commitment_id,
                "superseded",
                actor=body.actor,
                data={"superseded_by": str(commitment_id)},
            )
        except Exception as e:
            logger.warning("Supersession transition failed: %s", e)

    # Insert refinement_applied event
    try:
        await sb.table("commitment_events").insert({
            "id": str(uuid.uuid4()),
            "commitment_id": str(commitment_id),
            "event_type": "refinement_applied",
            "actor": body.actor,
            "data": {"fields": [c.field for c in changes]},
            "occurred_at": now,
            "recorded_at": now,
        }).execute()
    except Exception as e:
        logger.warning("Refinement event insert failed: %s", e)

    return RefinementHistory(commitment_id=commitment_id, changes=changes)


@router.get("/commitments/{commitment_id}/refinements", response_model=RefinementHistory)
async def get_refinements(commitment_id: uuid.UUID) -> RefinementHistory:
    sb = get_supabase()
    resp = await sb.table("commitment_changes").select("*").eq(
        "commitment_id", str(commitment_id)
    ).order("timestamp", desc=False).execute()
    changes = [CommitmentChange.model_validate(r) for r in (resp.data or [])]
    return RefinementHistory(commitment_id=commitment_id, changes=changes)
