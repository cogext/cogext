"""P0-3 slice 1 – refinement ownership and field allow-list (no DB, no network).

Two synthetic accounts, A and B, each own commitments in an in-memory fake of
the Supabase client. Every test asserts the HTTP response *and* the stored row
state, so a check that only changes status codes cannot pass.
"""
import copy
import uuid

import pytest

import app.api.refinements as refinements_module
import app.core.state_machine as state_machine_module
from app.core.auth import Account, get_current_account
from app.main import app

ACCOUNT_A = "aaaaaaaa-0000-4000-8000-00000000000a"
ACCOUNT_B = "bbbbbbbb-0000-4000-8000-00000000000b"

A_COMMITMENT = "a1a1a1a1-0000-4000-8000-0000000000a1"
A_OTHER = "a2a2a2a2-0000-4000-8000-0000000000a2"
B_COMMITMENT = "b1b1b1b1-0000-4000-8000-0000000000b1"


def _commitment(cid: str, owner: str, label: str) -> dict:
    return {
        "id": cid,
        "user_id": owner,
        "tenant_id": None,
        "status": "open",
        "promise_text": f"{label} will deliver the report",
        "action": "deliver",
        "object": "report",
        "recipient": f"{label}-client",
        "deadline_expression": "by Friday",
        "priority": "medium",
        "receipt_token": f"receipt-{label}",
        "idempotency_key": f"idem-{label}",
        "created_at": "2026-10-01T00:00:00+00:00",
        "updated_at": "2026-10-01T00:00:00+00:00",
        "superseded_by": None,
        "shape": None,
        "verifier_query": None,
    }


# ---------------------------------------------------------------------------
# In-memory fake of the subset of the supabase-py query builder used here.
# ---------------------------------------------------------------------------

class _Resp:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, db: "_FakeDB", table: str):
        self.db, self.table = db, table
        self.op, self.payload, self.filters = "select", None, []

    def select(self, *_cols, **_kw):
        self.op = "select"
        return self

    def insert(self, payload):
        self.op, self.payload = "insert", payload
        return self

    def update(self, payload):
        self.op, self.payload = "update", payload
        return self

    def eq(self, column, value):
        self.filters.append((column, str(value)))
        return self

    def order(self, *_a, **_kw):
        return self

    def limit(self, *_a, **_kw):
        return self

    def maybe_single(self):
        return self

    def _matches(self, row):
        return all(str(row.get(c)) == v for c, v in self.filters)

    async def execute(self):
        rows = self.db.tables.setdefault(self.table, [])
        if self.op == "select":
            return _Resp([copy.deepcopy(r) for r in rows if self._matches(r)])
        self.db.writes.append((self.op, self.table, copy.deepcopy(self.payload), list(self.filters)))
        if self.op == "insert":
            rows.append(copy.deepcopy(self.payload))
            return _Resp([copy.deepcopy(self.payload)])
        hit = [r for r in rows if self._matches(r)]
        for r in hit:
            r.update(copy.deepcopy(self.payload))
        return _Resp([copy.deepcopy(r) for r in hit])


class _FakeDB:
    def __init__(self):
        self.tables = {
            "commitments": [
                _commitment(A_COMMITMENT, ACCOUNT_A, "A"),
                _commitment(A_OTHER, ACCOUNT_A, "A2"),
                _commitment(B_COMMITMENT, ACCOUNT_B, "B"),
            ],
            "commitment_changes": [],
            "commitment_events": [],
        }
        self.writes: list[tuple] = []

    def table(self, name):
        return _Query(self, name)

    def row(self, cid):
        return next(r for r in self.tables["commitments"] if r["id"] == cid)

    def snapshot(self):
        return copy.deepcopy(self.tables)


@pytest.fixture
def fake_db(monkeypatch):
    db = _FakeDB()
    monkeypatch.setattr(refinements_module, "get_supabase", lambda: db)
    return db


@pytest.fixture
def transitions(monkeypatch):
    """Stand-in for the DB state machine; records calls and applies the status."""
    calls = []

    async def _transition(commitment_id, target_status, actor="system", data=None, idempotency_key=None):
        calls.append((str(commitment_id), target_status))

    monkeypatch.setattr(state_machine_module, "transition_commitment", _transition)
    return calls


@pytest.fixture
def as_account():
    def _use(account_id: str):
        app.dependency_overrides[get_current_account] = lambda: Account(
            account_id=account_id, email=f"{account_id[:8]}@example.test", key_id=account_id
        )
    yield _use
    app.dependency_overrides.pop(get_current_account, None)


def _body(cid, changes, supersedes=None):
    body = {"commitment_id": cid, "changes": changes, "actor": "tester"}
    if supersedes is not None:
        body["supersedes"] = supersedes
    return body


def _url(cid):
    return f"/api/v1/commitments/{cid}/refinements"


