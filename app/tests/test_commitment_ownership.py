"""P0-3 slice 2 – ownership on commitment write routes (no DB, no network).

Covers PATCH /commitments/{id}, POST /commitments/{id}/approve,
POST /commitments/{id}/evidence, POST /evidence/external, POST /dependencies,
DELETE /dependencies/{id} and POST /commitments/{id}/redact.

Accounts A and B own commitments in an in-memory fake of the Supabase client.
The real state machine runs against the fake (its RPC is made unavailable, so
it takes its Python fallback), so legitimate transitions are exercised for
real. Every test asserts the HTTP response and the stored rows.
"""
import copy
import uuid

import pytest

import app.api.dependencies as dependencies_api
import app.api.evidence as evidence_api
import app.api.privacy as privacy_api
import app.api.status as status_api
import app.core.dependency as dependency_core
import app.core.state_machine as state_machine
import app.db.connection as db_connection
from app.core.auth import Account, get_current_account
from app.main import app

ACCOUNT_A = "aaaaaaaa-0000-4000-8000-00000000000a"
ACCOUNT_B = "bbbbbbbb-0000-4000-8000-00000000000b"

A_OPEN = "a1000000-0000-4000-8000-000000000001"
A_OPEN_2 = "a2000000-0000-4000-8000-000000000002"
A_PENDING = "a3000000-0000-4000-8000-000000000003"
A_BLOCKED = "a4000000-0000-4000-8000-000000000004"
A_BLOCKED_BY_B = "a5000000-0000-4000-8000-000000000005"
B_OPEN = "b1000000-0000-4000-8000-000000000001"

A_REVIEW = "a6000000-0000-4000-8000-000000000006"
A_DEP = "a7000000-0000-4000-8000-000000000007"        # A_OPEN blocks A_BLOCKED
LEGACY_DEP = "c1000000-0000-4000-8000-000000000001"   # B_OPEN blocks A_BLOCKED_BY_B (pre-fix cross-account row)

A_EMAIL_TEXT = "Send the contract to alice@example.com by Friday"


def _commitment(cid, owner, status, promise="Deliver the report by Friday"):
    return {
        "id": cid,
        "user_id": owner,
        "source_agent_id": "5a5a5a5a-0000-4000-8000-000000000000",
        "status": status,
        "promise_text": promise,
        "action": "deliver",
        "object": "report",
        "recipient": "client",
        "due_condition": {"type": "time", "deadline": "2026-12-01T00:00:00+00:00"},
        "confidence": 0.9,
        "shape": None,
        "created_at": "2026-10-01T00:00:00+00:00",
        "updated_at": "2026-10-01T00:00:00+00:00",
    }


# ---------------------------------------------------------------------------
# In-memory fake of the supabase-py query builder subset these routes use.
# ---------------------------------------------------------------------------

class _Resp:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, db, table):
        self.db, self.table = db, table
        self.op, self.payload, self.filters = "select", None, []
        self.single, self.on_conflict, self.ignore_duplicates = False, None, False

    def select(self, *_a, **_kw):
        self.op = "select"
        return self

    def insert(self, payload):
        self.op, self.payload = "insert", payload
        return self

    def upsert(self, payload, on_conflict=None, ignore_duplicates=False, **_kw):
        self.op, self.payload = "upsert", payload
        self.on_conflict, self.ignore_duplicates = on_conflict, ignore_duplicates
        return self

    def update(self, payload):
        self.op, self.payload = "update", payload
        return self

    def eq(self, column, value):
        self.filters.append(lambda r, c=column, v=str(value): str(r.get(c)) == v)
        return self

    def in_(self, column, values):
        allowed = {str(v) for v in values}
        self.filters.append(lambda r, c=column: str(r.get(c)) in allowed)
        return self

    def is_(self, column, value):
        assert value == "null"
        self.filters.append(lambda r, c=column: r.get(c) is None)
        return self

    def order(self, *_a, **_kw):
        return self

    def limit(self, *_a, **_kw):
        return self

    def maybe_single(self):
        self.single = True
        return self

    def _hits(self, rows):
        return [r for r in rows if all(f(r) for f in self.filters)]

    async def execute(self):
        rows = self.db.tables.setdefault(self.table, [])
        if self.op == "select":
            hits = [copy.deepcopy(r) for r in self._hits(rows)]
            if self.single:
                return _Resp(hits[0] if hits else None)
            return _Resp(hits)
        self.db.writes.append((self.op, self.table, copy.deepcopy(self.payload)))
        if self.op == "insert":
            rows.append(copy.deepcopy(self.payload))
            return _Resp([copy.deepcopy(self.payload)])
        if self.op == "upsert":
            key = self.payload.get(self.on_conflict)
            clash = [r for r in rows if key is not None and r.get(self.on_conflict) == key]
            if clash and self.ignore_duplicates:
                return _Resp([])
            rows.append(copy.deepcopy(self.payload))
            return _Resp([copy.deepcopy(self.payload)])
        hits = self._hits(rows)
        for r in hits:
            r.update(copy.deepcopy(self.payload))
        return _Resp([copy.deepcopy(r) for r in hits])


