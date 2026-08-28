"""V1.5 – Append-only event model.
No UPDATE or DELETE is permitted on events at the application layer.
The DB migration also installs a trigger that rejects UPDATE/DELETE.
"""
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

EventType = Literal[
    "detected",
    "status_changed",
    "deadline_changed",
    "evidence_submitted",
    "evidence_verified",
    "evidence_rejected",
    "contradiction_detected",
    "superseded",
    "fulfilled",
    "failed",
    "expired",
    "cancelled",
    "blocked",
    "unblocked",
    "review_accepted",
    "review_rejected",
    "condition_satisfied",
    "condition_unsatisfied",
    "nudge_sent",
    "dependency_added",
    "dependency_resolved",
    "refinement_applied",
    "webhook_delivered",
    "webhook_failed",
    "redacted",
]


class CommitmentEvent(BaseModel):
    """Immutable event record — never mutated after creation."""
    id: UUID | None = None
    commitment_id: UUID
    event_type: EventType
    actor: str | None = None          # who/what caused the event
    previous_status: str | None = None
    new_status: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)
    occurred_at: datetime | None = None
    recorded_at: datetime | None = None
    idempotency_key: str | None = None


class EventListResponse(BaseModel):
    events: list[CommitmentEvent]
    total: int
