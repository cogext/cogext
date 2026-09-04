"""V1.9 – Route extracted commitments by shape and confidence into initial statuses.

Routing rules:
  external_side_effect → always 'pending_review' (requires human review before open)
  logged_intent, confidence >= 0.92 → 'open'
  logged_intent, confidence < 0.92  → 'pending_review'

Verifiability:
  verifier_query is None → verification_status = 'unverifiable'
  verifier_query present → verification_status = 'unverified' (default)

This module is a pure function with no DB side effects — all DB writes
happen in the ingest API handler.
"""
import uuid
from datetime import datetime, timezone as tz_module
from typing import Literal

from app.core.extractor import compute_idempotency_key
from app.models.commitment import Commitment, ExtractedCommitment

_OPEN_THRESHOLD = 0.92


def route_by_confidence(
    extracted: list[ExtractedCommitment],
    user_id: uuid.UUID,
    source_agent_id: uuid.UUID,
    target_agent_id: uuid.UUID | None = None,
    record_key: str | None = None,
    source_type: str = "agent_message",
    source_timestamp: datetime | None = None,
    source_message_id: str | None = None,
    actor_timezone: str = "UTC",
) -> list[Commitment]:
    now = datetime.now(tz_module.utc)
    ts = source_timestamp or now
    results: list[Commitment] = []

    for item in extracted:
        # Only ingest genuine commitments
        if item.classification != "genuine_commitment":
            continue

        # Route by shape first, then confidence
        shape = item.shape
        if shape == "external_side_effect":
            # External commitments always require human review before becoming open
            status: Literal["open", "pending_review"] = "pending_review"
        elif item.confidence >= _OPEN_THRESHOLD:
            status = "open"
        else:
            status = "pending_review"

        # Mark unverifiable upfront when the LLM could not produce a verifier query
        verifier_query = item.verifier_query
        verification_status = "unverifiable" if verifier_query is None else "unverified"

        idem_key = compute_idempotency_key(
            str(source_agent_id),
            item.promise_text,
            now,
        )
        results.append(
            Commitment(
                id=uuid.uuid4(),
                user_id=user_id,
                source_agent_id=source_agent_id,
                target_agent_id=target_agent_id,
                record_key=record_key,
                source_type=source_type,  # type: ignore[arg-type]
                source_timestamp=ts,
                source_message_id=source_message_id,
                promise_text=item.promise_text,
                action=item.action,
                object=item.object,
                recipient=item.recipient,
                due_condition=item.due_condition,
                deadline_expression=item.deadline_expression,
                conditions=item.conditions,
                status=status,
                confidence=item.confidence,
                classification=item.classification,
                shape=shape,
                verifier_query=verifier_query,
                verification_status=verification_status,
                idempotency_key=idem_key,
                created_at=now,
                updated_at=now,
            )
        )

    return results
