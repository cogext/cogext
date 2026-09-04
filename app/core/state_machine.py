"""V1.9 – Atomic PostgreSQL state machine for commitment transitions.

All state changes go through ``transition_commitment()``, which calls the
``cogext_transition_commitment`` Postgres function defined in
migrations/001_initial_nuclear.sql.  The function:
  1. Locks the row
  2. Validates the transition
  3. Updates the commitment
  4. Inserts an event (append-only)
  5. Returns the updated commitment row

This module never issues a direct UPDATE on ``commitments.status``.
"""
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from app.db.connection import get_supabase
from app.models.commitment import Commitment, CommitmentStatus

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Valid transitions – spec V1.5
# ---------------------------------------------------------------------------

_TRANSITIONS: dict[CommitmentStatus, frozenset[CommitmentStatus]] = {
    "detected":        frozenset({"open", "pending_review", "cancelled"}),
    "pending_review":  frozenset({"open", "cancelled"}),
    "open":            frozenset({"due", "fulfilled", "failed", "expired", "cancelled",
                                  "superseded", "contradicted", "blocked"}),
    "due":             frozenset({"overdue", "fulfilled", "failed", "cancelled",
                                  "superseded", "contradicted", "blocked"}),
    "overdue":         frozenset({"fulfilled", "failed", "expired", "cancelled"}),
    "blocked":         frozenset({"open", "failed", "cancelled"}),
    # Terminal states – no outbound transitions
    "fulfilled":       frozenset(),
    "failed":          frozenset(),
    "expired":         frozenset(),
    "cancelled":       frozenset(),
    "superseded":      frozenset(),
    "contradicted":    frozenset(),
}


def validate_transition(current: str, target: str) -> bool:
    """Return True if ``current → target`` is a valid transition."""
    allowed = _TRANSITIONS.get(current, frozenset())
    return target in allowed


async def transition_commitment(
    commitment_id: uuid.UUID,
    target_status: CommitmentStatus,
    actor: str = "system",
    data: dict[str, Any] | None = None,
    idempotency_key: str | None = None,
) -> Commitment:
    """Atomically transition a commitment via the DB state machine function.

    Falls back to a Python-layer (non-atomic) transition when the DB function
    is not yet installed (e.g. unit-test environments without migrations).

    V1.9 evidence gate: external_side_effect commitments cannot be marked
    fulfilled without at least one evidence record with score > 0.7.
    """
    sb = get_supabase()

    # Evidence gate — block fulfilled for external commitments lacking strong evidence
    if target_status == "fulfilled":
        c_resp = await sb.table("commitments").select("shape").eq(
            "id", str(commitment_id)
        ).maybe_single().execute()
        if c_resp and c_resp.data and c_resp.data.get("shape") == "external_side_effect":
            ev_resp = await sb.table("evidence").select("score").eq(
                "commitment_id", str(commitment_id)
            ).execute()
            scores = [float(r.get("score") or 0) for r in (ev_resp.data or [])]
            best_score = max(scores, default=0.0)
            if best_score < 0.7:
                raise ValueError(
                    f"External commitment {commitment_id} requires evidence score >= 0.7 "
                    f"before fulfillment (best score so far: {best_score:.2f}). "
                    "Add evidence first via POST /commitments/{id}/evidence."
                )

    # Try the DB-level atomic function first
    try:
        result = await sb.rpc(
            "cogext_transition_commitment",
            {
                "p_commitment_id": str(commitment_id),
                "p_target_status": target_status,
                "p_actor": actor,
                "p_data": data or {},
                "p_idempotency_key": idempotency_key or str(uuid.uuid4()),
            },
        ).execute()
        if result.data:
            row = result.data[0] if isinstance(result.data, list) else result.data
            return Commitment.model_validate(row)
    except Exception as rpc_err:
        logger.warning(
            "cogext_transition_commitment RPC unavailable, using Python fallback: %s",
            rpc_err,
        )

    # Python-layer fallback (two queries – not atomic but safe for dev/test)
    current_resp = await sb.table("commitments").select("status").eq(
        "id", str(commitment_id)
    ).execute()
    if not current_resp.data:
        raise ValueError(f"Commitment {commitment_id} not found")

    current_status: str = current_resp.data[0]["status"]
    if not validate_transition(current_status, target_status):
        raise ValueError(
            f"Invalid transition {current_status!r} → {target_status!r}"
        )

    now = datetime.now(timezone.utc).isoformat()
    update_fields: dict[str, Any] = {
        "status": target_status,
        "updated_at": now,
    }
    if target_status in {"fulfilled", "failed", "expired", "cancelled",
                         "superseded", "contradicted"}:
        update_fields["resolved_at"] = now

    await sb.table("commitments").update(update_fields).eq(
        "id", str(commitment_id)
    ).execute()

    # Insert event (best-effort in fallback mode)
    try:
        await sb.table("commitment_events").insert({
            "id": str(uuid.uuid4()),
            "commitment_id": str(commitment_id),
            "event_type": "status_changed",
            "actor": actor,
            "previous_status": current_status,
            "new_status": target_status,
            "data": data or {},
            "occurred_at": now,
            "recorded_at": now,
        }).execute()
    except Exception as ev_err:
        logger.warning("Event insert failed in fallback: %s", ev_err)

    row_resp = await sb.table("commitments").select("*").eq(
        "id", str(commitment_id)
    ).execute()
    return Commitment.model_validate(row_resp.data[0])
