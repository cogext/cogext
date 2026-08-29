"""V1.5 – Full Commitment model with all spec fields.
All mutable defaults use Field(default_factory=…) — no bare list/dict defaults.
"""
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator


# ---------------------------------------------------------------------------
# Status / transition types
# ---------------------------------------------------------------------------

CommitmentStatus = Literal[
    "detected",
    "pending_review",
    "open",
    "due",
    "overdue",
    "fulfilled",
    "failed",
    "expired",
    "cancelled",
    "superseded",
    "contradicted",
    "blocked",
]

CommitmentSourceType = Literal[
    "agent_message", "webhook", "api", "manual"
]

VerificationStatus = Literal[
    "unverified", "pending", "verified", "rejected", "insufficient"
]

ClassificationType = Literal[
    "genuine_commitment", "intention", "question",
    "suggestion", "hypothetical", "quoted_statement",
]


# ---------------------------------------------------------------------------
# Sub-models
# ---------------------------------------------------------------------------

class DueCondition(BaseModel):
    """Encodes when/how a commitment becomes due."""
    type: Literal["time", "event_implicit", "event_external", "state"]
    deadline: datetime | None = None
    trigger_description: str | None = None
    entity_ref: str | None = None
    match_threshold: float = 0.88
    partial_match_threshold: float = 0.65
    # V1.7 trigger fields
    status: Literal["pending", "satisfied", "unsatisfied"] = "pending"
    matched_at: datetime | None = None
    evidence_id: UUID | None = None


class EvidenceRequirement(BaseModel):
    field: str
    description: str
    required: bool = True


# ---------------------------------------------------------------------------
# Core Commitment model  (V1.5 full spec)
# ---------------------------------------------------------------------------

class Commitment(BaseModel):
    # ---------------------------------------------------------------------------
    # Coerce DB NULLs to model defaults (old rows may lack column defaults)
    # ---------------------------------------------------------------------------
    @model_validator(mode="before")
    @classmethod
    def _coerce_nulls(cls, data: dict) -> dict:
        if not isinstance(data, dict):
            return data
        _list_defaults: dict[str, list] = {
            "child_commitment_ids": [],
            "evidence_requirements": [],
            "evidence_found": [],
            "conditions": [],
        }
        _scalar_defaults: dict[str, object] = {
            "verification_status": "unverified",
            "priority": "medium",
            "classification": "genuine_commitment",
            "source_type": "agent_message",
            "status": "open",
            "metadata": {},
            "due_condition": None,  # keep None for optional handling below
        }
        for field, default in _list_defaults.items():
            if data.get(field) is None:
                data[field] = default
        for field, default in _scalar_defaults.items():
            if data.get(field) is None and default is not None:
                data[field] = default
        return data

    # Identity
    id: UUID | None = None
    tenant_id: UUID | None = None
    user_id: UUID
    source_agent_id: UUID
    target_agent_id: UUID | None = None
    source_message_id: str | None = None
    source_type: CommitmentSourceType = "agent_message"
    source_timestamp: datetime | None = None

    # Core promise
    action: str | None = None
    object: str | None = None
    recipient: str | None = None
    original_text: str | None = None
    normalized_text: str | None = None
    promise_text: str

    # Deadline / timing
    deadline: datetime | None = None
    deadline_type: Literal["absolute", "relative", "conditional", "none"] | None = None
    deadline_expression: str | None = None
    due_condition: DueCondition

    # Hierarchy
    parent_commitment_id: UUID | None = None
    child_commitment_ids: list[UUID] = Field(default_factory=list)
    supersedes: UUID | None = None
    superseded_by: UUID | None = None

    # Evidence
    evidence_requirements: list[EvidenceRequirement] = Field(default_factory=list)
    evidence_found: list[dict[str, Any]] = Field(default_factory=list)
    verification_status: VerificationStatus = "unverified"
    verification_reason: str | None = None

    # Lifecycle state
    status: CommitmentStatus = "open"
    confidence: float = Field(..., ge=0.0, le=1.0)
    conditions: list[str] = Field(default_factory=list)
    priority: Literal["low", "medium", "high", "critical"] = "medium"

    # Metadata
    metadata: dict[str, Any] = Field(default_factory=dict)
    extraction_model: str | None = None
    extraction_version: str | None = None

    # Timestamps
    created_at: datetime | None = None
    updated_at: datetime | None = None
    resolved_at: datetime | None = None

    # Idempotency / linking
    idempotency_key: str | None = None
    record_key: str | None = None

    # V1.7 classification
    classification: ClassificationType = "genuine_commitment"


# ---------------------------------------------------------------------------
# API request/response models
# ---------------------------------------------------------------------------

class IngestRequest(BaseModel):
    user_id: UUID
    source_agent_id: UUID
    message: str
    target_agent_id: UUID | None = None
    record_key: str | None = None
    source_type: CommitmentSourceType = "agent_message"
    source_timestamp: datetime | None = None
    source_message_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class IngestResponse(BaseModel):
    commitments: list[Commitment]


class ExtractedCommitment(BaseModel):
    promise_text: str
    due_condition: DueCondition
    confidence: float = Field(..., ge=0.0, le=1.0)
    # V1.5 classification
    classification: ClassificationType = "genuine_commitment"
    action: str | None = None
    object: str | None = None
    recipient: str | None = None
    conditions: list[str] = Field(default_factory=list)
    deadline_expression: str | None = None
