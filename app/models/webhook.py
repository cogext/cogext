"""V1.7 – Outbound webhook subscription and delivery models."""
from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class WebhookSubscription(BaseModel):
    id: UUID | None = None
    endpoint: str
    active: bool = True
    subscribed_event_types: list[str] = Field(default_factory=list)
    # secret is write-only — never exposed in API responses
    created_at: datetime | None = None
    updated_at: datetime | None = None
    failure_count: int = 0
    last_delivery_at: datetime | None = None


class WebhookDelivery(BaseModel):
    id: UUID | None = None
    event_id: UUID
    webhook_id: UUID
    attempt: int = 1
    status: str = "pending"           # pending | delivered | failed | retrying
    response_code: int | None = None
    truncated_response: str | None = None
    delivered_at: datetime | None = None
    next_retry_at: datetime | None = None


class CreateWebhookRequest(BaseModel):
    endpoint: str
    subscribed_event_types: list[str] = Field(default_factory=list)
    secret: str                       # caller supplies; stored hashed


class UpdateWebhookRequest(BaseModel):
    endpoint: str | None = None
    active: bool | None = None
    subscribed_event_types: list[str] | None = None
    secret: str | None = None


class WebhookSubscriptionResponse(BaseModel):
    """Like WebhookSubscription but without the secret."""
    id: UUID | None = None
    endpoint: str
    active: bool
    subscribed_event_types: list[str]
    created_at: datetime | None = None
    updated_at: datetime | None = None
    failure_count: int
    last_delivery_at: datetime | None = None
