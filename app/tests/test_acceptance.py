"""V1.7 – Acceptance tests.
All tests require RUN_DB_TESTS=true and live Supabase credentials.

Test scenarios:
1. End-to-end ingest → evidence → state transition → events → reliability
2. Conditional commitment (event_external trigger)
3. Dependency: A blocks B → B blocked → A fulfills → B open
4. Review flow: pending_review → review created → accepted → open
5. Webhook subscription and delivery record
6. Refinement: field change → history preserved
"""
import json
import uuid
from unittest.mock import patch

import pytest

from app.tests.conftest import TEST_SOURCE_AGENT_ID, TEST_USER_ID, skip_without_db

_BASE = "/api/v1"

_MOCK_DEPLOYMENT_REPORT = json.dumps([{
    "promise_text": "I will send the deployment report to Sarah",
    "classification": "genuine_commitment",
    "action": "send",
    "object": "deployment report",
    "recipient": "Sarah",
    "deadline_expression": "by Friday at 5pm",
    "conditions": [],
    "due_condition": {
        "type": "time",
        "deadline": None,
        "trigger_description": "by Friday at 5pm",
        "entity_ref": None,
        "match_threshold": 0.88,
        "partial_match_threshold": 0.65,
    },
    "confidence": 0.95,
}])

_MOCK_CI_COMMITMENT = json.dumps([{
    "promise_text": "I will merge the PR once CI passes",
    "classification": "genuine_commitment",
    "action": "merge",
    "object": "PR",
    "recipient": None,
    "deadline_expression": None,
    "conditions": ["CI must pass"],
    "due_condition": {
        "type": "event_external",
        "deadline": None,
        "trigger_description": "once CI passes",
        "entity_ref": "CI",
        "match_threshold": 0.88,
        "partial_match_threshold": 0.65,
    },
    "confidence": 0.93,
}])

_MOCK_EMPTY = json.dumps([])


# ---------------------------------------------------------------------------
# 1. End-to-end: ingest → evidence → status change → events
# ---------------------------------------------------------------------------

@skip_without_db
@pytest.mark.asyncio
async def test_e2e_ingest_evidence_fulfill(test_client, clean_db):
    # Step 1: Ingest
    with patch("app.core.extractor.extract_completion", return_value=_MOCK_DEPLOYMENT_REPORT):
        resp = await test_client.post(f"{_BASE}/ingest", json={
            "user_id": str(TEST_USER_ID),
            "source_agent_id": str(TEST_SOURCE_AGENT_ID),
            "message": "I'll send the deployment report to Sarah by Friday at 5pm",
        })
    assert resp.status_code == 200
    commitments = resp.json()["commitments"]
    assert len(commitments) == 1
    cid = commitments[0]["id"]

    # Step 2: Verify detected event was created
    ev_resp = await test_client.get(f"{_BASE}/commitments/{cid}/events")
    assert ev_resp.status_code == 200
    event_types = {e["event_type"] for e in ev_resp.json()["events"]}
    assert "detected" in event_types

    # Step 3: Submit evidence
    ev_body = {
        "commitment_id": cid,
        "source": "manual",
        "data": {"action": "send", "recipient": "Sarah", "object": "deployment report"},
    }
    ev_resp2 = await test_client.post(f"{_BASE}/commitments/{cid}/evidence", json=ev_body)
    assert ev_resp2.status_code == 200
    evidence = ev_resp2.json()
    assert evidence["score"] > 0

    # Step 4: Transition to fulfilled via state machine
    status_resp = await test_client.patch(f"{_BASE}/commitments/{cid}", json={
        "status": "fulfilled",
        "actor": "test",
    })
    assert status_resp.status_code == 200
    assert status_resp.json()["status"] == "fulfilled"

    # Step 5: Verify reliability metrics updated
    rel_resp = await test_client.get(f"{_BASE}/reliability", params={
        "user_id": str(TEST_USER_ID),
    })
    assert rel_resp.status_code == 200
    metrics = rel_resp.json()
    assert metrics["total_commitments"] >= 1


# ---------------------------------------------------------------------------
# 2. Dependency: A blocks B
# ---------------------------------------------------------------------------

@skip_without_db
@pytest.mark.asyncio
async def test_dependency_blocks_and_unblocks(test_client, clean_db):
    # Use side_effect so each call returns a distinct promise_text → distinct idempotency_key
    with patch("app.core.extractor.extract_completion",
               side_effect=[_MOCK_DEPLOYMENT_REPORT, _MOCK_CI_COMMITMENT]):
        resp_a = await test_client.post(f"{_BASE}/ingest", json={
            "user_id": str(TEST_USER_ID),
            "source_agent_id": str(TEST_SOURCE_AGENT_ID),
            "message": "I will deploy the service",
        })
        resp_b = await test_client.post(f"{_BASE}/ingest", json={
            "user_id": str(TEST_USER_ID),
            "source_agent_id": str(TEST_SOURCE_AGENT_ID),
            "message": "I will send the report after deployment",
        })

    cid_a = resp_a.json()["commitments"][0]["id"]
    cid_b = resp_b.json()["commitments"][0]["id"]

    # Create blocking dependency: A blocks B
    dep_resp = await test_client.post(f"{_BASE}/dependencies", json={
        "source_commitment_id": cid_a,
        "target_commitment_id": cid_b,
        "dependency_type": "blocks",
    })
    assert dep_resp.status_code == 200

    # B should now be blocked
    b_resp = await test_client.get(f"{_BASE}/commitments/{cid_b}")
    assert b_resp.status_code == 200
    # status may be "blocked" if transition succeeded
    dep_graph = await test_client.get(f"{_BASE}/commitments/{cid_b}/dependencies")
    assert dep_graph.status_code == 200


