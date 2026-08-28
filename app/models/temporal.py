"""V1.6 – Temporal intelligence model."""
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

ResolutionMethod = Literal[
    "deterministic",   # parsed directly from expression
    "llm_fallback",    # LLM resolved ambiguity
    "default",         # fallback to end-of-day / end-of-week heuristic
]


class TemporalResolution(BaseModel):
    """Result of resolving a natural-language deadline expression."""
    raw_expression: str
    anchor_timestamp: datetime            # source message timestamp (NOT server time)
    timezone: str = "UTC"
    resolved_deadline: datetime | None = None
    resolution_method: ResolutionMethod = "deterministic"
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    ambiguity: str | None = None          # human-readable description of ambiguity
    error: str | None = None              # if resolution failed
