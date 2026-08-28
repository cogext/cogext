"""V1.6/V1.7 – Evidence model with graduated strength and field-level scores."""
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

EvidenceStrength = Literal["strong", "supporting", "weak", "contradictory"]


class FieldMatchDetail(BaseModel):
    field: str                         # action | recipient | object | deadline
    weight: float                      # canonical weight for this field
    matched: bool
    matched_value: str | None = None
    score_contribution: float = 0.0


class Evidence(BaseModel):
    id: UUID | None = None
    commitment_id: UUID

    # Source info
    source: str                        # e.g. "webhook:github", "manual", "api"
    external_system: str | None = None
    external_event_id: str | None = None

    # Actor / provenance
    actor: str | None = None
    provenance: str | None = None
    raw_reference: str | None = None
    adapter_version: str | None = None

    # Content
    data: dict[str, Any] = Field(default_factory=dict)

    # Timing
    occurred_at: datetime | None = None
    recorded_at: datetime | None = None

    # V1.6 graduated evidence
    strength: EvidenceStrength = "supporting"
    score: float = Field(default=0.0, ge=0.0, le=1.0)
    match_details: list[FieldMatchDetail] = Field(default_factory=list)

    # Verification
    verified: bool = False
    verification_details: dict[str, Any] = Field(default_factory=dict)

    # Idempotency (V1.7: unique per external_system + external_event_id)
    idempotency_key: str | None = None


class SubmitEvidenceRequest(BaseModel):
    commitment_id: UUID
    source: str
    data: dict[str, Any]
    occurred_at: datetime | None = None
    external_system: str | None = None
    external_event_id: str | None = None
    actor: str | None = None
    provenance: str | None = None
    raw_reference: str | None = None


class EvidenceAggregation(BaseModel):
    """Result of aggregating all evidence records for a commitment."""
    commitment_id: UUID
    total_records: int
    aggregate_score: float
    field_coverage: dict[str, float]   # field -> best score across all records
    meets_threshold: bool
    threshold: float
    evidence_ids: list[UUID] = Field(default_factory=list)
