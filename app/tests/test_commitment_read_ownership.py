"""P0-3 slice 3 – ownership on commitment read routes (no DB, no network).

Covers GET /commitments/{id} and its nested reads: events, evidence, evidence
aggregate, dependencies and refinements. Reuses the in-memory fake from the
write-route tests, seeded with nested rows for both accounts. Every test checks
the response body as well as the status, and that reads never write.
"""
import json
import uuid

import pytest

import app.api.dependencies as dependencies_api
import app.api.events as events_api
import app.api.evidence as evidence_api
import app.api.refinements as refinements_api
import app.api.status as status_api
import app.core.dependency as dependency_core
from app.core.auth import Account, get_current_account
from app.main import app
from app.tests.test_commitment_ownership import (
    A_BLOCKED,
    A_DEP,
    A_EMAIL_TEXT,
    A_OPEN,
    ACCOUNT_A,
    ACCOUNT_B,
    B_OPEN,
    FakeDB,
)

A_EVENT = "e1000000-0000-4000-8000-0000000000a1"
B_EVENT = "e2000000-0000-4000-8000-0000000000b1"
A_EVIDENCE = "f1000000-0000-4000-8000-0000000000a1"
B_EVIDENCE = "f2000000-0000-4000-8000-0000000000b1"
A_CHANGE = "d1000000-0000-4000-8000-0000000000a1"
A_SECRET = "A-only-evidence-marker"


def _event(eid, cid):
    return {"id": eid, "commitment_id": cid, "event_type": "detected", "actor": "ingest_api",
            "data": {}, "occurred_at": "2026-10-01T00:00:00+00:00",
            "recorded_at": "2026-10-01T00:00:00+00:00"}


def _evidence(eid, cid, marker):
    return {"id": eid, "commitment_id": cid, "source": "test", "data": {"note": marker},
            "occurred_at": "2026-10-01T00:00:00+00:00", "recorded_at": "2026-10-01T00:00:00+00:00",
            "strength": "strong", "score": 0.9, "match_details": [], "verified": False}


@pytest.fixture
def db(monkeypatch):
    fake = FakeDB()
    fake.tables["commitment_events"] = [_event(A_EVENT, A_OPEN), _event(B_EVENT, B_OPEN)]
    fake.tables["evidence"] = [
        _evidence(A_EVIDENCE, A_OPEN, A_SECRET),
        _evidence(B_EVIDENCE, B_OPEN, "B-only-evidence-marker"),
    ]
    fake.tables["commitment_changes"] = [{
        "id": A_CHANGE, "commitment_id": A_OPEN, "field": "deadline_expression",
        "previous_value": "by Friday", "new_value": "by Monday", "change_type": "changes_deadline",
        "actor": "tester", "timestamp": "2026-10-02T00:00:00+00:00", "reason": None,
    }]
    for module in (status_api, events_api, evidence_api, dependencies_api, dependency_core, refinements_api):
        monkeypatch.setattr(module, "get_supabase", lambda: fake)
    return fake


@pytest.fixture
def as_account():
    def _use(account_id):
        app.dependency_overrides[get_current_account] = lambda: Account(
            account_id=account_id, email=f"{account_id[:8]}@example.test", key_id=account_id
        )
    yield _use
    app.dependency_overrides.pop(get_current_account, None)


NOT_FOUND = {"detail": "Commitment not found"}

READ_PATHS = [
    "/api/v1/commitments/{id}",
    "/api/v1/commitments/{id}/events",
    "/api/v1/commitments/{id}/evidence",
    "/api/v1/commitments/{id}/evidence/aggregate",
    "/api/v1/commitments/{id}/dependencies",
    "/api/v1/commitments/{id}/refinements",
]

# Values that only A's records contain; none may appear in a response to B.
A_ONLY = [A_EMAIL_TEXT, A_EVENT, A_EVIDENCE, A_SECRET, A_CHANGE, A_DEP, ACCOUNT_A]


