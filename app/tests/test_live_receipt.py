"""Tests for POST /live/receipt write-once behaviour.

Hermetic: the handler is called directly against an in-memory fake that mimics
PostgREST's primary-key violation, so no database is needed.
"""
import pytest
from fastapi import HTTPException
from postgrest.exceptions import APIError

import app.api.live as live_module
from app.api.live import Receipt, store_receipt


class _FakeInsert:
    def __init__(self, table, payload):
        self._table = table
        self._payload = payload

    async def execute(self):
        if self._table.error is not None:
            raise self._table.error
        rid = self._payload["receipt_id"]
        if rid in self._table.rows:
            raise APIError({
                "message": 'duplicate key value violates unique constraint "live_receipts_pkey"',
                "code": "23505",
                "hint": None,
                "details": f"Key (receipt_id)=({rid}) already exists.",
            })
        self._table.rows[rid] = self._payload
        return None


class _FakeTable:
    def __init__(self):
        self.rows: dict[str, dict] = {}
        self.error: APIError | None = None

    def insert(self, payload):
        return _FakeInsert(self, payload)

    def upsert(self, payload):  # pragma: no cover - must never be called
        raise AssertionError("store_receipt must not upsert")


class _FakeSupabase:
    def __init__(self):
        self.receipts = _FakeTable()

    def table(self, name):
        assert name == "live_receipts"
        return self.receipts


def _receipt(**overrides) -> Receipt:
    data = {
        "receipt_id": "0123456789abcdef",
        "session_id": "a1b2c3d4",
        "tool": "send_email",
        "tool_response": {"http_status": 500, "body": {"error": "x"}},
        "agent_claim": "Email sent.",
        "verdict": "MISMATCH",
        "reason": "Agent claimed success, tool returned failure",
        "recorded_at": "2026-10-12T00:00:00+00:00",
        "signature": "00" * 32,
        "verify_url": "https://cogextai.com/r/0123456789abcdef",
    }
    data.update(overrides)
    return Receipt(**data)


@pytest.fixture
def sb(monkeypatch):
    fake = _FakeSupabase()
    monkeypatch.setattr(live_module, "get_supabase", lambda: fake)
    return fake


async def test_new_receipt_is_stored(sb):
    result = await store_receipt(_receipt())

    assert result == {"ok": True, "verify_url": "https://cogextai.com/r/0123456789abcdef"}
    assert "0123456789abcdef" in sb.receipts.rows


async def test_duplicate_receipt_id_is_rejected_and_original_kept(sb):
    await store_receipt(_receipt(agent_claim="Email sent."))

    with pytest.raises(HTTPException) as exc_info:
        await store_receipt(_receipt(agent_claim="OVERWRITTEN"))

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == "receipt_id already exists"
    # The stored receipt is untouched.
    assert sb.receipts.rows["0123456789abcdef"]["agent_claim"] == "Email sent."


async def test_other_database_errors_are_not_masked_as_409(sb):
    sb.receipts.error = APIError({"message": "boom", "code": "57014", "hint": None, "details": None})

    with pytest.raises(APIError) as exc_info:
        await store_receipt(_receipt())

    assert exc_info.value.code == "57014"