class _UnavailableRpc:
    async def execute(self):
        raise RuntimeError("rpc unavailable in fake")


class FakeDB:
    def __init__(self):
        self.tables = {
            "commitments": [
                _commitment(A_OPEN, ACCOUNT_A, "open", A_EMAIL_TEXT),
                _commitment(A_OPEN_2, ACCOUNT_A, "open"),
                _commitment(A_PENDING, ACCOUNT_A, "pending_review"),
                _commitment(A_BLOCKED, ACCOUNT_A, "blocked"),
                _commitment(A_BLOCKED_BY_B, ACCOUNT_A, "blocked"),
                _commitment(B_OPEN, ACCOUNT_B, "open"),
            ],
            "human_reviews": [{
                "id": A_REVIEW, "commitment_id": A_PENDING, "status": "pending",
                "created_at": "2026-10-01T00:00:00+00:00", "resolved_at": None, "resolution": None,
            }],
            "commitment_dependencies": [
                {"id": A_DEP, "source_commitment_id": A_OPEN, "target_commitment_id": A_BLOCKED,
                 "dependency_type": "blocks", "created_at": "2026-10-01T00:00:00+00:00",
                 "resolved_at": None, "metadata": {}},
                {"id": LEGACY_DEP, "source_commitment_id": B_OPEN, "target_commitment_id": A_BLOCKED_BY_B,
                 "dependency_type": "blocks", "created_at": "2026-10-01T00:00:00+00:00",
                 "resolved_at": None, "metadata": {}},
            ],
            "evidence": [],
            "commitment_events": [],
        }
        self.writes = []

    def table(self, name):
        return _Query(self, name)

    def rpc(self, *_a, **_kw):
        return _UnavailableRpc()

    def row(self, table, rid):
        return next(r for r in self.tables[table] if r["id"] == rid)

    def status(self, cid):
        return self.row("commitments", cid)["status"]

    def snapshot(self):
        return copy.deepcopy(self.tables)


@pytest.fixture
def db(monkeypatch):
    fake = FakeDB()
    for module in (status_api, evidence_api, dependencies_api, dependency_core,
                   privacy_api, state_machine, db_connection):
        monkeypatch.setattr(module, "get_supabase", lambda: fake, raising=False)
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
EVIDENCE = {"source": "test", "data": {"action": "deliver", "object": "report"}}


def _evidence(cid):
    return {**EVIDENCE, "commitment_id": cid}