DEADLINE_CHANGE = {
    "field": "deadline_expression",
    "new_value": "by Monday",
    "change_type": "changes_deadline",
    "reason": "extension agreed",
}


# ---------------------------------------------------------------------------
# Legitimate use
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_owner_can_refine_allowed_field(test_client, fake_db, as_account, transitions):
    as_account(ACCOUNT_A)
    b_before = copy.deepcopy(fake_db.row(B_COMMITMENT))

    resp = await test_client.post(_url(A_COMMITMENT), json=_body(A_COMMITMENT, [DEADLINE_CHANGE]))

    assert resp.status_code == 200, resp.text
    history = resp.json()
    assert history["commitment_id"] == A_COMMITMENT
    assert [(c["field"], c["previous_value"], c["new_value"]) for c in history["changes"]] == [
        ("deadline_expression", "by Friday", "by Monday")
    ]
    assert fake_db.row(A_COMMITMENT)["deadline_expression"] == "by Monday"
    assert [c["field"] for c in fake_db.tables["commitment_changes"]] == ["deadline_expression"]
    assert [e["event_type"] for e in fake_db.tables["commitment_events"]] == ["refinement_applied"]
    assert fake_db.row(B_COMMITMENT) == b_before
    assert transitions == []


@pytest.mark.asyncio
async def test_owner_can_refine_several_allowed_fields(test_client, fake_db, as_account, transitions):
    as_account(ACCOUNT_A)
    changes = [
        {"field": "recipient", "new_value": "new-client", "change_type": "changes_recipient"},
        {"field": "priority", "new_value": "high", "change_type": "changes_priority"},
        {"field": "promise_text", "new_value": "A will deliver the full report", "change_type": "clarifies"},
    ]

    resp = await test_client.post(_url(A_COMMITMENT), json=_body(A_COMMITMENT, changes))

    assert resp.status_code == 200, resp.text
    row = fake_db.row(A_COMMITMENT)
    assert (row["recipient"], row["priority"], row["promise_text"]) == (
        "new-client", "high", "A will deliver the full report"
    )
    assert len(fake_db.tables["commitment_changes"]) == 3


@pytest.mark.asyncio
async def test_change_without_field_is_still_skipped(test_client, fake_db, as_account, transitions):
    """Existing behaviour: an entry with no 'field' is ignored, not an error."""
    as_account(ACCOUNT_A)
    resp = await test_client.post(
        _url(A_COMMITMENT), json=_body(A_COMMITMENT, [{"new_value": "x"}, DEADLINE_CHANGE])
    )
    assert resp.status_code == 200, resp.text
    assert [c["field"] for c in resp.json()["changes"]] == ["deadline_expression"]


@pytest.mark.asyncio
async def test_writes_are_scoped_to_the_owner(test_client, fake_db, as_account, transitions):
    """Defence in depth: the commitment UPDATE itself filters on user_id."""
    as_account(ACCOUNT_A)
    await test_client.post(_url(A_COMMITMENT), json=_body(A_COMMITMENT, [DEADLINE_CHANGE]))
    updates = [w for w in fake_db.writes if w[0] == "update" and w[1] == "commitments"]
    assert updates and all(("user_id", ACCOUNT_A) in w[3] for w in updates)


@pytest.mark.asyncio
async def test_owner_can_supersede_own_commitment(test_client, fake_db, as_account, transitions):
    as_account(ACCOUNT_A)

    resp = await test_client.post(
        _url(A_OTHER), json=_body(A_OTHER, [DEADLINE_CHANGE], supersedes=A_COMMITMENT)
    )

    assert resp.status_code == 200, resp.text
    assert fake_db.row(A_COMMITMENT)["superseded_by"] == A_OTHER
    assert transitions == [(A_COMMITMENT, "superseded")]


# ---------------------------------------------------------------------------
# Cross-account access
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_other_account_cannot_refine(test_client, fake_db, as_account, transitions):
    as_account(ACCOUNT_B)
    before = fake_db.snapshot()

    resp = await test_client.post(_url(A_COMMITMENT), json=_body(A_COMMITMENT, [DEADLINE_CHANGE]))

    assert resp.status_code == 404
    assert resp.json() == {"detail": "Commitment not found"}
    assert fake_db.snapshot() == before
    assert fake_db.writes == []
    assert transitions == []


@pytest.mark.asyncio
async def test_foreign_commitment_is_indistinguishable_from_missing(test_client, fake_db, as_account, transitions):
    as_account(ACCOUNT_B)
    missing = str(uuid.uuid4())

    foreign = await test_client.post(_url(A_COMMITMENT), json=_body(A_COMMITMENT, [DEADLINE_CHANGE]))
    absent = await test_client.post(_url(missing), json=_body(missing, [DEADLINE_CHANGE]))

    assert (foreign.status_code, foreign.json()) == (absent.status_code, absent.json())
    assert fake_db.writes == []


