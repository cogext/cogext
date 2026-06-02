"""Unit tests for the confidence scorer / router."""
import uuid

import pytest

from app.core.scorer import route_by_confidence
from app.models.commitment import DueCondition, ExtractedCommitment

_USER_ID = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
_AGENT_ID = uuid.UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")


def _make_extracted(confidence: float) -> ExtractedCommitment:
    return ExtractedCommitment(
        promise_text="I will send the report",
        due_condition=DueCondition(
            type="time",
            trigger_description="by Friday",
        ),
        confidence=confidence,
    )


def test_high_confidence_gets_open_status():
    result = route_by_confidence(
        [_make_extracted(0.95)], _USER_ID, _AGENT_ID
    )
    assert result[0].status == "open"


def test_exactly_at_threshold_gets_open():
    result = route_by_confidence(
        [_make_extracted(0.92)], _USER_ID, _AGENT_ID
    )
    assert result[0].status == "open"


def test_below_threshold_gets_pending_review():
    result = route_by_confidence(
        [_make_extracted(0.91)], _USER_ID, _AGENT_ID
    )
    assert result[0].status == "pending_review"


def test_low_confidence_gets_pending_review():
    result = route_by_confidence(
        [_make_extracted(0.5)], _USER_ID, _AGENT_ID
    )
    assert result[0].status == "pending_review"


def test_each_commitment_gets_unique_id():
    extracted = [_make_extracted(0.95), _make_extracted(0.95)]
    results = route_by_confidence(extracted, _USER_ID, _AGENT_ID)
    ids = [r.id for r in results]
    assert ids[0] != ids[1]
    assert all(isinstance(i, uuid.UUID) for i in ids)


def test_each_commitment_gets_idempotency_key():
    extracted = [_make_extracted(0.95), _make_extracted(0.80)]
    results = route_by_confidence(extracted, _USER_ID, _AGENT_ID)
    for r in results:
        assert r.idempotency_key is not None
        assert len(r.idempotency_key) == 64


def test_user_and_agent_ids_are_preserved():
    target = uuid.uuid4()
    results = route_by_confidence(
        [_make_extracted(0.95)],
        user_id=_USER_ID,
        source_agent_id=_AGENT_ID,
        target_agent_id=target,
        record_key="proj-123",
    )
    assert results[0].user_id == _USER_ID
    assert results[0].source_agent_id == _AGENT_ID
    assert results[0].target_agent_id == target
    assert results[0].record_key == "proj-123"


def test_empty_input_returns_empty_list():
    assert route_by_confidence([], _USER_ID, _AGENT_ID) == []