# ---------------------------------------------------------------------------
# PATCH /commitments/{id}
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_owner_can_change_status(test_client, db, as_account):
    as_account(ACCOUNT_A)
    resp = await test_client.patch(f"/api/v1/commitments/{A_OPEN_2}", json={"status": "fulfilled"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "fulfilled"
    assert db.status(A_OPEN_2) == "fulfilled"
    assert [(e["event_type"], e["new_status"]) for e in db.tables["commitment_events"]] == [
        ("status_changed", "fulfilled")
    ]


@pytest.mark.asyncio
async def test_owner_invalid_transition_still_409(test_client, db, as_account):
    as_account(ACCOUNT_A)
    before = db.snapshot()
    resp = await test_client.patch(f"/api/v1/commitments/{A_PENDING}", json={"status": "fulfilled"})
    assert resp.status_code == 409
    assert db.snapshot() == before


@pytest.mark.asyncio
async def test_other_account_cannot_change_status(test_client, db, as_account):
    as_account(ACCOUNT_B)
    before = db.snapshot()
    resp = await test_client.patch(f"/api/v1/commitments/{A_OPEN}", json={"status": "cancelled"})
    assert (resp.status_code, resp.json()) == (404, NOT_FOUND)
    assert db.snapshot() == before and db.writes == []


@pytest.mark.asyncio
async def test_status_foreign_and_missing_look_the_same(test_client, db, as_account):
    as_account(ACCOUNT_B)
    foreign = await test_client.patch(f"/api/v1/commitments/{A_OPEN}", json={"status": "cancelled"})
    missing = await test_client.patch(f"/api/v1/commitments/{uuid.uuid4()}", json={"status": "cancelled"})
    assert (foreign.status_code, foreign.json()) == (missing.status_code, missing.json()) == (404, NOT_FOUND)


# ---------------------------------------------------------------------------
# POST /commitments/{id}/approve
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_owner_can_approve(test_client, db, as_account):
    as_account(ACCOUNT_A)
    resp = await test_client.post(f"/api/v1/commitments/{A_PENDING}/approve", json={})
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"id": A_PENDING, "state": "open", "status": "open"}
    assert db.status(A_PENDING) == "open"
    assert db.row("human_reviews", A_REVIEW)["status"] == "accepted"


@pytest.mark.asyncio
async def test_owner_approve_of_non_pending_still_409(test_client, db, as_account):
    as_account(ACCOUNT_A)
    before = db.snapshot()
    resp = await test_client.post(f"/api/v1/commitments/{A_OPEN}/approve", json={})
    assert resp.status_code == 409
    assert db.snapshot() == before


@pytest.mark.asyncio
async def test_other_account_cannot_approve(test_client, db, as_account):
    as_account(ACCOUNT_B)
    before = db.snapshot()
    resp = await test_client.post(f"/api/v1/commitments/{A_PENDING}/approve", json={})
    assert (resp.status_code, resp.json()) == (404, NOT_FOUND)
    assert db.snapshot() == before and db.writes == []
    assert db.row("human_reviews", A_REVIEW)["status"] == "pending"


# ---------------------------------------------------------------------------
# Evidence
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_owner_can_attach_evidence(test_client, db, as_account):
    as_account(ACCOUNT_A)
    resp = await test_client.post(f"/api/v1/commitments/{A_OPEN}/evidence", json=_evidence(A_OPEN))
    assert resp.status_code == 200, resp.text
    assert resp.json()["commitment_id"] == A_OPEN
    assert [e["commitment_id"] for e in db.tables["evidence"]] == [A_OPEN]
    assert [e["event_type"] for e in db.tables["commitment_events"]] == ["evidence_submitted"]


@pytest.mark.asyncio
async def test_owner_can_attach_external_evidence(test_client, db, as_account):
    as_account(ACCOUNT_A)
    resp = await test_client.post("/api/v1/evidence/external", json=_evidence(A_OPEN))
    assert resp.status_code == 200, resp.text
    assert [e["commitment_id"] for e in db.tables["evidence"]] == [A_OPEN]


@pytest.mark.asyncio
async def test_other_account_cannot_attach_evidence(test_client, db, as_account):
    as_account(ACCOUNT_B)
    before = db.snapshot()
    resp = await test_client.post(f"/api/v1/commitments/{A_OPEN}/evidence", json=_evidence(A_OPEN))
    assert (resp.status_code, resp.json()) == (404, NOT_FOUND)
    assert db.snapshot() == before and db.writes == []


@pytest.mark.asyncio
async def test_other_account_cannot_attach_external_evidence(test_client, db, as_account):
    as_account(ACCOUNT_B)
    before = db.snapshot()
    resp = await test_client.post("/api/v1/evidence/external", json=_evidence(A_OPEN))
    assert (resp.status_code, resp.json()) == (404, NOT_FOUND)
    assert db.snapshot() == before and db.writes == []


@pytest.mark.asyncio
async def test_evidence_for_missing_commitment_is_404(test_client, db, as_account):
    as_account(ACCOUNT_A)
    missing = str(uuid.uuid4())
    resp = await test_client.post(f"/api/v1/commitments/{missing}/evidence", json=_evidence(missing))
    assert (resp.status_code, resp.json()) == (404, NOT_FOUND)
    assert db.writes == []


# ---------------------------------------------------------------------------
# POST /dependencies
# ---------------------------------------------------------------------------

def _dep(source, target, kind="blocks"):
    return {"source_commitment_id": source, "target_commitment_id": target, "dependency_type": kind}


@pytest.mark.asyncio
async def test_owner_can_create_dependency(test_client, db, as_account):
    as_account(ACCOUNT_A)
    resp = await test_client.post("/api/v1/dependencies", json=_dep(A_OPEN, A_OPEN_2))
    assert resp.status_code == 200, resp.text
    created = resp.json()
    assert (created["source_commitment_id"], created["target_commitment_id"]) == (A_OPEN, A_OPEN_2)
    assert db.status(A_OPEN_2) == "blocked"   # blocks → state machine transition still runs


@pytest.mark.asyncio
async def test_owner_self_dependency_still_409(test_client, db, as_account):
    as_account(ACCOUNT_A)
    before = db.snapshot()
    resp = await test_client.post("/api/v1/dependencies", json=_dep(A_OPEN, A_OPEN))
    assert resp.status_code == 409
    assert db.snapshot() == before


@pytest.mark.asyncio
@pytest.mark.parametrize("source,target", [
    (B_OPEN, A_OPEN),     # B's source would block A's commitment
    (A_OPEN, B_OPEN),     # A's commitment as B's blocker
    (A_OPEN, A_OPEN_2),   # both A's
])
async def test_other_account_cannot_create_dependency_involving_a(test_client, db, as_account, source, target):
    as_account(ACCOUNT_B)
    before = db.snapshot()
    resp = await test_client.post("/api/v1/dependencies", json=_dep(source, target))
    assert (resp.status_code, resp.json()) == (404, NOT_FOUND)
    assert db.snapshot() == before and db.writes == []


@pytest.mark.asyncio
async def test_dependency_with_missing_commitment_is_404(test_client, db, as_account):
    as_account(ACCOUNT_A)
    resp = await test_client.post("/api/v1/dependencies", json=_dep(A_OPEN, str(uuid.uuid4())))
    assert (resp.status_code, resp.json()) == (404, NOT_FOUND)
    assert db.writes == []


# ---------------------------------------------------------------------------
# DELETE /dependencies/{id}
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_owner_can_resolve_dependency(test_client, db, as_account):
    as_account(ACCOUNT_A)
    resp = await test_client.delete(f"/api/v1/dependencies/{A_DEP}")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"resolved": True, "dependency_id": A_DEP}
    assert db.row("commitment_dependencies", A_DEP)["resolved_at"] is not None
    assert db.status(A_BLOCKED) == "open"     # unblocked through the state machine


