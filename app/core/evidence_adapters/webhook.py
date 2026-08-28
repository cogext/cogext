"""V1.7 – Generic inbound webhook evidence adapter.

Converts any incoming webhook payload into normalised evidence.
Callers must supply at minimum: external_system, external_event_id, occurred_at.
"""
import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any

from app.core.evidence_adapters.base import NormalisedEvidence

logger = logging.getLogger(__name__)

ADAPTER_VERSION = "1.0"


class GenericWebhookAdapter:
    """Generic adapter for arbitrary webhook payloads."""

    def __init__(self, system_name: str) -> None:
        self.system_name = system_name

    def can_handle(self, raw_event: dict[str, Any]) -> bool:
        return isinstance(raw_event, dict)

    def normalise(self, raw_event: dict[str, Any]) -> NormalisedEvidence:
        external_event_id = str(
            raw_event.get("id")
            or raw_event.get("event_id")
            or raw_event.get("uid")
            or _content_hash(raw_event)
        )

        occurred_raw = raw_event.get("occurred_at") or raw_event.get(
            "timestamp"
        ) or raw_event.get("created_at")
        occurred_at = _parse_dt(occurred_raw) or datetime.now(timezone.utc)

        actor = (
            raw_event.get("actor")
            or raw_event.get("user")
            or (
                raw_event.get("sender", {}).get("login")
                if isinstance(raw_event.get("sender"), dict)
                else raw_event.get("sender")
            )
        )

        idem_key = hashlib.sha256(
            f"{self.system_name}:{external_event_id}".encode()
        ).hexdigest()

        return NormalisedEvidence(
            external_event_id=external_event_id,
            external_system=self.system_name,
            source=f"webhook:{self.system_name}",
            actor=str(actor) if actor else None,
            data=raw_event,
            occurred_at=occurred_at,
            recorded_at=datetime.now(timezone.utc),
            raw_reference=json.dumps(raw_event)[:500],
            idempotency_key=idem_key,
            adapter_version=ADAPTER_VERSION,
        )


def _content_hash(data: dict) -> str:
    return hashlib.sha256(
        json.dumps(data, sort_keys=True, default=str).encode()
    ).hexdigest()[:16]


def _parse_dt(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        dt = datetime.fromisoformat(str(value))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None
