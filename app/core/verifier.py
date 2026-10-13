"""Verifier Engine — V2.0.

Dispatches a verification attempt for a commitment by routing to the
appropriate evidence adapter based on available evidence and the
commitment's verifier_query.

Pipeline:
  1. Load all registered adapters.
  2. Filter to adapters that can_handle the raw event.
  3. Normalise → NormalisedEvidence.
  4. Score evidence relevance against the commitment.
  5. If best score ≥ threshold → mark verified / rejected via state machine.
"""
from __future__ import annotations

import hashlib
import logging
from typing import Any

from app.core.evidence_adapters.base import NormalisedEvidence
from app.core.evidence_adapters.gmail import GmailAdapter
import uuid as _uuid
from app.core.state_machine import transition_commitment
from app.db.connection import get_supabase
from app.models.commitment import Commitment
from app.llm.provider import extract_completion
import json

logger = logging.getLogger(__name__)

_ADAPTERS = [GmailAdapter()]

_VERIFY_THRESHOLD = 0.70
_REJECT_THRESHOLD = 0.30


async def run_verification(
    commitment: Commitment,
    raw_event: dict[str, Any],
    actor: str = "verifier_engine",
) -> dict[str, Any]:
    """Attempt to verify *commitment* using *raw_event* from any system.

    Returns a dict with keys: status, score, evidence_id, reason.
    """
    # Find an adapter that can handle this event
    adapter = next((a for a in _ADAPTERS if a.can_handle(raw_event)), None)
    if adapter is None:
        return {"status": "no_adapter", "score": 0.0, "evidence_id": None, "reason": "No adapter for event type"}

    evidence: NormalisedEvidence = adapter.normalise(raw_event)

    # Score relevance using LLM (cheap: short prompt)
    score, reason = await _score_evidence(commitment, evidence)

    # Verification may only succeed on evidence actually stored for this
    # commitment. If it is not stored, nothing else changes.
    sb = get_supabase()
    evidence_id = await _persist_evidence(sb, commitment, evidence, score)
    if evidence_id is None:
        return {
            "status": "evidence_not_recorded",
            "score": score,
            "evidence_id": None,
            "reason": "Evidence could not be recorded for this commitment; verification was not applied.",
        }

    # Transition commitment based on score
    if score >= _VERIFY_THRESHOLD:
        new_status = "fulfilled"
        v_status = "verified"
    elif score <= _REJECT_THRESHOLD:
        new_status = None  # don't auto-fail, just mark insufficient
        v_status = "insufficient"
    else:
        new_status = None
        v_status = "pending"

    if new_status and commitment.status != new_status:
        try:
            await transition_commitment(
                _uuid.UUID(str(commitment.id)),
                new_status,
                actor=actor,
                data={"evidence_id": evidence_id, "score": score, "reason": reason},
            )
        except Exception as e:
            # Do not record "verified" for a commitment whose status did not change.
            logger.warning("Verifier transition failed: %s", e)
            return {"status": "transition_failed", "score": score, "evidence_id": evidence_id, "reason": reason}

    # Update verification_status on commitment row directly
    try:
        await sb.table("commitments").update({
            "verification_status": v_status,
            "verification_reason": reason,
        }).eq("id", str(commitment.id)).execute()
    except Exception as e:
        logger.warning("verification_status update failed: %s", e)

    return {
        "status": v_status,
        "score": score,
        "evidence_id": evidence_id,
        "reason": reason,
    }


async def _persist_evidence(
    sb: Any, commitment: Commitment, evidence: NormalisedEvidence, score: float
) -> str | None:
    """Store *evidence* for *commitment*. Return the stored row's id, or None.

    The idempotency key has the same account-scoped format as
    app/api/evidence.py (and migration 012), so an external event is stored once
    per account and never collides with another account's evidence. A duplicate
    counts as stored only if the existing row belongs to this commitment.
    """
    idem_key = hashlib.sha256(
        f"{commitment.user_id}:{evidence.external_system}:{evidence.external_event_id}".encode()
    ).hexdigest()
    row = {
        "id": str(_uuid.uuid4()),
        "commitment_id": str(commitment.id),
        "source": evidence.source,
        "external_system": evidence.external_system,
        "external_event_id": evidence.external_event_id,
        "actor": evidence.actor,
        "provenance": evidence.provenance,
        "raw_reference": evidence.raw_reference,
        "adapter_version": evidence.adapter_version,
        "data": evidence.data,
        "occurred_at": evidence.occurred_at.isoformat(),
        "recorded_at": evidence.recorded_at.isoformat(),
        "score": score,
        "idempotency_key": idem_key,
    }
    try:
        result = await sb.table("evidence").upsert(
            row, on_conflict="idempotency_key", ignore_duplicates=True
        ).execute()
        if result and result.data:
            return row["id"]
        existing = await sb.table("evidence").select("id, commitment_id").eq(
            "idempotency_key", idem_key
        ).maybe_single().execute()
    except Exception as e:
        logger.warning("Evidence persist failed: %s", e)
        return None
    if existing and existing.data and str(existing.data.get("commitment_id")) == str(commitment.id):
        return str(existing.data["id"])
    return None


async def _score_evidence(commitment: Commitment, evidence: NormalisedEvidence) -> tuple[float, str]:
    """Ask the LLM to score how well *evidence* fulfils *commitment*."""
    verifier_q = commitment.verifier_query or f"Did the agent fulfil: {commitment.promise_text}?"
    prompt = f"""You are an evidence evaluator for an AI accountability system.

Commitment promise: "{commitment.promise_text}"
Verification question: "{verifier_q}"

Evidence from {evidence.external_system}:
Source: {evidence.source}
Actor: {evidence.actor}
Data: {json.dumps(evidence.data, default=str)[:800]}

Score 0.0–1.0 how strongly this evidence fulfils the commitment.
1.0 = conclusive proof of fulfilment
0.5 = partial evidence
0.0 = no relevance or proves non-fulfilment

Return ONLY valid JSON: {{"score": 0.85, "reason": "one sentence explanation"}}"""

    try:
        raw = extract_completion(prompt)
        parsed = json.loads(raw)
        score = float(parsed.get("score", 0.0))
        reason = str(parsed.get("reason", ""))
        return max(0.0, min(1.0, score)), reason
    except Exception as e:
        logger.warning("LLM evidence scoring failed: %s", e)
        return 0.0, "LLM scoring unavailable"
