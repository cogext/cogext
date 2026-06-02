"""Unit tests for the extractor — LLM calls are mocked by default."""
import json
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from app.core.extractor import compute_idempotency_key, extract_commitments
from app.models.commitment import ExtractedCommitment

_CANONICAL_MSG = (
    "I'll send the quarterly report by Tuesday end of day and loop in Sarah "
    "after the sync. Once legal review is done I'll forward the contract."
)

_CANONICAL_RESPONSE = json.dumps([
    {
        "promise_text": "I will send the quarterly report",
        "due_condition": {
            "type": "time",
            "deadline": None,
            "trigger_description": "by Tuesday end of day",
            "entity_ref": None,
            "match_threshold": 0.88,
            "partial_match_threshold": 0.65,
        },
        "confidence": 0.95,
    },
    {
        "promise_text": "I will loop in Sarah",
        "due_condition": {
            "type": "event_implicit",
            "deadline": None,
            "trigger_description": "after the sync",
            "entity_ref": "Sarah",
            "match_threshold": 0.88,
            "partial_match_threshold": 0.65,
        },
        "confidence": 0.88,
    },
    {
        "promise_text": "I will forward the contract",
        "due_condition": {
            "type": "event_external",
            "deadline": None,
            "trigger_description": "once legal review is done",
            "entity_ref": "legal",
            "match_threshold": 0.88,
            "partial_match_threshold": 0.65,
        },
        "confidence": 0.93,
    },
])

_EMPTY_RESPONSE = json.dumps([])

_VAGUE_RESPONSE = json.dumps([
    {
        "promise_text": "We might talk",
        "due_condition": {
            "type": "event_implicit",
            "deadline": None,
            "trigger_description": "later",
            "entity_ref": None,
            "match_threshold": 0.88,
            "partial_match_threshold": 0.65,
        },
        "confidence": 0.55,
    }
])


def _mock_llm(response: str):
    """Patch extract_completion to return a fixed string without calling Groq."""
    return patch("app.core.extractor.extract_completion", return_value=response)


@pytest.mark.asyncio
async def test_extracts_three_from_canonical_message():
    with _mock_llm(_CANONICAL_RESPONSE):
        results = await extract_commitments(_CANONICAL_MSG)
    assert len(results) == 3
    types = {r.due_condition.type for r in results}
    assert types == {"time", "event_implicit", "event_external"}


@pytest.mark.asyncio
async def test_empty_for_no_commitment_message():
    with _mock_llm(_EMPTY_RESPONSE):
        results = await extract_commitments("Thanks for the update, sounds good.")
    assert results == []


@pytest.mark.asyncio
async def test_vague_message_low_confidence():
    with _mock_llm(_VAGUE_RESPONSE):
        results = await extract_commitments("Maybe we can talk later")
    assert len(results) == 1
    assert results[0].confidence < 0.7


@pytest.mark.asyncio
async def test_malformed_json_returns_empty_no_crash():
    with _mock_llm("this is not json { broken"):
        results = await extract_commitments("Something")
    assert results == []


@pytest.mark.asyncio
async def test_partial_invalid_items_are_dropped():
    """Valid + invalid items: only valid one survives."""
    mixed = json.dumps([
        {
            "promise_text": "I will do the thing",
            "due_condition": {
                "type": "time",
                "deadline": None,
                "trigger_description": "tomorrow",
                "entity_ref": None,
                "match_threshold": 0.88,
                "partial_match_threshold": 0.65,
            },
            "confidence": 0.9,
        },
        {"broken": "no required fields at all"},
    ])
    with _mock_llm(mixed):
        results = await extract_commitments("anything")
    assert len(results) == 1
    assert results[0].promise_text == "I will do the thing"


def test_idempotency_key_is_deterministic():
    now = datetime(2026, 1, 15, 10, 30, 0, tzinfo=timezone.utc)
    k1 = compute_idempotency_key("agent-1", "send the report", now)
    k2 = compute_idempotency_key("agent-1", "send the report", now)
    assert k1 == k2
    assert len(k1) == 64  # sha256 hex


def test_idempotency_key_normalises_whitespace():
    now = datetime(2026, 1, 15, 10, 0, 0, tzinfo=timezone.utc)
    k1 = compute_idempotency_key("agent-1", "  send the report  ", now)
    k2 = compute_idempotency_key("agent-1", "send the report", now)
    assert k1 == k2


def test_idempotency_key_differs_for_different_inputs():
    now = datetime(2026, 1, 15, 10, 0, 0, tzinfo=timezone.utc)
    k1 = compute_idempotency_key("agent-1", "send the report", now)
    k2 = compute_idempotency_key("agent-2", "send the report", now)
    k3 = compute_idempotency_key("agent-1", "forward the contract", now)
    assert k1 != k2
    assert k1 != k3
