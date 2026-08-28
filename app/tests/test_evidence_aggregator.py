"""V1.6 – Unit tests for evidence aggregation (no DB required)."""
import uuid

import pytest

from app.core.evidence_aggregator import (
    FIELD_WEIGHTS,
    aggregate_evidence,
    score_evidence_against_commitment,
)
from app.models.evidence import Evidence, FieldMatchDetail


def _make_evidence(match_details: list[FieldMatchDetail], score: float = 0.0) -> Evidence:
    return Evidence(
        id=uuid.uuid4(),
        commitment_id=uuid.uuid4(),
        source="test",
        data={},
        strength="supporting",
        score=score,
        match_details=match_details,
    )


def _detail(field: str, matched: bool, score: float) -> FieldMatchDetail:
    return FieldMatchDetail(
        field=field,
        weight=FIELD_WEIGHTS[field],
        matched=matched,
        score_contribution=score,
    )


# ---------------------------------------------------------------------------
# Field weights
# ---------------------------------------------------------------------------

def test_field_weights_sum_to_one():
    total = sum(FIELD_WEIGHTS.values())
    assert abs(total - 1.0) < 1e-9


# ---------------------------------------------------------------------------
# aggregate_evidence
# ---------------------------------------------------------------------------

def test_empty_evidence_returns_zero_score():
    cid = uuid.uuid4()
    result = aggregate_evidence(cid, [])
    assert result.aggregate_score == 0.0
    assert result.meets_threshold is False
    assert result.total_records == 0


def test_single_record_full_match():
    """One record matching all fields → score = 1.0."""
    details = [
        _detail("action",    matched=True,  score=FIELD_WEIGHTS["action"]),
        _detail("recipient", matched=True,  score=FIELD_WEIGHTS["recipient"]),
        _detail("object",    matched=True,  score=FIELD_WEIGHTS["object"]),
        _detail("deadline",  matched=True,  score=FIELD_WEIGHTS["deadline"]),
    ]
    ev = _make_evidence(details, score=1.0)
    result = aggregate_evidence(ev.commitment_id, [ev])
    assert abs(result.aggregate_score - 1.0) < 1e-6
    assert result.meets_threshold is True


def test_two_records_covering_different_fields():
    """Critical: two partial records collectively cover all fields."""
    cid = uuid.uuid4()

    # Record 1: covers action (0.40) and recipient (0.30)
    ev1 = Evidence(
        id=uuid.uuid4(),
        commitment_id=cid,
        source="a",
        data={},
        strength="supporting",
        score=0.7,
        match_details=[
            _detail("action",    matched=True,  score=0.40),
            _detail("recipient", matched=True,  score=0.30),
            _detail("object",    matched=False, score=0.0),
            _detail("deadline",  matched=False, score=0.0),
        ],
    )
    # Record 2: covers object (0.20) and deadline (0.10)
    ev2 = Evidence(
        id=uuid.uuid4(),
        commitment_id=cid,
        source="b",
        data={},
        strength="supporting",
        score=0.3,
        match_details=[
            _detail("action",    matched=False, score=0.0),
            _detail("recipient", matched=False, score=0.0),
            _detail("object",    matched=True,  score=0.20),
            _detail("deadline",  matched=True,  score=0.10),
        ],
    )
    result = aggregate_evidence(cid, [ev1, ev2])
    # Both records together cover all fields → aggregate should be ~1.0
    assert abs(result.aggregate_score - 1.0) < 1e-6
    assert result.meets_threshold is True
    assert result.total_records == 2


def test_no_averaging_uses_best_field_score():
    """If two records both cover 'action', take the BEST, not the average."""
    cid = uuid.uuid4()
    ev1 = Evidence(
        id=uuid.uuid4(), commitment_id=cid, source="a", data={},
        strength="weak", score=0.1,
        match_details=[_detail("action", True, 0.10),
                       _detail("recipient", False, 0.0),
                       _detail("object", False, 0.0),
                       _detail("deadline", False, 0.0)],
    )
    ev2 = Evidence(
        id=uuid.uuid4(), commitment_id=cid, source="b", data={},
        strength="strong", score=0.4,
        match_details=[_detail("action", True, 0.40),
                       _detail("recipient", False, 0.0),
                       _detail("object", False, 0.0),
                       _detail("deadline", False, 0.0)],
    )
    result = aggregate_evidence(cid, [ev1, ev2])
    # action coverage should be 0.40 (best), not 0.25 (average)
    assert result.field_coverage["action"] == pytest.approx(0.40)


# ---------------------------------------------------------------------------
# score_evidence_against_commitment
# ---------------------------------------------------------------------------

def test_score_full_match():
    data = {"action": "send", "recipient": "Sarah", "object": "report", "deadline": "Friday"}
    score, details = score_evidence_against_commitment(
        evidence_data=data,
        commitment_action="send",
        commitment_recipient="Sarah",
        commitment_object="report",
        commitment_deadline="Friday",
    )
    assert score == pytest.approx(1.0)
    assert all(d.matched for d in details)


def test_score_no_match():
    score, details = score_evidence_against_commitment(
        evidence_data={"something": "unrelated"},
        commitment_action="send",
        commitment_recipient="Sarah",
        commitment_object="report",
        commitment_deadline="Friday",
    )
    assert score == 0.0
    assert not any(d.matched for d in details)


def test_score_partial_match():
    data = {"text": "send the thing to Sarah"}
    score, details = score_evidence_against_commitment(
        evidence_data=data,
        commitment_action="send",
        commitment_recipient="Sarah",
        commitment_object="contract",   # not in data
        commitment_deadline=None,
    )
    # Should match action (0.40) and recipient (0.30)
    assert score == pytest.approx(0.70)
