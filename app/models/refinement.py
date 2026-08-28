"""V1.7 – Commitment refinement / change-record model (append-only)."""
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

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
    supersedes: bool = False


class RefinementHistory(BaseModel):
    commitment_id: UUID
    changes: list[CommitmentChange] = Field(default_factory=list)
