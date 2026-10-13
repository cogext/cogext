"""P0-3 slice 5 – cross-account idempotency collisions (no DB, no network).

Ingest: commitments.idempotency_key is UNIQUE across the whole table
(001_initial_nuclear.sql:83), and the key used to omit the account.

Evidence: evidence.idempotency_key is UNIQUE across the table (002_v1.6.sql:40)
AND uq_evidence_external is a unique index on the raw
(external_system, external_event_id) pair (002_v1.6.sql:48). Migration 012
drops the latter and re-keys existing rows per account.

The fake below enforces the declared unique indexes the way Postgres does:
NULLs never conflict; INSERT .. ON CONFLICT (target) DO NOTHING skips rows that
clash on the target index and raises 23505 for a clash on any other unique
index. Evidence tests run under the current ("pre") and post-migration-012
("post") index sets. They show the application logic is correct *for those
modelled constraints*; they cannot prove the migration SQL itself.
"""
import copy
import hashlib
import json
import uuid

import pytest

import app.api.evidence as evidence_api
import app.api.ingest as ingest_api
from app.core.auth import Account, get_current_account
from app.main import app

ACCOUNT_A = "aaaaaaaa-0000-4000-8000-00000000000a"
ACCOUNT_B = "bbbbbbbb-0000-4000-8000-00000000000b"
AGENT = "5a5a5a5a-0000-4000-8000-000000000000"
A_COMMITMENT = "a1000000-0000-4000-8000-000000000001"
A_COMMITMENT_2 = "a2000000-0000-4000-8000-000000000002"
B_COMMITMENT = "b1000000-0000-4000-8000-000000000001"

MESSAGE = "I will send the quarterly report by Tuesday end of day."
EXTRACTED = json.dumps([{
    "promise_text": "I will send the quarterly report",
    "due_condition": {"type": "time", "deadline": None, "trigger_description": "by Tuesday end of day",
                      "entity_ref": None, "match_threshold": 0.88, "partial_match_threshold": 0.65},
    "confidence": 0.95,
}])

PK = ("pkey", ("id",))
INDEXES = {
    "commitments": [PK, ("commitments_idempotency_key_key", ("idempotency_key",))],
    "commitment_events": [PK],
    "episodic_log": [PK],
}
EVIDENCE_PRE = [PK, ("evidence_idempotency_key_key", ("idempotency_key",)),
                ("uq_evidence_external", ("external_system", "external_event_id"))]
EVIDENCE_POST = [PK, ("evidence_idempotency_key_key", ("idempotency_key",))]


class UniqueViolation(Exception):
    code = "23505"


class _Resp:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, db, table):
        self.db, self.table = db, table
        self.op, self.payload, self.filters = "select", None, []
        self.single, self.target, self.ignore = False, None, False

    def select(self, *_a, **_k):
        self.op = "select"
        return self

    def insert(self, payload):
        self.op, self.payload = "insert", payload
        return self

    def upsert(self, payload, on_conflict=None, ignore_duplicates=False, **_k):
        self.op, self.payload, self.target, self.ignore = "upsert", payload, on_conflict, ignore_duplicates
        return self

    def update(self, payload):
        self.op, self.payload = "update", payload
        return self

    def eq(self, column, value):
        self.filters.append((column, str(value)))
        return self

    def neq(self, *_a):
        return self

    def order(self, *_a, **_k):
        return self

    def limit(self, *_a, **_k):
        return self

    def maybe_single(self):
        self.single = True
        return self

    def _clashes(self, rows, row):
        """Names of unique indexes that `row` would violate (NULLs never clash)."""
        hit = []
        for name, cols in self.db.indexes[self.table]:
            vals = tuple(row.get(c) for c in cols)
            if None in vals:
                continue
            if any(tuple(r.get(c) for c in cols) == vals for r in rows):
                hit.append(name)
        return hit

    async def execute(self):
        rows = self.db.tables.setdefault(self.table, [])
        if self.op == "select":
            self.db.selects.append((self.table, list(self.filters)))
            hits = [copy.deepcopy(r) for r in rows if all(str(r.get(c)) == v for c, v in self.filters)]
            return _Resp((hits[0] if hits else None) if self.single else hits)
        if self.db.fail_next.get(self.table):
            self.db.fail_next[self.table] -= 1
            raise ConnectionError("simulated transient failure before the write reached the DB")
        row = copy.deepcopy(self.payload)
        if self.op in ("insert", "upsert"):
            clashes = self._clashes(rows, row)
            target = next((n for n, cols in self.db.indexes[self.table] if cols == (self.target,)), None)
            if self.op == "upsert" and target in clashes and self.ignore:
                return _Resp([])                      # ON CONFLICT (target) DO NOTHING
            if clashes:
                raise UniqueViolation(f"duplicate key value violates unique constraint {clashes[0]!r}")
            rows.append(row)
            self.db.writes.append((self.op, self.table))
            return _Resp([copy.deepcopy(row)])
        hits = [r for r in rows if all(str(r.get(c)) == v for c, v in self.filters)]
        for r in hits:
            r.update(row)
        self.db.writes.append(("update", self.table))
        return _Resp([copy.deepcopy(r) for r in hits])


