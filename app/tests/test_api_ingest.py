"""Integration tests for POST /api/v1/ingest — requires RUN_DB_TESTS=true."""
import json
from unittest.mock import patch

import pytest

from app.tests.conftest import TEST_SOURCE_AGENT_ID, TEST_USER_ID, skip_without_db

_INGEST_URL = "/api/v1/ingest"

_VALID_BODY = {
    "user_id": str(TEST_USER_ID),
    "source_agent_id": str(TEST_SOURCE_AGENT_ID),
    "message": "I will send the quarterly report by Tuesday end of day.",
}

_MOCK_EXTRACTED = json.dumps([
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
    }
])


@skip_without_db
@pytest.mark.asyncio
async def test_ingest_returns_200_with_commitments(test_client, clean_db):
    with patch("app.core.extractor.extract_completion", return_value=_MOCK_EXTRACTED):
        resp = await test_client.post(_INGEST_URL, json=_VALID_BODY)
    assert resp.status_code == 200
    data = resp.json()
    assert "commitments" in data
    assert len(data["commitments"]) == 1
    c = data["commitments"][0]
    assert c["promise_text"] == "I will send the quarterly report"
    assert c["status"] in ("open", "pending_review")
    assert 0.0 <= c["confidence"] <= 1.0


@skip_without_db
@pytest.mark.asyncio
async def test_ingest_is_idempotent(test_client, clean_db):
    with patch("app.core.extractor.extract_completion", return_value=_MOCK_EXTRACTED):
        resp1 = await test_client.post(_INGEST_URL, json=_VALID_BODY)
        resp2 = await test_client.post(_INGEST_URL, json=_VALID_BODY)
    assert resp1.status_code == 200
    assert resp2.status_code == 200
    ids1 = {c["idempotency_key"] for c in resp1.json()["commitments"]}
    ids2 = {c["idempotency_key"] for c in resp2.json()["commitments"]}
    # same idempotency keys — second call hit ON CONFLICT DO NOTHING
    assert ids1 == ids2


@pytest.mark.asyncio
async def test_ingest_rejects_invalid_uuid(test_client):
    body = {**_VALID_BODY, "user_id": "not-a-uuid"}
    resp = await test_client.post(_INGEST_URL, json=body)
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_ingest_rejects_malformed_json(test_client):
    resp = await test_client.post(
        _INGEST_URL,
        content=b"{bad json",
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_ingest_rejects_missing_message(test_client):
    body = {"user_id": str(TEST_USER_ID), "source_agent_id": str(TEST_SOURCE_AGENT_ID)}
    resp = await test_client.post(_INGEST_URL, json=body)
    assert resp.status_code == 422
