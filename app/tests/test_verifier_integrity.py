"""Verifier evidence integrity and migration 012 guards (no DB, no network).

run_verification may only report success on evidence that is actually stored
for the commitment being verified. The fake below rejects writes to columns
that do not exist in the evidence table (002_v1.6.sql), as PostgREST does, so
the old "relevance_score" write fails here exactly as it would in production.
The LLM scorer is stubbed; the real state machine runs against the fake.
"""
import hashlib
import pathlib
import re

import pytest

import app.api.verify as verify_api
import app.core.state_machine as state_machine
import app.core.verifier as verifier
from app.core.auth import Account, get_current_account
from app.main import app
from app.models.commitment import Commitment
from app.tests.test_commitment_ownership import (
    A_OPEN,
    A_OPEN_2,
    A_PENDING,
    ACCOUNT_A,
    ACCOUNT_B,
    B_OPEN,
    FakeDB,
    _Query,
)

# Columns of the evidence table as created by 002_v1.6.sql (no later migration adds any).
EVIDENCE_COLUMNS = {
    "id", "commitment_id", "source", "external_system", "external_event_id", "actor",
    "provenance", "raw_reference", "adapter_version", "data", "occurred_at", "recorded_at",
    "strength", "score", "match_details", "verified", "verification_details", "idempotency_key",
}


class SchemaError(Exception):
    """Stand-in for PostgREST's 'column not found' error."""


class _SchemaQuery(_Query):
    async def execute(self):
        if self.table == "evidence" and self.op in ("insert", "upsert", "update"):
            if self.db.fail_evidence_writes:
                raise ConnectionError("simulated DB failure on evidence write")
            unknown = set(self.payload) - EVIDENCE_COLUMNS
            if unknown:
                raise SchemaError(f"Could not find the {sorted(unknown)} column of 'evidence'")
        return await super().execute()


class SchemaFakeDB(FakeDB):
    def __init__(self):
        super().__init__()
        self.fail_evidence_writes = False

    def table(self, name):
        return _SchemaQuery(self, name)


@pytest.fixture
def db(monkeypatch):
    fake = SchemaFakeDB()
    for module in (verifier, state_machine, verify_api):
        monkeypatch.setattr(module, "get_supabase", lambda: fake)
    return fake


@pytest.fixture
def score(monkeypatch):
    """Set the stubbed LLM relevance score for the next verifications."""
    current = {"value": 0.9}

    async def _score(_commitment, _evidence):
        return current["value"], "stubbed score"

    monkeypatch.setattr(verifier, "_score_evidence", _score)

    def _set(value):
        current["value"] = value
    return _set


def _gmail(message_id="msg-1"):
    return {"source": "gmail", "message_id": message_id, "thread_id": "t-1",
            "from": "client@example.test", "subject": "Report", "date": "2026-10-10T10:00:00Z"}


def _commitment(db, cid):
    return Commitment(**db.row("commitments", cid))


def _external_id(message_id="msg-1"):
    return hashlib.sha256(f"gmail:{message_id}".encode()).hexdigest()[:32]


def _commitment_state(db, cid):
    row = db.row("commitments", cid)
    return row["status"], row.get("verification_status")


# ---------------------------------------------------------------------------
# Successful verification stores real evidence
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_success_stores_evidence_and_returns_its_id(db, score):
    result = await verifier.run_verification(_commitment(db, A_OPEN), _gmail())

    stored = db.tables["evidence"]
    assert len(stored) == 1
    row = stored[0]
    assert result["status"] == "verified"
    assert result["evidence_id"] == row["id"]                 # an id that exists
    assert (row["commitment_id"], row["score"], row["external_system"]) == (A_OPEN, 0.9, "gmail")
    assert row["external_event_id"] == _external_id()
    assert row["idempotency_key"] == hashlib.sha256(
        f"{ACCOUNT_A}:gmail:{_external_id()}".encode()).hexdigest()   # same format as POST /evidence
    assert _commitment_state(db, A_OPEN) == ("fulfilled", "verified")


@pytest.mark.asyncio
async def test_external_commitment_passes_evidence_gate_with_stored_score(db, score):
    db.row("commitments", A_OPEN)["shape"] = "external_side_effect"
    result = await verifier.run_verification(_commitment(db, A_OPEN), _gmail())
    assert result["status"] == "verified"
    assert _commitment_state(db, A_OPEN) == ("fulfilled", "verified")


@pytest.mark.asyncio
async def test_mid_score_stores_evidence_without_transition(db, score):
    score(0.5)
    result = await verifier.run_verification(_commitment(db, A_OPEN), _gmail())
    assert result["status"] == "pending"
    assert result["evidence_id"] == db.tables["evidence"][0]["id"]
    assert _commitment_state(db, A_OPEN) == ("open", "pending")