# ---------------------------------------------------------------------------
# Same-account reads
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_owner_reads_commitment(test_client, db, as_account):
    as_account(ACCOUNT_A)
    resp = await test_client.get(f"/api/v1/commitments/{A_OPEN}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert (body["id"], body["user_id"], body["promise_text"]) == (A_OPEN, ACCOUNT_A, A_EMAIL_TEXT)


@pytest.mark.asyncio
async def test_owner_reads_events(test_client, db, as_account):
    as_account(ACCOUNT_A)
    resp = await test_client.get(f"/api/v1/commitments/{A_OPEN}/events")
    assert resp.status_code == 200, resp.text
    assert [e["id"] for e in resp.json()["events"]] == [A_EVENT]
    assert resp.json()["total"] == 1


@pytest.mark.asyncio
async def test_owner_reads_evidence_and_aggregate(test_client, db, as_account):
    as_account(ACCOUNT_A)
    listed = await test_client.get(f"/api/v1/commitments/{A_OPEN}/evidence")
    assert listed.status_code == 200, listed.text
    assert [e["id"] for e in listed.json()] == [A_EVIDENCE]

    agg = await test_client.get(f"/api/v1/commitments/{A_OPEN}/evidence/aggregate")
    assert agg.status_code == 200, agg.text
    assert (agg.json()["commitment_id"], agg.json()["total_records"]) == (A_OPEN, 1)
    assert agg.json()["evidence_ids"] == [A_EVIDENCE]


@pytest.mark.asyncio
async def test_owner_reads_dependencies(test_client, db, as_account):
    as_account(ACCOUNT_A)
    blocker = await test_client.get(f"/api/v1/commitments/{A_OPEN}/dependencies")
    blocked = await test_client.get(f"/api/v1/commitments/{A_BLOCKED}/dependencies")
    assert blocker.status_code == blocked.status_code == 200
    assert [d["id"] for d in blocker.json()["blocking"]] == [A_DEP]
    assert [d["id"] for d in blocked.json()["blockers"]] == [A_DEP]


@pytest.mark.asyncio
async def test_owner_reads_refinements(test_client, db, as_account):
    as_account(ACCOUNT_A)
    resp = await test_client.get(f"/api/v1/commitments/{A_OPEN}/refinements")
    assert resp.status_code == 200, resp.text
    assert [(c["id"], c["new_value"]) for c in resp.json()["changes"]] == [(A_CHANGE, "by Monday")]


@pytest.mark.asyncio
@pytest.mark.parametrize("path", READ_PATHS)
async def test_b_reads_its_own_records(test_client, db, as_account, path):
    as_account(ACCOUNT_B)
    resp = await test_client.get(path.format(id=B_OPEN))
    assert resp.status_code == 200, resp.text
    assert not any(v in resp.text for v in A_ONLY)


# ---------------------------------------------------------------------------
# Cross-account and missing resources
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("path", READ_PATHS)
async def test_other_account_cannot_read(test_client, db, as_account, path):
    as_account(ACCOUNT_B)
    before = db.snapshot()
    resp = await test_client.get(path.format(id=A_OPEN))
    assert (resp.status_code, resp.json()) == (404, NOT_FOUND)
    assert not any(v in resp.text for v in A_ONLY)
    assert db.snapshot() == before and db.writes == []


@pytest.mark.asyncio
@pytest.mark.parametrize("path", READ_PATHS)
async def test_foreign_and_missing_look_the_same(test_client, db, as_account, path):
    as_account(ACCOUNT_B)
    foreign = await test_client.get(path.format(id=A_OPEN))
    missing = await test_client.get(path.format(id=uuid.uuid4()))
    assert (foreign.status_code, foreign.json()) == (missing.status_code, missing.json()) == (404, NOT_FOUND)


@pytest.mark.asyncio
@pytest.mark.parametrize("path", READ_PATHS)
async def test_owner_gets_404_for_missing_commitment(test_client, db, as_account, path):
    as_account(ACCOUNT_A)
    resp = await test_client.get(path.format(id=uuid.uuid4()))
    assert (resp.status_code, resp.json()) == (404, NOT_FOUND)


# ---------------------------------------------------------------------------
# Authentication, and reads never write
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("path", READ_PATHS)
async def test_missing_authentication_is_rejected(test_client, db, path):
    app.dependency_overrides.pop(get_current_account, None)
    before = db.snapshot()
    resp = await test_client.get(path.format(id=A_OPEN))
    assert resp.status_code == 401
    assert not any(v in resp.text for v in A_ONLY)
    assert db.snapshot() == before and db.writes == []


@pytest.mark.asyncio
async def test_successful_reads_do_not_write(test_client, db, as_account):
    as_account(ACCOUNT_A)
    before = json.dumps(db.snapshot(), sort_keys=True)
    for path in READ_PATHS:
        assert (await test_client.get(path.format(id=A_OPEN))).status_code == 200
    assert json.dumps(db.snapshot(), sort_keys=True) == before
    assert db.writes == []
