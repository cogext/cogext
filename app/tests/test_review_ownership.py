"""P0-3 slice 4 – review ownership (no DB, no network).

Authorization rule under test: a review is visible to, and changeable by, only
the account that owns its commitment (human_reviews.commitment_id ->
commitments.user_id). reviewer_id is a free-form label with no link to an
account, so it grants nothing. Another account's review gets the same 404 as a
missing one.

The Slack action route (GET /reviews/{id}/action) is deliberately unchanged in
this slice; its test pins the current fail-closed behaviour (Bearer key
required) so it cannot silently become public.
"""
import copy
import uuid

import pytest

import app.api.reviews as reviews_api
import app.core.state_machine as state_machine
from app.core.auth import Account, get_current_account
from app.main import app
from app.tests.test_commitment_ownership import (
    A_OPEN,
    A_PENDING,
    ACCOUNT_A,
    ACCOUNT_B,
    B_OPEN,
    FakeDB,
    _commitment,
)

A_DETECTED = "a8000000-0000-4000-8000-000000000008"
B_PENDING = "b2000000-0000-4000-8000-000000000002"
A_REVIEW = "a6000000-0000-4000-8000-000000000006"
B_REVIEW = "b6000000-0000-4000-8000-000000000006"
A_REASON = "A-only review reason marker"
B_REASON = "B-only review reason marker"
REVIEWER = "99999999-0000-4000-8000-000000000099"


def _review(rid, cid, reason, status="pending"):
    return {
        "id": rid, "commitment_id": cid, "reviewer_id": None, "status": status,
        "reason_code": "manual_request", "reason": reason, "proposed_changes": {},
        "created_at": "2026-10-01T00:00:00+00:00", "assigned_at": None,
        "resolved_at": None, "resolution": None, "metadata": {},
    }


@pytest.fixture
def db(monkeypatch):
    fake = FakeDB()
    fake.tables["commitments"] += [
        _commitment(A_DETECTED, ACCOUNT_A, "detected"),
        _commitment(B_PENDING, ACCOUNT_B, "pending_review"),
    ]
    fake.tables["human_reviews"] = [
        _review(A_REVIEW, A_PENDING, A_REASON),
        _review(B_REVIEW, B_PENDING, B_REASON),
    ]
    for module in (reviews_api, state_machine):
        monkeypatch.setattr(module, "get_supabase", lambda: fake)
    return fake


@pytest.fixture
def alerts(monkeypatch):
    """Records Slack kill-switch alerts instead of sending them."""
    sent = []

    async def _record(**kwargs):
        sent.append(kwargs)

    monkeypatch.setattr(reviews_api, "send_kill_switch_alert", _record)
    return sent


@pytest.fixture
def as_account():
    def _use(account_id):
        app.dependency_overrides[get_current_account] = lambda: Account(
            account_id=account_id, email=f"{account_id[:8]}@example.test", key_id=account_id
        )
    yield _use
    app.dependency_overrides.pop(get_current_account, None)


REVIEW_NOT_FOUND = {"detail": "Review not found"}
COMMITMENT_NOT_FOUND = {"detail": "Commitment not found"}
A_ONLY = [A_REVIEW, A_REASON, A_PENDING]

ACTIONS = [
    ("assign", {"reviewer_id": REVIEWER}),
    ("accept", {"resolution": "ok"}),
    ("reject", {"resolution": "no"}),
    ("edit", {"proposed_changes": {"recipient": "x"}, "resolution": "edited"}),
]


def _ids(resp):
    return [r["id"] for r in resp.json()]


# ---------------------------------------------------------------------------
# Listing and reading
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_returns_only_own_reviews(test_client, db, as_account, alerts):
    as_account(ACCOUNT_A)
    resp = await test_client.get("/api/v1/reviews")
    assert resp.status_code == 200, resp.text
    assert _ids(resp) == [A_REVIEW]
    assert B_REASON not in resp.text

    as_account(ACCOUNT_B)
    resp = await test_client.get("/api/v1/reviews")
    assert _ids(resp) == [B_REVIEW]
    assert not any(v in resp.text for v in A_ONLY)


@pytest.mark.asyncio
async def test_list_filters_still_work_for_owner(test_client, db, as_account, alerts):
    as_account(ACCOUNT_A)
    by_commitment = await test_client.get("/api/v1/reviews", params={"commitment_id": A_PENDING})
    by_status = await test_client.get("/api/v1/reviews", params={"status": "pending"})
    none_match = await test_client.get("/api/v1/reviews", params={"status": "accepted"})
    assert _ids(by_commitment) == _ids(by_status) == [A_REVIEW]
    assert _ids(none_match) == []


