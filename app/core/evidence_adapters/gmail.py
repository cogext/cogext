"""Gmail evidence adapter — V2.0.

Handles raw webhook events from Gmail (via Google Pub/Sub push or a polling integration).
Normalises to NormalisedEvidence so the Verifier Engine can score them.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

from app.core.evidence_adapters.base import EvidenceAdapter, NormalisedEvidence


class GmailAdapter(EvidenceAdapter):
    """Adapter for Gmail message events.

    Expected raw_event shape (from Google Pub/Sub decoded payload or direct API):
    {
        "source": "gmail",
        "message_id": "...",
        "thread_id": "...",
        "from": "sender@example.com",
        "to": "recipient@example.com",
        "subject": "...",
        "snippet": "...",        # first 200 chars of body
        "date": "2024-01-15T10:30:00Z",
        "labels": ["SENT", "INBOX"],
    }
    """

    def can_handle(self, raw_event: dict[str, Any]) -> bool:
        return raw_event.get("source") == "gmail" and "message_id" in raw_event

    def normalise(self, raw_event: dict[str, Any]) -> NormalisedEvidence:
        message_id = raw_event["message_id"]
        # Stable external ID: sha256 of message_id to stay URL-safe
        external_id = hashlib.sha256(f"gmail:{message_id}".encode()).hexdigest()[:32]

        occurred_at = raw_event.get("date")
        if occurred_at:
            try:
                dt = datetime.fromisoformat(occurred_at.replace("Z", "+00:00"))
            except ValueError:
                dt = datetime.now(timezone.utc)
        else:
            dt = datetime.now(timezone.utc)

        actor = raw_event.get("from")
        snippet = raw_event.get("snippet", "")
        subject = raw_event.get("subject", "")
        labels = raw_event.get("labels", [])

        return NormalisedEvidence(
            external_event_id=external_id,
            external_system="gmail",
            source=f"gmail:message:{message_id}",
            actor=actor,
            data={
                "message_id": message_id,
                "thread_id": raw_event.get("thread_id"),
                "from": actor,
                "to": raw_event.get("to"),
                "subject": subject,
                "snippet": snippet,
                "labels": labels,
            },
            occurred_at=dt,
            provenance=f"gmail:thread:{raw_event.get('thread_id', message_id)}",
            raw_reference=f"https://mail.google.com/mail/u/0/#all/{raw_event.get('thread_id', message_id)}",
            idempotency_key=f"gmail:{message_id}",
            adapter_version="2.0",
        )
