from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


class DueCondition(BaseModel):
    type: Literal["time", "event_implicit", "event_external", "state"]
    deadline: datetime | None = None
    trigger_description: str | None = None
    entity_ref: str | None = None
    match_threshold: float = 0.88
    partial_match_threshold: float = 0.65


class Commitment(BaseModel):
    id: UUID | None = None
    user_id: UUID
    source_agent_id: UUID
    target_agent_id: UUID | None = None
    record_key: str | None = None
    promise_text: str
    due_condition: DueCondition
    status: Literal["open", "fulfilled", "expired", "contradicted", "pending_review"] = "open"
    confidence: float = Field(..., ge=0.0, le=1.0)
    idempotency_key: str | None = None
    created_at: datetime | None = None


class IngestRequest(BaseModel):
    user_id: UUID
    source_agent_id: UUID
    message: str
    target_agent_id: UUID | None = None
    record_key: str | None = None


class IngestResponse(BaseModel):
    commitments: list[Commitment]


class ExtractedCommitment(BaseModel):
    promise_text: str
    due_condition: DueCondition
    confidence: float = Field(..., ge=0.0, le=1.0)
