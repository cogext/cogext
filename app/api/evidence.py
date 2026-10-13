"""V1.6/V1.7 – Evidence submission and retrieval API."""
import hashlib
import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.auth import Account, get_current_account
from app.core.evidence_aggregator import aggregate_evidence, score_evidence_against_commitment
from app.core.ownership import get_owned_commitment
from app.db.connection import get_supabase
from app.models.evidence import Evidence, EvidenceAggregation, SubmitEvidenceRequest

logger = logging.getLogger(__name__)
router = APIRouter()


def _map_strength(score: float) -> str:
    """Map score to a DB-valid strength value.

    DB CHECK: ('strong','supporting','weak','contradictory')
    Never return 'none' — that violates the constraint and silently fails the INSERT.
    A zero-score piece of evidence is still 'weak' (submitted but unmatched).
    """
    if score >= 0.8:
        return "strong"
    if score >= 0.5:
        return "supporting"
    return "weak"  # covers 0.0 — still stored, just unmatched


@router.post("/commitments/{commitment_id}/evidence", response_model=Evidence)
async def submit_evidence(
    commitment_id: uuid.UUID,
    body: SubmitEvidenceRequest,
    account: Account = Depends(get_current_account),
) -> Evidence:
    sb = get_supabase()

    # The caller must own the commitment (404 otherwise); get fields for scoring
    c_row = await get_owned_commitment(
        sb, commitment_id, account, "id, action, object, recipient, due_condition, status"
    )
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

    strength = _map_strength(score)

    # Idempotency key for external evidence, namespaced by account:
    # evidence.idempotency_key is UNIQUE across the whole table. Migration 012
    # drops the global (external_system, external_event_id) index and re-keys
    # existing rows with this same format; keep the two in step.
    idem_key = None
    if body.external_system and body.external_event_id:
        idem_key = hashlib.sha256(
            f"{account.account_id}:{body.external_system}:{body.external_event_id}".encode()
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
            result = await sb.table("evidence").upsert(
                row, on_conflict="idempotency_key", ignore_duplicates=True
            ).execute()
        else:
            result = await sb.table("evidence").insert(row).execute()

        # Supabase client may not raise on PostgREST errors — check explicitly
        if hasattr(result, "data") and result.data is None:
            logger.error("Evidence insert returned no data — possible constraint violation. row=%s", row)
            raise HTTPException(status_code=500, detail="Failed to save evidence — DB constraint")
    except HTTPException:
        raise
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
    account: Account = Depends(get_current_account),
) -> list[Evidence]:
    sb = get_supabase()
    # Evidence belongs to its commitment; the caller must own it (404 otherwise)
    await get_owned_commitment(sb, commitment_id, account, "id")
    resp = await sb.table("evidence").select("*").eq(
        "commitment_id", str(commitment_id)
    ).order("occurred_at", desc=False).limit(limit).execute()
    return [Evidence.model_validate(r) for r in (resp.data or [])]


@router.get("/commitments/{commitment_id}/evidence/aggregate", response_model=EvidenceAggregation)
async def get_evidence_aggregate(
    commitment_id: uuid.UUID,
    account: Account = Depends(get_current_account),
) -> EvidenceAggregation:
    sb = get_supabase()
    # Evidence belongs to its commitment; the caller must own it (404 otherwise)
    await get_owned_commitment(sb, commitment_id, account, "id")
    resp = await sb.table("evidence").select("*").eq(
        "commitment_id", str(commitment_id)
    ).execute()
    records = [Evidence.model_validate(r) for r in (resp.data or [])]
    return aggregate_evidence(commitment_id, records)


@router.post("/evidence/external", response_model=Evidence)
async def submit_external_evidence(
    body: SubmitEvidenceRequest,
    account: Account = Depends(get_current_account),
) -> Evidence:
    """Submit evidence from an external system adapter."""
    return await submit_evidence(body.commitment_id, body, account)
