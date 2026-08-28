"""Base protocol / interface for all evidence source adapters."""
from datetime import datetime, timezone
from typing import Any, Protocol
from uuid import UUID

from pydantic import BaseModel, Field


class NormalisedEvidence(BaseModel):
    """Common wire format produced by every adapter."""
    external_event_id: str
    external_system: str
    source: str
    actor: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)
    occurred_at: datetime
    recorded_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    provenance: str | None = None
    raw_reference: str | None = None
    idempotency_key: str | None = None
    adapter_version: str = "1.0"


class EvidenceAdapter(Protocol):
    """Protocol that every adapter must satisfy."""

    def normalise(self, raw_event: dict[str, Any]) -> NormalisedEvidence:
        """Convert *raw_event* from the external system to :class:`NormalisedEvidence`."""
        ...

    def can_handle(self, raw_event: dict[str, Any]) -> bool:
        """Return True if this adapter can process *raw_event*."""
        ...