class ConstraintDB:
    def __init__(self, evidence_indexes):
        self.indexes = {**INDEXES, "evidence": evidence_indexes}
        self.tables = {"commitments": [
            {"id": A_COMMITMENT, "user_id": ACCOUNT_A, "action": "send", "object": "report",
             "recipient": None, "due_condition": {}, "status": "open"},
            {"id": A_COMMITMENT_2, "user_id": ACCOUNT_A, "action": "send", "object": "report",
             "recipient": None, "due_condition": {}, "status": "open"},
            {"id": B_COMMITMENT, "user_id": ACCOUNT_B, "action": "send", "object": "report",
             "recipient": None, "due_condition": {}, "status": "open"},
        ]}
        self.writes, self.selects, self.fail_next = [], [], {}

    def table(self, name):
        return _Query(self, name)

    def rows(self, table, **match):
        return [r for r in self.tables.get(table, []) if all(r.get(k) == v for k, v in match.items())]


@pytest.fixture
def as_account():
    def _use(account_id):
        app.dependency_overrides[get_current_account] = lambda: Account(
            account_id=account_id, email=f"{account_id[:8]}@example.test", key_id=account_id
        )
    yield _use
    app.dependency_overrides.pop(get_current_account, None)


# ---------------------------------------------------------------------------
# Ingest
# ---------------------------------------------------------------------------

@pytest.fixture
def ingest_db(monkeypatch):
    db = ConstraintDB(EVIDENCE_POST)
    db.tables["commitments"] = []          # ingest creates its own commitments
    monkeypatch.setattr(ingest_api, "get_supabase", lambda: db)

    async def _none(*_a, **_k):
        return []

    async def _no_risk(*_a, **_k):
        return 0.0, []

    async def _no_delivery(*_a, **_k):
        return 0

    monkeypatch.setattr(ingest_api, "detect_contradictions", _none)
    monkeypatch.setattr(ingest_api, "score_risk", _no_risk)
    monkeypatch.setattr(ingest_api, "deliver_event", _no_delivery)
    monkeypatch.setattr("app.core.extractor.extract_completion", lambda _prompt: EXTRACTED)
    return db


def _ingest_body():
    return {"source_agent_id": AGENT, "message": MESSAGE}


@pytest.mark.asyncio
async def test_ingest_same_account_replay_is_still_deduplicated(test_client, ingest_db, as_account):
    as_account(ACCOUNT_A)
    first = await test_client.post("/api/v1/ingest", json=_ingest_body())
    second = await test_client.post("/api/v1/ingest", json=_ingest_body())
    assert first.status_code == second.status_code == 200, second.text
    assert first.json()["commitments"][0]["id"] == second.json()["commitments"][0]["id"]
    assert len(ingest_db.rows("commitments")) == 1
    assert len(ingest_db.rows("commitment_events", event_type="detected")) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("first,second", [(ACCOUNT_A, ACCOUNT_B), (ACCOUNT_B, ACCOUNT_A)])