# ---------------------------------------------------------------------------
# A failed or skipped write can never look like success
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_failed_evidence_write_is_not_a_successful_verification(db, score):
    db.fail_evidence_writes = True
    before = db.row("commitments", A_OPEN).copy()

    result = await verifier.run_verification(_commitment(db, A_OPEN), _gmail())

    assert result["status"] == "evidence_not_recorded"
    assert result["evidence_id"] is None
    assert db.tables["evidence"] == []
    assert db.row("commitments", A_OPEN) == before            # no transition, no verification_status
    assert db.tables["commitment_events"] == []


@pytest.mark.asyncio
async def test_written_columns_all_exist_in_the_evidence_schema(db, score):
    """Regression for the 'relevance_score' write: every column must exist."""
    await verifier.run_verification(_commitment(db, A_OPEN), _gmail())
    assert db.tables["evidence"] and set(db.tables["evidence"][0]) <= EVIDENCE_COLUMNS


@pytest.mark.asyncio
async def test_transition_failure_is_not_reported_as_verified(db, score):
    """pending_review -> fulfilled is not a valid transition."""
    result = await verifier.run_verification(_commitment(db, A_PENDING), _gmail())
    assert result["status"] == "transition_failed"
    assert result["evidence_id"] == db.tables["evidence"][0]["id"]   # evidence is real
    status, v_status = _commitment_state(db, A_PENDING)
    assert status == "pending_review" and v_status != "verified"


# ---------------------------------------------------------------------------
# Duplicates and tenant isolation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_same_account_retry_reuses_the_stored_evidence(db, score):
    first = await verifier.run_verification(_commitment(db, A_OPEN), _gmail())
    retry = await verifier.run_verification(_commitment(db, A_OPEN), _gmail())
    assert len(db.tables["evidence"]) == 1
    assert retry["evidence_id"] == first["evidence_id"]
    assert retry["status"] == "verified"
    assert _commitment_state(db, A_OPEN) == ("fulfilled", "verified")


@pytest.mark.asyncio
async def test_two_accounts_same_message_each_get_their_own_evidence(db, score):
    a = await verifier.run_verification(_commitment(db, A_OPEN), _gmail())
    b = await verifier.run_verification(_commitment(db, B_OPEN), _gmail())
    rows = {r["id"]: r["commitment_id"] for r in db.tables["evidence"]}
    assert rows == {a["evidence_id"]: A_OPEN, b["evidence_id"]: B_OPEN}
    assert a["status"] == b["status"] == "verified"


@pytest.mark.asyncio
async def test_event_already_stored_for_another_commitment_does_not_verify_this_one(db, score):
    """Per-account de-duplication: the same message is stored once per account.
    Verifying a second commitment with it must not succeed on evidence that is
    attached to the first."""
    await verifier.run_verification(_commitment(db, A_OPEN), _gmail())
    result = await verifier.run_verification(_commitment(db, A_OPEN_2), _gmail())
    assert result["status"] == "evidence_not_recorded"
    assert result["evidence_id"] is None
    assert _commitment_state(db, A_OPEN_2)[0] == "open"


# ---------------------------------------------------------------------------
# Through the HTTP route
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_route_reports_failed_write_without_an_evidence_id(test_client, db, score):
    db.fail_evidence_writes = True
    app.dependency_overrides[get_current_account] = lambda: Account(ACCOUNT_A, "a@example.test", ACCOUNT_A)
    try:
        resp = await test_client.post(f"/api/v1/commitments/{A_OPEN}/verify", json={"raw_event": _gmail()})
    finally:
        app.dependency_overrides.pop(get_current_account, None)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert (body["verification_status"], body["evidence_id"]) == ("evidence_not_recorded", None)
    assert db.row("commitments", A_OPEN)["status"] == "open"


# ---------------------------------------------------------------------------
# Migration 012 text guards (the SQL itself is not executed here)
# ---------------------------------------------------------------------------

MIGRATION_012 = pathlib.Path(__file__).resolve().parents[2] / "migrations" / "012_evidence_dedup_per_account.sql"


def _sql_body():
    return "\n".join(l for l in MIGRATION_012.read_text().splitlines() if not l.lstrip().startswith("--"))


def test_migration_012_sets_lock_timeout_before_dropping_the_index():
    body = _sql_body()
    assert re.search(r"SET\s+LOCAL\s+lock_timeout", body)
    assert body.index("lock_timeout") < body.index("DROP INDEX")
    assert body.index("BEGIN") < body.index("lock_timeout") and body.index("DROP INDEX") < body.index("COMMIT")


def test_migration_012_key_and_filter_mirror_the_api():
    """API: key only when external_system and external_event_id are both non-empty;
    key = sha256(f"{account}:{system}:{event_id}")."""
    body = re.sub(r"\s+", " ", _sql_body())
    assert "c.user_id::text || ':' || e.external_system || ':' || e.external_event_id" in body
    assert "e.external_system <> ''" in body and "e.external_event_id <> ''" in body