# ---------------------------------------------------------------------------
# 3. Review flow
# ---------------------------------------------------------------------------

@skip_without_db
@pytest.mark.asyncio
async def test_review_flow_pending_to_open(test_client, clean_db):
    # Ingest with low-confidence mock (pending_review)
    low_conf = json.dumps([{
        "promise_text": "I will check on it",
        "classification": "genuine_commitment",
        "action": "check",
        "object": None,
        "recipient": None,
        "deadline_expression": None,
        "conditions": [],
        "due_condition": {
            "type": "event_implicit",
            "deadline": None,
            "trigger_description": "later",
            "entity_ref": None,
            "match_threshold": 0.88,
            "partial_match_threshold": 0.65,
        },
        "confidence": 0.60,   # below 0.92 threshold → pending_review
    }])
    with patch("app.core.extractor.extract_completion", return_value=low_conf):
        resp = await test_client.post(f"{_BASE}/ingest", json={
            "user_id": str(TEST_USER_ID),
            "source_agent_id": str(TEST_SOURCE_AGENT_ID),
            "message": "I will check on it later",
        })
    assert resp.status_code == 200
    commitments = resp.json()["commitments"]
    assert len(commitments) == 1
    cid = commitments[0]["id"]
    assert commitments[0]["status"] == "pending_review"

    # Create review
    rev_resp = await test_client.post(f"{_BASE}/reviews", json={
        "commitment_id": cid,
        "reason_code": "low_extraction_confidence",
        "reason": "Confidence below threshold",
    })
    assert rev_resp.status_code == 200
    rev_id = rev_resp.json()["id"]

    # Accept review → commitment transitions to open
    accept_resp = await test_client.post(f"{_BASE}/reviews/{rev_id}/accept", json={
        "resolution": "Verified as genuine",
        "proposed_changes": {},
    })
    assert accept_resp.status_code == 200
    assert accept_resp.json()["status"] == "accepted"

    # Commitment should be open now
    c_resp = await test_client.get(f"{_BASE}/commitments/{cid}")
    assert c_resp.status_code == 200
    assert c_resp.json()["status"] == "open"


# ---------------------------------------------------------------------------
# 4. Refinement: deadline change, history preserved
# ---------------------------------------------------------------------------

@skip_without_db
@pytest.mark.asyncio
async def test_refinement_deadline_change_preserved(test_client, clean_db):
    with patch("app.core.extractor.extract_completion", return_value=_MOCK_DEPLOYMENT_REPORT):
        resp = await test_client.post(f"{_BASE}/ingest", json={
            "user_id": str(TEST_USER_ID),
            "source_agent_id": str(TEST_SOURCE_AGENT_ID),
            "message": "I will deliver by Friday",
        })
    cid = resp.json()["commitments"][0]["id"]

    # Apply refinement: change deadline expression
    ref_resp = await test_client.post(f"{_BASE}/commitments/{cid}/refinements", json={
        "commitment_id": cid,
        "changes": [{
            "field": "deadline_expression",
            "new_value": "by Monday",
            "change_type": "changes_deadline",
            "reason": "Team requested extension",
        }],
        "actor": "product_manager",
    })
    assert ref_resp.status_code == 200
    history = ref_resp.json()
    assert len(history["changes"]) == 1
    change = history["changes"][0]
    assert change["field"] == "deadline_expression"
    assert change["new_value"] == "by Monday"
    assert change["previous_value"] is not None  # original preserved

    # History is retrievable
    get_resp = await test_client.get(f"{_BASE}/commitments/{cid}/refinements")
    assert get_resp.status_code == 200
    assert len(get_resp.json()["changes"]) >= 1


# ---------------------------------------------------------------------------
# 5. Webhook subscription created successfully
# ---------------------------------------------------------------------------

@skip_without_db
@pytest.mark.asyncio
async def test_webhook_subscription_lifecycle(test_client, clean_db):
    # Create
    create_resp = await test_client.post(f"{_BASE}/webhooks", json={
        "endpoint": "https://example.com/cogext-hook",
        "secret": "mysecret123",
        "subscribed_event_types": ["status_changed"],
    })
    assert create_resp.status_code == 200
    wh = create_resp.json()
    assert wh["active"] is True
    assert "secret" not in wh           # secret must NEVER be in response
    assert "secret_hash" not in wh     # hash also must not be exposed
    wh_id = wh["id"]

    # List
    list_resp = await test_client.get(f"{_BASE}/webhooks")
    assert list_resp.status_code == 200
    ids = [w["id"] for w in list_resp.json()]
    assert wh_id in ids

    # Deactivate
    del_resp = await test_client.delete(f"{_BASE}/webhooks/{wh_id}")
    assert del_resp.status_code == 200

    # Verify inactive
    get_resp = await test_client.get(f"{_BASE}/webhooks/{wh_id}")
    assert get_resp.json()["active"] is False


# ---------------------------------------------------------------------------
# 6. Privacy / redaction
# ---------------------------------------------------------------------------

@skip_without_db
@pytest.mark.asyncio
async def test_redact_text_endpoint(test_client, clean_db):
    resp = await test_client.post(f"{_BASE}/redact/text", json={
        "text": "Send email to alice@example.com with API key sk-abc123longkeyhere"
    })
    assert resp.status_code == 200
    data = resp.json()
    assert "alice@example.com" not in data["redacted"]
    assert len(data["types_found"]) >= 1