async def test_ingest_identical_message_from_two_accounts_creates_two_commitments(
    test_client, ingest_db, as_account, first, second
):
    as_account(first)
    r1 = await test_client.post("/api/v1/ingest", json=_ingest_body())
    as_account(second)
    r2 = await test_client.post("/api/v1/ingest", json=_ingest_body())
    assert r1.status_code == r2.status_code == 200, r2.text

    c1, c2 = r1.json()["commitments"][0], r2.json()["commitments"][0]
    assert (c1["user_id"], c2["user_id"]) == (first, second)   # nobody receives the other's row
    assert c1["id"] != c2["id"]
    rows = ingest_db.rows("commitments")
    assert sorted(r["user_id"] for r in rows) == sorted([ACCOUNT_A, ACCOUNT_B])
    assert rows[0]["idempotency_key"] != rows[1]["idempotency_key"]
    assert len(ingest_db.rows("commitment_events", event_type="detected")) == 2


@pytest.mark.asyncio
async def test_ingest_duplicate_lookup_is_scoped_to_the_account(test_client, ingest_db, as_account):
    as_account(ACCOUNT_A)
    await test_client.post("/api/v1/ingest", json=_ingest_body())
    await test_client.post("/api/v1/ingest", json=_ingest_body())
    lookups = [f for t, f in ingest_db.selects if t == "commitments" and any(c == "idempotency_key" for c, _ in f)]
    assert lookups and all(("user_id", ACCOUNT_A) in f for f in lookups)


@pytest.mark.asyncio
async def test_ingest_retry_after_failed_write_stores_one_commitment(test_client, ingest_db, as_account):
    as_account(ACCOUNT_A)
    ingest_db.fail_next["commitments"] = 1
    failed = await test_client.post("/api/v1/ingest", json=_ingest_body())
    assert failed.status_code == 500
    assert ingest_db.rows("commitments") == [] and ingest_db.rows("commitment_events") == []

    retry = await test_client.post("/api/v1/ingest", json=_ingest_body())
    again = await test_client.post("/api/v1/ingest", json=_ingest_body())
    assert retry.status_code == again.status_code == 200
    assert len(ingest_db.rows("commitments")) == 1
    assert retry.json()["commitments"][0]["id"] == again.json()["commitments"][0]["id"]


# ---------------------------------------------------------------------------
# Evidence
# ---------------------------------------------------------------------------

@pytest.fixture
def evidence_db(monkeypatch, request):
    db = ConstraintDB(EVIDENCE_POST if getattr(request, "param", "post") == "post" else EVIDENCE_PRE)
    monkeypatch.setattr(evidence_api, "get_supabase", lambda: db)
    return db


def _external(cid, event_id="msg-123", system="gmail"):
    return {"commitment_id": cid, "source": "adapter", "data": {"action": "send"},
            "external_system": system, "external_event_id": event_id}


async def _submit(client, cid, body):
    return await client.post(f"/api/v1/commitments/{cid}/evidence", json=body)


@pytest.mark.asyncio
async def test_evidence_same_account_same_event_is_still_deduplicated(test_client, evidence_db, as_account):
    as_account(ACCOUNT_A)
    first = await _submit(test_client, A_COMMITMENT, _external(A_COMMITMENT))
    second = await _submit(test_client, A_COMMITMENT, _external(A_COMMITMENT))
    assert first.status_code == second.status_code == 200, second.text
    assert len(evidence_db.rows("evidence")) == 1


