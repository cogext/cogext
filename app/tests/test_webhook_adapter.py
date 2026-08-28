"""V1.7 – Unit tests for the GenericWebhookAdapter (no DB, no network)."""
import hashlib
from datetime import datetime, timezone

import pytest

from app.core.evidence_adapters.webhook import GenericWebhookAdapter


@pytest.fixture
def adapter():
    return GenericWebhookAdapter("github")


def test_normalises_basic_payload(adapter):
    payload = {
        "id": "evt-001",
        "occurred_at": "2026-08-28T10:00:00Z",
        "actor": "alice",
        "action": "push",
    }
    ev = adapter.normalise(payload)
    assert ev.external_event_id == "evt-001"
    assert ev.external_system == "github"
    assert ev.source == "webhook:github"
    assert ev.actor == "alice"
    assert ev.occurred_at.tzinfo is not None


def test_normalises_timestamp_field_fallback(adapter):
    payload = {"id": "evt-002", "timestamp": "2026-08-27T08:00:00Z"}
    ev = adapter.normalise(payload)
    assert ev.occurred_at.year == 2026
    assert ev.occurred_at.month == 8


def test_generates_idempotency_key(adapter):
    payload = {"id": "evt-003"}
    ev = adapter.normalise(payload)
    assert ev.idempotency_key is not None
    expected = hashlib.sha256(b"github:evt-003").hexdigest()
    assert ev.idempotency_key == expected


def test_idempotency_key_same_for_same_payload(adapter):
    payload = {"id": "evt-004"}
    ev1 = adapter.normalise(payload)
    ev2 = adapter.normalise(payload)
    assert ev1.idempotency_key == ev2.idempotency_key


def test_idempotency_key_differs_for_different_events(adapter):
    ev1 = adapter.normalise({"id": "evt-005"})
    ev2 = adapter.normalise({"id": "evt-006"})
    assert ev1.idempotency_key != ev2.idempotency_key


def test_can_handle_any_dict(adapter):
    assert adapter.can_handle({}) is True
    assert adapter.can_handle({"a": 1}) is True


def test_missing_id_uses_content_hash(adapter):
    payload = {"action": "push", "repo": "cogext"}
    ev = adapter.normalise(payload)
    assert ev.external_event_id is not None
    assert len(ev.external_event_id) > 0


def test_adapter_version_is_set(adapter):
    ev = adapter.normalise({"id": "evt-007"})
    assert ev.adapter_version == "1.0"


def test_raw_reference_is_truncated_to_500(adapter):
    big_payload = {"id": "big", "data": "x" * 1000}
    ev = adapter.normalise(big_payload)
    assert len(ev.raw_reference) <= 500
