"""V1.5 – Unit tests for the extended Commitment model (no DB required)."""
import uuid
from datetime import datetime, timezone

import pytest

from app.models.commitment import (
    Commitment,
    DueCondition,
    ExtractedCommitment,
    IngestRequest,
    IngestResponse,
)


def _make_due_condition(**kwargs) -> DueCondition:
    defaults = {"type": "time", "trigger_description": "by Friday"}
    defaults.update(kwargs)
    return DueCondition(**defaults)


def _make_commitment(**kwargs) -> Commitment:
    defaults = dict(
        user_id=uuid.uuid4(),
        source_agent_id=uuid.uuid4(),
        promise_text="I will send the report",
        due_condition=_make_due_condition(),
        confidence=0.95,
    )
    defaults.update(kwargs)
    return Commitment(**defaults)


# ---------------------------------------------------------------------------
# DueCondition
# ---------------------------------------------------------------------------

def test_due_condition_time():
    dc = _make_due_condition(type="time", deadline=datetime.now(timezone.utc))
    assert dc.type == "time"
    assert dc.deadline is not None


def test_due_condition_trigger_types():
    for t in ("event_implicit", "event_external", "state"):
        dc = _make_due_condition(type=t)
        assert dc.type == t


def test_due_condition_v17_trigger_fields():
    dc = _make_due_condition(status="satisfied", matched_at=datetime.now(timezone.utc))
    assert dc.status == "satisfied"
    assert dc.matched_at is not None


# ---------------------------------------------------------------------------
# Commitment – core fields
# ---------------------------------------------------------------------------

def test_commitment_minimal_fields():
    c = _make_commitment()
    assert c.promise_text == "I will send the report"
    assert c.status == "open"
    assert c.confidence == 0.95


def test_commitment_all_statuses():
    statuses = [
        "detected", "pending_review", "open", "due", "overdue",
        "fulfilled", "failed", "expired", "cancelled",
        "superseded", "contradicted", "blocked",
    ]
    for s in statuses:
        c = _make_commitment(status=s)
        assert c.status == s


def test_commitment_v15_optional_fields():
    c = _make_commitment(
        action="send",
        object="deployment report",
        recipient="Sarah",
        deadline_expression="by Friday at 5pm",
        conditions=["CI must pass"],
        priority="high",
    )
    assert c.action == "send"
    assert c.recipient == "Sarah"
    assert c.conditions == ["CI must pass"]
    assert c.priority == "high"


def test_commitment_mutable_defaults_are_independent():
    """Ensure list/dict fields don't share the same object across instances."""
    c1 = _make_commitment()
    c2 = _make_commitment()
    c1.conditions.append("something")
    assert c2.conditions == []

    c1.metadata["key"] = "value"
    assert "key" not in c2.metadata


def test_commitment_confidence_bounds():
    with pytest.raises(Exception):
        _make_commitment(confidence=1.1)
    with pytest.raises(Exception):
        _make_commitment(confidence=-0.1)


def test_commitment_classification_default():
    c = _make_commitment()
    assert c.classification == "genuine_commitment"


def test_commitment_hierarchy_fields():
    parent_id = uuid.uuid4()
    c = _make_commitment(parent_commitment_id=parent_id)
    assert c.parent_commitment_id == parent_id
    assert c.child_commitment_ids == []


# ---------------------------------------------------------------------------
# ExtractedCommitment – V1.5
# ---------------------------------------------------------------------------

def test_extracted_commitment_classification():
    ec = ExtractedCommitment(
        promise_text="I will send the report",
        due_condition=_make_due_condition(),
        confidence=0.9,
        classification="genuine_commitment",
        action="send",
        recipient="Sarah",
    )
    assert ec.classification == "genuine_commitment"
    assert ec.action == "send"
    assert ec.recipient == "Sarah"
    assert ec.conditions == []


def test_extracted_commitment_other_classifications():
    for cls in ("intention", "question", "suggestion", "hypothetical", "quoted_statement"):
        ec = ExtractedCommitment(
            promise_text="text",
            due_condition=_make_due_condition(),
            confidence=0.7,
            classification=cls,
        )
        assert ec.classification == cls


# ---------------------------------------------------------------------------
# IngestRequest – V1.5 extended fields
# ---------------------------------------------------------------------------

def test_ingest_request_extended():
    req = IngestRequest(
        user_id=uuid.uuid4(),
        source_agent_id=uuid.uuid4(),
        message="I will do the thing",
        source_type="webhook",
        source_message_id="msg-123",
    )
    assert req.source_type == "webhook"
    assert req.source_message_id == "msg-123"
    assert req.metadata == {}
