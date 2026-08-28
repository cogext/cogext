"""V1.5 – Route extracted commitments by confidence into initial statuses.

High confidence (>= _OPEN_THRESHOLD) → 'open'
Below threshold → 'pending_review'

This module is a pure function with no DB side effects — all DB writes
happen in the ingest API handler.
"""
import uuid
from datetime import datetime, timezone
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
) -> list[Commitment]:
    now = datetime.now(timezone.utc)
    ts = source_timestamp or now
    results: list[Commitment] = []

    for item in extracted:
        # Only ingest genuine commitments
        if item.classification != "genuine_commitment":
            continue

        status: Literal["open", "pending_review"] = (
            "open" if item.confidence >= _OPEN_THRESHOLD else "pending_review"
        )
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
                idempotency_key=idem_key,
                created_at=now,
                updated_at=now,
            )
        )

    return results