@pytest.mark.asyncio
async def test_other_account_cannot_resolve_dependency(test_client, db, as_account):
    as_account(ACCOUNT_B)
    before = db.snapshot()
    resp = await test_client.delete(f"/api/v1/dependencies/{A_DEP}")
    assert (resp.status_code, resp.json()) == (404, {"detail": f"Dependency {A_DEP} not found"})
    assert db.snapshot() == before and db.writes == []


@pytest.mark.asyncio
async def test_foreign_and_missing_dependency_look_the_same(test_client, db, as_account):
    as_account(ACCOUNT_B)
    missing = str(uuid.uuid4())
    foreign = await test_client.delete(f"/api/v1/dependencies/{A_DEP}")
    absent = await test_client.delete(f"/api/v1/dependencies/{missing}")
    assert foreign.status_code == absent.status_code == 404
    assert foreign.json()["detail"].replace(A_DEP, "<id>") == absent.json()["detail"].replace(missing, "<id>")


@pytest.mark.asyncio
async def test_owner_of_blocker_only_cannot_unblock_other_accounts_commitment(test_client, db, as_account):
    """A pre-fix row where B's commitment blocks A's: B must not be able to unblock A's."""
    as_account(ACCOUNT_B)
    before = db.snapshot()
    resp = await test_client.delete(f"/api/v1/dependencies/{LEGACY_DEP}")
    assert resp.status_code == 404
    assert db.snapshot() == before and db.writes == []