@pytest.mark.asyncio
async def test_other_account_cannot_supersede(test_client, fake_db, as_account, transitions):
    """B refines its own commitment but names A's as superseded: nothing may change."""
    as_account(ACCOUNT_B)
    before = fake_db.snapshot()

    resp = await test_client.post(
        _url(B_COMMITMENT), json=_body(B_COMMITMENT, [DEADLINE_CHANGE], supersedes=A_COMMITMENT)
    )

    assert resp.status_code == 404
    assert resp.json() == {"detail": "Commitment not found"}
    assert fake_db.snapshot() == before
    assert fake_db.writes == []
    assert transitions == []


@pytest.mark.asyncio
async def test_other_account_cannot_supersede_without_changes(test_client, fake_db, as_account, transitions):
    as_account(ACCOUNT_B)
    before = fake_db.snapshot()

    resp = await test_client.post(_url(B_COMMITMENT), json=_body(B_COMMITMENT, [], supersedes=A_COMMITMENT))

    assert resp.status_code == 404
    assert fake_db.snapshot() == before
    assert transitions == []


# ---------------------------------------------------------------------------
# Field allow-list
# ---------------------------------------------------------------------------

FORBIDDEN = [
    ("user_id", ACCOUNT_B),
    ("status", "fulfilled"),
    ("receipt_token", "attacker-token"),
    ("id", str(uuid.uuid4())),
    ("idempotency_key", "attacker-idem"),
    ("created_at", "2020-01-01T00:00:00+00:00"),
    ("tenant_id", str(uuid.uuid4())),
    ("superseded_by", B_COMMITMENT),
    ("shape", "deliverable"),
    ("verifier_query", "select 1"),
    ("verification_status", "verified"),
    ("risk_score", 0.0),
    ("not_a_column", "x"),
    (" user_id", ACCOUNT_B),
    ("USER_ID", ACCOUNT_B),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", FORBIDDEN)
async def test_forbidden_field_is_rejected_and_nothing_changes(
    test_client, fake_db, as_account, transitions, field, value
):
    as_account(ACCOUNT_A)
    before = fake_db.snapshot()

    resp = await test_client.post(
        _url(A_COMMITMENT),
        json=_body(A_COMMITMENT, [{"field": field, "new_value": value, "change_type": "refines"}]),
    )

    assert resp.status_code == 422
    assert "cannot be changed by a refinement" in resp.text
    assert fake_db.snapshot() == before
    assert fake_db.writes == []
    assert transitions == []


@pytest.mark.asyncio
async def test_one_forbidden_field_rejects_the_whole_request(test_client, fake_db, as_account, transitions):
    as_account(ACCOUNT_A)
    before = fake_db.snapshot()

    resp = await test_client.post(
        _url(A_COMMITMENT),
        json=_body(A_COMMITMENT, [DEADLINE_CHANGE, {"field": "user_id", "new_value": ACCOUNT_B}]),
    )

    assert resp.status_code == 422
    assert fake_db.snapshot() == before
    assert fake_db.row(A_COMMITMENT)["user_id"] == ACCOUNT_A
    assert fake_db.row(A_COMMITMENT)["deadline_expression"] == "by Friday"


@pytest.mark.asyncio
async def test_non_string_field_name_is_rejected(test_client, fake_db, as_account, transitions):
    as_account(ACCOUNT_A)
    before = fake_db.snapshot()
    resp = await test_client.post(
        _url(A_COMMITMENT), json=_body(A_COMMITMENT, [{"field": ["user_id"], "new_value": ACCOUNT_B}])
    )
    assert resp.status_code == 422
    assert fake_db.snapshot() == before


@pytest.mark.asyncio
async def test_other_account_ownership_takeover_is_rejected(test_client, fake_db, as_account, transitions):
    """The original exploit: B rewrites user_id on A's commitment."""
    as_account(ACCOUNT_B)
    before = fake_db.snapshot()

    resp = await test_client.post(
        _url(A_COMMITMENT),
        json=_body(A_COMMITMENT, [{"field": "user_id", "new_value": ACCOUNT_B}]),
    )

    assert resp.status_code in (404, 422)
    assert fake_db.snapshot() == before
    assert fake_db.row(A_COMMITMENT)["user_id"] == ACCOUNT_A


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_missing_authentication_is_rejected(test_client, fake_db, transitions):
    app.dependency_overrides.pop(get_current_account, None)
    before = fake_db.snapshot()

    resp = await test_client.post(_url(A_COMMITMENT), json=_body(A_COMMITMENT, [DEADLINE_CHANGE]))

    assert resp.status_code == 401
    assert fake_db.snapshot() == before
    assert fake_db.writes == []