@pytest.mark.asyncio
async def test_evidence_same_account_dedup_spans_its_commitments(test_client, evidence_db, as_account):
    """Unchanged semantics: within one account an external event is stored once,
    even if submitted for a second commitment (per-commitment scope would be a
    product decision)."""
    as_account(ACCOUNT_A)
    await _submit(test_client, A_COMMITMENT, _external(A_COMMITMENT))
    await _submit(test_client, A_COMMITMENT_2, _external(A_COMMITMENT_2))
    assert [r["commitment_id"] for r in evidence_db.rows("evidence")] == [A_COMMITMENT]


@pytest.mark.asyncio
@pytest.mark.parametrize("first,second", [
    ((ACCOUNT_A, A_COMMITMENT), (ACCOUNT_B, B_COMMITMENT)),
    ((ACCOUNT_B, B_COMMITMENT), (ACCOUNT_A, A_COMMITMENT)),
])
async def test_evidence_same_external_ids_from_two_accounts_both_stored(
    test_client, evidence_db, as_account, first, second
):
    for account, cid in (first, second):
        as_account(account)
        resp = await _submit(test_client, cid, _external(cid))
        assert resp.status_code == 200, resp.text
    stored = evidence_db.rows("evidence")
    assert sorted(r["commitment_id"] for r in stored) == sorted([A_COMMITMENT, B_COMMITMENT])
    assert stored[0]["idempotency_key"] != stored[1]["idempotency_key"]
    assert len(evidence_db.rows("commitment_events", event_type="evidence_submitted")) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("evidence_db", ["pre"], indirect=True)
async def test_without_migration_012_the_global_index_still_blocks_other_accounts(
    test_client, evidence_db, as_account
):
    """Documents why 012 is required: with only the key namespaced, the global
    raw-identifier index rejects B's row. It fails loudly (500), not silently, and
    nothing of B's is stored."""
    as_account(ACCOUNT_A)
    assert (await _submit(test_client, A_COMMITMENT, _external(A_COMMITMENT))).status_code == 200
    as_account(ACCOUNT_B)
    blocked = await _submit(test_client, B_COMMITMENT, _external(B_COMMITMENT))
    assert blocked.status_code == 500
    assert [r["commitment_id"] for r in evidence_db.rows("evidence")] == [A_COMMITMENT]
    assert evidence_db.rows("commitment_events", commitment_id=B_COMMITMENT) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("evidence_db", ["pre", "post"], indirect=True)
async def test_evidence_without_external_ids_is_never_deduplicated(test_client, evidence_db, as_account):
    as_account(ACCOUNT_A)
    body = {"commitment_id": A_COMMITMENT, "source": "manual", "data": {"action": "send"}}
    for _ in range(2):
        assert (await _submit(test_client, A_COMMITMENT, body)).status_code == 200
    assert len(evidence_db.rows("evidence")) == 2


@pytest.mark.asyncio
async def test_evidence_retry_after_failed_write_stores_one_row(test_client, evidence_db, as_account):
    as_account(ACCOUNT_A)
    evidence_db.fail_next["evidence"] = 1
    failed = await _submit(test_client, A_COMMITMENT, _external(A_COMMITMENT))
    assert failed.status_code == 500
    assert evidence_db.rows("evidence") == [] and evidence_db.rows("commitment_events") == []

    for _ in range(2):
        assert (await _submit(test_client, A_COMMITMENT, _external(A_COMMITMENT))).status_code == 200
    assert len(evidence_db.rows("evidence")) == 1


@pytest.mark.asyncio
async def test_evidence_key_matches_the_migration_012_backfill_formula(test_client, evidence_db, as_account):
    """012 re-keys existing rows in SQL as
    encode(sha256(convert_to(user_id::text || ':' || external_system || ':' || external_event_id, 'UTF8')), 'hex').
    The API must write the same value, or same-account dedup breaks across the deploy."""
    as_account(ACCOUNT_A)
    await _submit(test_client, A_COMMITMENT, _external(A_COMMITMENT, event_id="evt:42", system="gmail"))
    expected = hashlib.sha256(f"{ACCOUNT_A}:gmail:evt:42".encode("utf-8")).hexdigest()
    assert evidence_db.rows("evidence")[0]["idempotency_key"] == expected
