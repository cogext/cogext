"""V1.6 – Commitment dependency graph model."""
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

DependencyType = Literal["blocks", "requires", "triggers"]


class CommitmentDependency(BaseModel):
    id: UUID | None = None
    source_commitment_id: UUID     # the upstream commitment
    target_commitment_id: UUID     # the downstream commitment (blocked/required)
    dependency_type: DependencyType
    created_at: datetime | None = None
    resolved_at: datetime | None = None
    metadata: dict = Field(default_factory=dict)


class AddDependencyRequest(BaseModel):
    source_commitment_id: UUID
    target_commitment_id: UUID
    dependency_type: DependencyType = "blocks"


class DependencyGraph(BaseModel):
    commitment_id: UUID
    blockers: list[CommitmentDependency] = Field(default_factory=list)
    blocking: list[CommitmentDependency] = Field(default_factory=list)
