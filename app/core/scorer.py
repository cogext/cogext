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
) -> list[Commitment]:
    now = datetime.now(timezone.utc)
    results: list[Commitment] = []

    for item in extracted:
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
                promise_text=item.promise_text,
                due_condition=item.due_condition,
                status=status,
                confidence=item.confidence,
                idempotency_key=idem_key,
                created_at=now,
            )
        )

    return results
