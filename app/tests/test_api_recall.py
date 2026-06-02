"""Integration tests for GET /api/v1/commitments — requires RUN_DB_TESTS=true."""
import json
import uuid
from unittest.mock import patch

import pytest

from app.tests.conftest import TEST_SOURCE_AGENT_ID, TEST_USER_ID, skip_without_db

_INGEST_URL = "/api/v1/ingest"
_RECALL_URL = "/api/v1/commitments"

_MOCK_TWO = json.dumps([
    {
        "promise_text": "I will send the report",
        "due_condition": {
            "type": "time", "deadline": None,
            "trigger_description": "by Tuesday",
            "entity_ref": None,
            "match_threshold": 0.88, "partial_match_threshold": 0.65,
        },
        "confidence": 0.95,
    },
    {
        "promise_text": "I will call Sarah",
        "due_condition": {
            "type": "event_implicit", "deadline": None,
            "trigger_description": "after the meeting",
            "entity_ref": "Sarah",
            "match_threshold": 0.88, "partial_match_threshold": 0.65,
        },
        "confidence": 0.80,
    },
])


async def _seed(client, record_key=None):
    body = {
        "user_id": str(TEST_USER_ID),
        "source_agent_id": str(TEST_SOURCE_AGENT_ID),
        "message": "I will send the report by Tuesday. I will call Sarah after the meeting.",
        **({"record_key": record_key} if record_key else {}),
    }
    with patch("app.core.extractor.extract_completion", return_value=_MOCK_TWO):
        resp = await client.post(_INGEST_URL, json=body)
    assert resp.status_code == 200
    return resp.json()["commitments"]


@skip_without_db
@pytest.mark.asyncio
async def test_recall_returns_this_users_commitments(test_client, clean_db):
    await _seed(test_client)
    resp = await test_client.get(_RECALL_URL, params={"user_id": str(TEST_USER_ID)})
    assert resp.status_code == 200
    data = resp.json()["commitments"]
    assert len(data) >= 1
    assert all(c["user_id"] == str(TEST_USER_ID) for c in data)


@skip_without_db
@pytest.mark.asyncio
async def test_recall_does_not_return_other_users(test_client, clean_db):
    await _seed(test_client)
    other_user = uuid.uuid4()
    resp = await test_client.get(_RECALL_URL, params={"user_id": str(other_user)})
    assert resp.status_code == 200
    assert resp.json()["commitments"] == []


@skip_without_db
@pytest.mark.asyncio
async def test_recall_filter_by_source_agent_id(test_client, clean_db):
    await _seed(test_client)
    resp = await test_client.get(
        _RECALL_URL,
        params={"user_id": str(TEST_USER_ID), "source_agent_id": str(TEST_SOURCE_AGENT_ID)},
    )
    assert resp.status_code == 200
    data = resp.json()["commitments"]
    assert len(data) >= 1
    assert all(c["source_agent_id"] == str(TEST_SOURCE_AGENT_ID) for c in data)


@skip_without_db
@pytest.mark.asyncio
async def test_recall_filter_by_record_key(test_client, clean_db):
    await _seed(test_client, record_key="project-abc")
    resp = await test_client.get(
        _RECALL_URL,
        params={"user_id": str(TEST_USER_ID), "record_key": "project-abc"},
    )
    assert resp.status_code == 200
    data = resp.json()["commitments"]
    assert len(data) >= 1
    assert all(c["record_key"] == "project-abc" for c in data)


@skip_without_db
@pytest.mark.asyncio
async def test_recall_filter_by_status(test_client, clean_db):
    await _seed(test_client)
    # pending_review commitments should not appear in status=open query
    resp_open = await test_client.get(
        _RECALL_URL,
        params={"user_id": str(TEST_USER_ID), "status": "open"},
    )
    assert resp_open.status_code == 200
    for c in resp_open.json()["commitments"]:
        assert c["status"] == "open"


@skip_without_db
@pytest.mark.asyncio
async def test_recall_empty_for_unknown_user(test_client, clean_db):
    resp = await test_client.get(
        _RECALL_URL,
        params={"user_id": str(uuid.uuid4())},
    )
    assert resp.status_code == 200
    assert resp.json() == {"commitments": []}


@pytest.mark.asyncio
async def test_recall_requires_user_id(test_client):
    resp = await test_client.get(_RECALL_URL)
    assert resp.status_code == 422
