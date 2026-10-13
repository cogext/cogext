"""GET /calibration is scoped to the authenticated account (no DB, no network).

Accounts A and B have different commitment confidences and outcomes in an
in-memory fake. A report must reflect only the caller's own rows; naming
another account's user_id is refused without returning any data.
"""
import uuid

import pytest

import app.core.calibration as calibration_core
from app.core.auth import Account, get_current_account
from app.main import app
from app.tests.test_commitment_ownership import ACCOUNT_A, ACCOUNT_B, FakeDB


def _row(owner, confidence, status):
    return {"id": str(uuid.uuid4()), "user_id": owner, "confidence": confidence,
            "status": status, "extraction_model": "test"}


@pytest.fixture
def db(monkeypatch):
    fake = FakeDB()
    fake.tables["commitments"] = [
        _row(ACCOUNT_A, 0.95, "fulfilled"),
        _row(ACCOUNT_A, 0.95, "failed"),
        _row(ACCOUNT_A, 0.55, "open"),
        _row(ACCOUNT_B, 0.65, "contradicted"),   # buckets start at 0.50 (core/calibration.py)
        _row(ACCOUNT_B, 0.65, "contradicted"),
        _row(ACCOUNT_B, 0.65, "fulfilled"),
        _row(ACCOUNT_B, 0.75, "fulfilled"),
    ]
    monkeypatch.setattr(calibration_core, "get_supabase", lambda: fake)
    return fake


@pytest.fixture
def as_account():
    def _use(account_id):
        app.dependency_overrides[get_current_account] = lambda: Account(account_id, "x@example.test", account_id)
    yield _use
    app.dependency_overrides.pop(get_current_account, None)


def _populated(report):
    return {label: (b["sample_count"], b["fulfilled_rate"], b["contradiction_rate"])
            for label, b in report["buckets"].items() if b["sample_count"]}


@pytest.mark.asyncio
async def test_report_covers_only_the_callers_commitments(test_client, db, as_account):
    as_account(ACCOUNT_A)
    resp = await test_client.get("/api/v1/calibration")
    assert resp.status_code == 200, resp.text
    report = resp.json()
    assert report["total_rows"] == 3
    populated = _populated(report)
    assert sum(n for n, _, _ in populated.values()) == 3
    assert all(c == 0.0 for _, _, c in populated.values())          # B's contradictions not counted
    assert len(populated) == 2                                       # A has two confidence bands


@pytest.mark.asyncio
async def test_b_sees_only_its_own_report(test_client, db, as_account):
    as_account(ACCOUNT_B)
    report = (await test_client.get("/api/v1/calibration")).json()
    assert report["total_rows"] == 4
    assert any(c > 0 for _, _, c in _populated(report).values())


@pytest.mark.asyncio
async def test_own_user_id_parameter_still_works(test_client, db, as_account):
    as_account(ACCOUNT_A)
    default = (await test_client.get("/api/v1/calibration")).json()
    explicit = await test_client.get("/api/v1/calibration", params={"user_id": ACCOUNT_A})
    assert explicit.status_code == 200
    assert _populated(explicit.json()) == _populated(default)
    assert explicit.json()["total_rows"] == default["total_rows"]


@pytest.mark.asyncio
@pytest.mark.parametrize("other", [ACCOUNT_B, str(uuid.uuid4())])
async def test_other_user_id_is_refused_without_data(test_client, db, as_account, other):
    as_account(ACCOUNT_A)
    before = db.snapshot()
    resp = await test_client.get("/api/v1/calibration", params={"user_id": other})
    assert resp.status_code == 403
    assert "buckets" not in resp.text and "total_rows" not in resp.text
    assert db.snapshot() == before and db.writes == []


@pytest.mark.asyncio
async def test_account_without_commitments_gets_an_empty_report(test_client, db, as_account):
    as_account(str(uuid.uuid4()))
    report = (await test_client.get("/api/v1/calibration")).json()
    assert report["total_rows"] == 0 and _populated(report) == {}


@pytest.mark.asyncio
async def test_missing_authentication_is_rejected(test_client, db):
    app.dependency_overrides.pop(get_current_account, None)
    resp = await test_client.get("/api/v1/calibration")
    assert resp.status_code == 401
    assert "buckets" not in resp.text
