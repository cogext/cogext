"""V1.6/V1.7 – Evidence submission and retrieval API."""
import hashlib
import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query

from app.core.evidence_aggregator import aggregate_evidence, score_evidence_against_commitment
from app.db.connection import get_supabase
from app.models.evidence import Evidence, EvidenceAggregation, SubmitEvidenceRequest

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/commitments/{commitment_id}/evidence", response_model=Evidence)
async def submit_evidence(
    commitment_id: uuid.UUID,
    body: SubmitEvidenceRequest,
) -> Evidence:
    sb = get_supabase()

    # Verify commitment exists and get fields for scoring
    c_resp = await sb.table("commitments").select(
        "id, action, object, recipient, due_condition, status"
    ).eq("id", str(commitment_id)).execute()
    if not c_resp.data:
        raise HTTPException(status_code=404, detail="Commitment not found")

    c_row = c_resp.data[0]
    due_cond = c_row.get("due_condition") or {}
    commitment_deadline = due_cond.get("deadline")

    # Score evidence against commitment fields
    score, match_details = score_evidence_against_commitment(
        evidence_data=body.data,
        commitment_action=c_row.get("action"),
        commitment_recipient=c_row.get("recipient"),
        commitment_object=c_row.get("object"),
        commitment_deadline=commitment_deadline,
    )

    # Determine strength from score
    if score >= 0.8:
        strength = "strong"
    elif score >= 0.5:
        strength = "supporting"
    elif score > 0.0:
        strength = "weak"
    else:
        strength = "contradictory"

    # Idempotency key for external evidence
    idem_key = None
    if body.external_system and body.external_event_id:
        idem_key = hashlib.sha256(
            f"{body.external_system}:{body.external_event_id}".encode()
        ).hexdigest()

    now = datetime.now(timezone.utc).isoformat()
    ev_id = uuid.uuid4()

    row = {
        "id": str(ev_id),
        "commitment_id": str(commitment_id),
        "source": body.source,
        "external_system": body.external_system,
        "external_event_id": body.external_event_id,
        "actor": body.actor,
        "provenance": body.provenance,
        "raw_reference": body.raw_reference,
        "data": body.data,
        "occurred_at": body.occurred_at.isoformat() if body.occurred_at else now,
        "recorded_at": now,
        "strength": strength,
        "score": score,
        "match_details": [d.model_dump() for d in match_details],
        "verified": False,
        "idempotency_key": idem_key,
    }

    try:
        if idem_key:
            await sb.table("evidence").upsert(
                row, on_conflict="idempotency_key", ignore_duplicates=True
            ).execute()
        else:
            await sb.table("evidence").insert(row).execute()
    except Exception as e:
        logger.error("Evidence insert failed: %s", e)
        raise HTTPException(status_code=500, detail="Failed to save evidence")

    # Insert event
    try:
        await sb.table("commitment_events").insert({
            "id": str(uuid.uuid4()),
            "commitment_id": str(commitment_id),
            "event_type": "evidence_submitted",
            "actor": body.actor or "api",
            "data": {"evidence_id": str(ev_id), "source": body.source, "score": score},
            "occurred_at": now,
            "recorded_at": now,
        }).execute()
    except Exception as e:
        logger.warning("Evidence event insert failed: %s", e)

    return Evidence(
        id=ev_id,
        commitment_id=commitment_id,
        source=body.source,
        external_system=body.external_system,
        external_event_id=body.external_event_id,
        actor=body.actor,
        provenance=body.provenance,
        raw_reference=body.raw_reference,
        data=body.data,
        occurred_at=body.occurred_at,
        recorded_at=datetime.now(timezone.utc),
        strength=strength,
        score=score,
        match_details=match_details,
        idempotency_key=idem_key,
    )


@router.get("/commitments/{commitment_id}/evidence", response_model=list[Evidence])
async def get_evidence(
    commitment_id: uuid.UUID,
    limit: int = Query(default=50, ge=1, le=200),
) -> list[Evidence]:
    sb = get_supabase()
    resp = await sb.table("evidence").select("*").eq(
        "commitment_id", str(commitment_id)
    ).order("occurred_at", desc=False).limit(limit).execute()
    return [Evidence.model_validate(r) for r in (resp.data or [])]


@router.get("/commitments/{commitment_id}/evidence/aggregate", response_model=EvidenceAggregation)
async def get_evidence_aggregate(commitment_id: uuid.UUID) -> EvidenceAggregation:
    sb = get_supabase()
    resp = await sb.table("evidence").select("*").eq(
        "commitment_id", str(commitment_id)
    ).execute()
    records = [Evidence.model_validate(r) for r in (resp.data or [])]
    return aggregate_evidence(commitment_id, records)


@router.post("/evidence/external", response_model=Evidence)
async def submit_external_evidence(body: SubmitEvidenceRequest) -> Evidence:
    """Submit evidence from an external system adapter."""
    return await submit_evidence(body.commitment_id, body)