@pytest.mark.asyncio
async def test_owner_of_blocked_commitment_can_clear_foreign_blocker(test_client, db, as_account):
    """The same pre-fix row: A owns the blocked commitment, so A may resolve it."""
    as_account(ACCOUNT_A)
    resp = await test_client.delete(f"/api/v1/dependencies/{LEGACY_DEP}")
    assert resp.status_code == 200, resp.text
    assert db.status(A_BLOCKED_BY_B) == "open"
    assert db.status(B_OPEN) == "open"


# ---------------------------------------------------------------------------
# POST /commitments/{id}/redact
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_owner_can_redact(test_client, db, as_account):
    as_account(ACCOUNT_A)
    resp = await test_client.post(f"/api/v1/commitments/{A_OPEN}/redact")
    assert resp.status_code == 200, resp.text
    assert resp.json()["fields_redacted"] == {"promise_text": "email"}
    assert "alice@example.com" not in db.row("commitments", A_OPEN)["promise_text"]
    assert [e["event_type"] for e in db.tables["commitment_events"]] == ["redacted"]


@pytest.mark.asyncio
async def test_other_account_cannot_redact(test_client, db, as_account):
    as_account(ACCOUNT_B)
    before = db.snapshot()
    resp = await test_client.post(f"/api/v1/commitments/{A_OPEN}/redact")
    assert (resp.status_code, resp.json()) == (404, NOT_FOUND)
    assert db.snapshot() == before and db.writes == []
    assert db.row("commitments", A_OPEN)["promise_text"] == A_EMAIL_TEXT


@pytest.mark.asyncio
async def test_redact_foreign_and_missing_look_the_same(test_client, db, as_account):
    as_account(ACCOUNT_B)
    foreign = await test_client.post(f"/api/v1/commitments/{A_OPEN}/redact")
    missing = await test_client.post(f"/api/v1/commitments/{uuid.uuid4()}/redact")
    assert (foreign.status_code, foreign.json()) == (missing.status_code, missing.json()) == (404, NOT_FOUND)


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("method,path,body", [
    ("PATCH", f"/api/v1/commitments/{A_OPEN}", {"status": "cancelled"}),
    ("POST", f"/api/v1/commitments/{A_PENDING}/approve", {}),
    ("POST", f"/api/v1/commitments/{A_OPEN}/evidence", _evidence(A_OPEN)),
    ("POST", "/api/v1/evidence/external", _evidence(A_OPEN)),
    ("POST", "/api/v1/dependencies", _dep(A_OPEN, A_OPEN_2)),
    ("DELETE", f"/api/v1/dependencies/{A_DEP}", None),
    ("POST", f"/api/v1/commitments/{A_OPEN}/redact", None),
])
async def test_missing_authentication_is_rejected(test_client, db, method, path, body):
    app.dependency_overrides.pop(get_current_account, None)
    before = db.snapshot()
    resp = await test_client.request(method, path, json=body)
    assert resp.status_code == 401
    assert db.snapshot() == before and db.writes == []
