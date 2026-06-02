"""Integration tests for PATCH /api/v1/commitments/{id} — requires RUN_DB_TESTS=true."""
import json
import uuid
from unittest.mock import patch

import pytest

from app.tests.conftest import TEST_SOURCE_AGENT_ID, TEST_USER_ID, skip_without_db

_INGEST_URL = "/api/v1/ingest"

_MOCK_ONE = json.dumps([
    {
        "promise_text": "I will deliver the slides",
        "due_condition": {
            "type": "time", "deadline": None,
            "trigger_description": "by Monday",
            "entity_ref": None,
            "match_threshold": 0.88, "partial_match_threshold": 0.65,
        },
        "confidence": 0.95,
    }
])


async def _seed_one(client) -> dict:
    body = {
        "user_id": str(TEST_USER_ID),
        "source_agent_id": str(TEST_SOURCE_AGENT_ID),
        "message": "I will deliver the slides by Monday.",
    }
    with patch("app.core.extractor.extract_completion", return_value=_MOCK_ONE):
        resp = await client.post(_INGEST_URL, json=body)
    assert resp.status_code == 200
    commitments = resp.json()["commitments"]
    assert len(commitments) == 1
    return commitments[0]


@skip_without_db
@pytest.mark.asyncio
async def test_patch_open_to_fulfilled(test_client, clean_db):
    c = await _seed_one(test_client)
    resp = await test_client.patch(
        f"/api/v1/commitments/{c['id']}",
        json={"status": "fulfilled"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "fulfilled"


@skip_without_db
@pytest.mark.asyncio
async def test_patch_sets_resolved_at(test_client, clean_db):
    c = await _seed_one(test_client)
    # fulfilled should set resolved_at; open should clear it
    resp = await test_client.patch(
        f"/api/v1/commitments/{c['id']}",
        json={"status": "fulfilled"},
    )
    assert resp.status_code == 200
    # resolved_at not in our Commitment model response but we can verify via DB
    from app.db.connection import get_supabase
    row = await get_supabase().table("commitments").select("resolved_at").eq("id", c["id"]).execute()
    assert row.data[0]["resolved_at"] is not None


@skip_without_db
@pytest.mark.asyncio
async def test_patch_open_to_expired(test_client, clean_db):
    c = await _seed_one(test_client)
    resp = await test_client.patch(
        f"/api/v1/commitments/{c['id']}",
        json={"status": "expired"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "expired"


@skip_without_db
@pytest.mark.asyncio
async def test_patch_rejects_terminal_to_open(test_client, clean_db):
    c = await _seed_one(test_client)
    await test_client.patch(
        f"/api/v1/commitments/{c['id']}",
        json={"status": "fulfilled"},
    )
    resp = await test_client.patch(
        f"/api/v1/commitments/{c['id']}",
        json={"status": "open"},
    )
    assert resp.status_code == 409


@skip_without_db
@pytest.mark.asyncio
async def test_patch_rejects_terminal_to_terminal(test_client, clean_db):
    c = await _seed_one(test_client)
    await test_client.patch(
        f"/api/v1/commitments/{c['id']}",
        json={"status": "fulfilled"},
    )
    resp = await test_client.patch(
        f"/api/v1/commitments/{c['id']}",
        json={"status": "expired"},
    )
    assert resp.status_code == 409


@skip_without_db
@pytest.mark.asyncio
async def test_patch_returns_404_for_unknown_id(test_client, clean_db):
    resp = await test_client.patch(
        f"/api/v1/commitments/{uuid.uuid4()}",
        json={"status": "fulfilled"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_patch_rejects_invalid_status(test_client):
    resp = await test_client.patch(
        f"/api/v1/commitments/{uuid.uuid4()}",
        json={"status": "deleted"},
    )
    assert resp.status_code == 422
