"""V1.7 – Commitment refinement / change-record model (append-only)."""
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

# Fields a refinement may change: the content of the promise, matching the
# ChangeType values below. Everything else is identity, ownership, lifecycle,
# verification or audit state (user_id, status, receipt_token, id,
# idempotency_key, created_at, shape, verifier_query, superseded_by, ...).
# Status moves only through the state machine; supersession only through
# ApplyRefinementRequest.supersedes.
REFINABLE_FIELDS = frozenset({
    "promise_text",
    "action",
    "object",
    "recipient",
    "deadline",
    "deadline_type",
    "deadline_expression",
    "due_condition",
    "conditions",
    "priority",
})

ChangeType = Literal[
    "supersedes",
    "refines",
    "clarifies",
    "changes_deadline",
    "changes_recipient",
    "changes_scope",
    "changes_priority",
    "changes_condition",
]


class CommitmentChange(BaseModel):
    """Immutable record of a single field change. Never mutated after creation."""
    id: UUID | None = None
    commitment_id: UUID
    field: str
    previous_value: Any | None = None
    new_value: Any | None = None
    change_type: str                   # one of ChangeType values
    actor: str
    timestamp: datetime | None = None
    reason: str | None = None


class ApplyRefinementRequest(BaseModel):
    commitment_id: UUID
    changes: list[dict[str, Any]]      # [{field, new_value, change_type, reason}]
    actor: str
    supersedes: UUID | None = None  # UUID of the commitment this one supersedes

    @field_validator("changes")
    @classmethod
    def _only_refinable_fields(cls, changes: list[dict[str, Any]]) -> list[dict[str, Any]]:
        # A change with no field is skipped by the handler, as before.
        rejected = [
            ch.get("field") for ch in changes
            if ch.get("field") not in (None, "")
            and not (isinstance(ch.get("field"), str) and ch.get("field") in REFINABLE_FIELDS)
        ]
        if rejected:
            raise ValueError(
                f"field(s) {rejected!r} cannot be changed by a refinement; "
                f"allowed: {sorted(REFINABLE_FIELDS)}"
            )
        return changes


class RefinementHistory(BaseModel):
    commitment_id: UUID
    changes: list[CommitmentChange] = Field(default_factory=list)
