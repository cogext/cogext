"""V1.7 – Human review model."""
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

ReviewStatus = Literal["pending", "assigned", "accepted", "rejected", "edited"]
ReviewReasonCode = Literal[
    "low_extraction_confidence",
    "ambiguous_deadline",
    "contradictory_commitment",
    "ambiguous_recipient",
    "ambiguous_action",
    "ambiguous_object",
    "suspicious_evidence",
    "manual_request",
]


class HumanReview(BaseModel):
    id: UUID | None = None
    commitment_id: UUID
    reviewer_id: UUID | None = None
    status: ReviewStatus = "pending"
    reason_code: ReviewReasonCode
    reason: str | None = None
    proposed_changes: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime | None = None
    assigned_at: datetime | None = None
    resolved_at: datetime | None = None
    resolution: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class CreateReviewRequest(BaseModel):
    commitment_id: UUID
    reason_code: ReviewReasonCode
    reason: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class AssignReviewRequest(BaseModel):
    reviewer_id: UUID


class AcceptReviewRequest(BaseModel):
    resolution: str | None = None
    proposed_changes: dict[str, Any] = Field(default_factory=dict)


class RejectReviewRequest(BaseModel):
    resolution: str


class EditReviewRequest(BaseModel):
    proposed_changes: dict[str, Any]
    resolution: str | None = None