@pytest.mark.asyncio
async def test_list_filtered_by_foreign_commitment_is_empty_like_missing(test_client, db, as_account, alerts):
    as_account(ACCOUNT_B)
    foreign = await test_client.get("/api/v1/reviews", params={"commitment_id": A_PENDING})
    missing = await test_client.get("/api/v1/reviews", params={"commitment_id": str(uuid.uuid4())})
    assert (foreign.status_code, foreign.json()) == (missing.status_code, missing.json()) == (200, [])


@pytest.mark.asyncio
async def test_list_for_account_without_commitments_is_empty(test_client, db, as_account, alerts):
    as_account(str(uuid.uuid4()))
    resp = await test_client.get("/api/v1/reviews")
    assert (resp.status_code, resp.json()) == (200, [])


@pytest.mark.asyncio
async def test_owner_reads_review(test_client, db, as_account, alerts):
    as_account(ACCOUNT_A)
    resp = await test_client.get(f"/api/v1/reviews/{A_REVIEW}")
    assert resp.status_code == 200, resp.text
    assert (resp.json()["id"], resp.json()["reason"]) == (A_REVIEW, A_REASON)


@pytest.mark.asyncio
async def test_other_account_cannot_read_review(test_client, db, as_account, alerts):
    as_account(ACCOUNT_B)
    foreign = await test_client.get(f"/api/v1/reviews/{A_REVIEW}")
    missing = await test_client.get(f"/api/v1/reviews/{uuid.uuid4()}")
    assert (foreign.status_code, foreign.json()) == (missing.status_code, missing.json()) == (404, REVIEW_NOT_FOUND)
    assert not any(v in foreign.text for v in A_ONLY)


# ---------------------------------------------------------------------------
# Creating
# ---------------------------------------------------------------------------

def _create(cid):
    return {"commitment_id": cid, "reason_code": "manual_request", "reason": "please check"}


@pytest.mark.asyncio
async def test_owner_creates_review(test_client, db, as_account, alerts):
    as_account(ACCOUNT_A)
    resp = await test_client.post("/api/v1/reviews", json=_create(A_OPEN))
    assert resp.status_code == 200, resp.text
    created = resp.json()
    assert (created["commitment_id"], created["status"]) == (A_OPEN, "pending")
    assert len(db.tables["human_reviews"]) == 3
    assert [e["event_type"] for e in db.tables["commitment_events"]] == ["review_created"]
    assert [a["commitment_id"] for a in alerts] == [A_OPEN]
    assert db.status(A_OPEN) == "open"     # only detected -> pending_review moves


@pytest.mark.asyncio
async def test_owner_create_moves_detected_to_pending_review(test_client, db, as_account, alerts):
    as_account(ACCOUNT_A)
    resp = await test_client.post("/api/v1/reviews", json=_create(A_DETECTED))
    assert resp.status_code == 200, resp.text
    assert db.status(A_DETECTED) == "pending_review"


@pytest.mark.asyncio
async def test_other_account_cannot_create_review(test_client, db, as_account, alerts):
    as_account(ACCOUNT_B)
    before = db.snapshot()
    resp = await test_client.post("/api/v1/reviews", json=_create(A_DETECTED))
    assert (resp.status_code, resp.json()) == (404, COMMITMENT_NOT_FOUND)
    assert db.snapshot() == before and db.writes == []
    assert alerts == []     # A's promise text is not sent to Slack either


@pytest.mark.asyncio
async def test_create_review_for_missing_commitment_is_404(test_client, db, as_account, alerts):
    as_account(ACCOUNT_A)
    resp = await test_client.post("/api/v1/reviews", json=_create(str(uuid.uuid4())))
    assert (resp.status_code, resp.json()) == (404, COMMITMENT_NOT_FOUND)
    assert db.writes == [] and alerts == []


# ---------------------------------------------------------------------------
# Assign / accept / reject / edit
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_owner_assigns_review(test_client, db, as_account, alerts):
    as_account(ACCOUNT_A)
    resp = await test_client.post(f"/api/v1/reviews/{A_REVIEW}/assign", json={"reviewer_id": REVIEWER})
    assert resp.status_code == 200, resp.text
    row = db.row("human_reviews", A_REVIEW)
    assert (row["status"], row["reviewer_id"]) == ("assigned", REVIEWER)


@pytest.mark.asyncio
async def test_owner_accepts_review_and_commitment_opens(test_client, db, as_account, alerts):
    as_account(ACCOUNT_A)
    resp = await test_client.post(f"/api/v1/reviews/{A_REVIEW}/accept", json={"resolution": "ok"})
    assert resp.status_code == 200, resp.text
    assert db.row("human_reviews", A_REVIEW)["status"] == "accepted"
    assert db.status(A_PENDING) == "open"


@pytest.mark.asyncio
async def test_owner_rejects_review(test_client, db, as_account, alerts):
    as_account(ACCOUNT_A)
    resp = await test_client.post(f"/api/v1/reviews/{A_REVIEW}/reject", json={"resolution": "no"})
    assert resp.status_code == 200, resp.text
    assert db.row("human_reviews", A_REVIEW)["status"] == "rejected"
    assert db.status(A_PENDING) == "pending_review"


@pytest.mark.asyncio
async def test_owner_edits_review(test_client, db, as_account, alerts):
    as_account(ACCOUNT_A)
    resp = await test_client.post(
        f"/api/v1/reviews/{A_REVIEW}/edit", json={"proposed_changes": {"recipient": "x"}, "resolution": "e"}
    )
    assert resp.status_code == 200, resp.text
    row = db.row("human_reviews", A_REVIEW)
    assert (row["status"], row["proposed_changes"]) == ("edited", {"recipient": "x"})


@pytest.mark.asyncio
async def test_owner_assign_of_non_pending_still_409(test_client, db, as_account, alerts):
    as_account(ACCOUNT_A)
    db.row("human_reviews", A_REVIEW)["status"] = "accepted"
    before = db.snapshot()
    resp = await test_client.post(f"/api/v1/reviews/{A_REVIEW}/assign", json={"reviewer_id": REVIEWER})
    assert resp.status_code == 409
    assert db.snapshot() == before


@pytest.mark.asyncio
@pytest.mark.parametrize("action,body", ACTIONS)
async def test_other_account_cannot_change_review(test_client, db, as_account, alerts, action, body):
    as_account(ACCOUNT_B)
    before = db.snapshot()
    resp = await test_client.post(f"/api/v1/reviews/{A_REVIEW}/{action}", json=body)
    assert (resp.status_code, resp.json()) == (404, REVIEW_NOT_FOUND)
    assert not any(v in resp.text for v in A_ONLY)
    assert db.snapshot() == before and db.writes == []
    assert db.status(A_PENDING) == "pending_review"


@pytest.mark.asyncio
@pytest.mark.parametrize("action,body", ACTIONS)
async def test_foreign_and_missing_review_look_the_same(test_client, db, as_account, alerts, action, body):
    as_account(ACCOUNT_B)
    foreign = await test_client.post(f"/api/v1/reviews/{A_REVIEW}/{action}", json=body)
    missing = await test_client.post(f"/api/v1/reviews/{uuid.uuid4()}/{action}", json=body)
    assert (foreign.status_code, foreign.json()) == (missing.status_code, missing.json()) == (404, REVIEW_NOT_FOUND)


@pytest.mark.asyncio
async def test_assigned_reviewer_id_grants_no_access(test_client, db, as_account, alerts):
    """Assigning B's account id as reviewer does not let B act on A's review."""
    as_account(ACCOUNT_A)
    assert (await test_client.post(
        f"/api/v1/reviews/{A_REVIEW}/assign", json={"reviewer_id": ACCOUNT_B}
    )).status_code == 200

    as_account(ACCOUNT_B)
    before = db.snapshot()
    read = await test_client.get(f"/api/v1/reviews/{A_REVIEW}")
    accept = await test_client.post(f"/api/v1/reviews/{A_REVIEW}/accept", json={"resolution": "ok"})
    listed = await test_client.get("/api/v1/reviews")
    assert read.status_code == accept.status_code == 404
    assert A_REVIEW not in listed.text
    assert db.snapshot() == before


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("method,path,body", [
    ("GET", "/api/v1/reviews", None),
    ("GET", f"/api/v1/reviews/{A_REVIEW}", None),
    ("POST", "/api/v1/reviews", _create(A_OPEN)),
] + [("POST", f"/api/v1/reviews/{A_REVIEW}/{a}", b) for a, b in ACTIONS])
async def test_missing_authentication_is_rejected(test_client, db, alerts, method, path, body):
    app.dependency_overrides.pop(get_current_account, None)
    before = db.snapshot()
    resp = await test_client.request(method, path, json=body)
    assert resp.status_code == 401
    assert not any(v in resp.text for v in A_ONLY)
    assert db.snapshot() == before and db.writes == [] and alerts == []


@pytest.mark.asyncio
async def test_slack_action_route_still_requires_a_bearer_key(test_client, db, alerts):
    """Unchanged in this slice: making it public needs a product decision and a
    confirmed ACTION_TOKEN_SECRET. Until then it must stay fail-closed."""
    app.dependency_overrides.pop(get_current_account, None)
    before = db.snapshot()
    resp = await test_client.get(
        f"/api/v1/reviews/{A_REVIEW}/action", params={"action": "approve", "token": "0.forged"}
    )
    assert resp.status_code == 401
    assert db.snapshot() == before and db.writes == []
