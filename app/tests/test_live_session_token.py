"""Tests for per-session write tokens on the public /live/* routes.

Hermetic: handlers are called directly against an in-memory fake of the
live_sessions table. The fake honours the column list passed to select(), so
the "never return session_token_hash" tests exercise the real select strings.
"""
import hashlib

import pytest
from fastapi import HTTPException
from postgrest.exceptions import APIError

import app.api.live as live_module
from app.api.live import (
    EventLog,
    PublishRequest,
    SessionCreate,
    create_session,
    get_session,
    log_event,
    publish,
    square,
)


class _Result:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, table):
        self._table = table
        self._columns = None
        self._filters: list[tuple[str, object]] = []
        self._insert = None
        self._update = None

    # builder -----------------------------------------------------------
    def select(self, columns="*"):
        self._columns = columns
        return self

    def insert(self, payload, **_kwargs):
        self._insert = payload
        return self

    def update(self, payload, **_kwargs):
        self._update = payload
        return self

    def eq(self, column, value):
        self._filters.append((column, value))
        return self

    def order(self, *_args, **_kwargs):
        return self

    def limit(self, *_args, **_kwargs):
        return self

    # execution ---------------------------------------------------------
    def _matches(self):
        return [r for r in self._table.rows if all(r.get(c) == v for c, v in self._filters)]

    def _project(self, row):
        if self._columns in (None, "*"):
            return dict(row)
        return {c.strip(): row.get(c.strip()) for c in self._columns.split(",")}

    async def execute(self):
        if self._insert is not None:
            sid = self._insert["session_id"]
            if any(r["session_id"] == sid for r in self._table.rows):
                raise APIError({
                    "message": 'duplicate key value violates unique constraint "live_sessions_pkey"',
                    "code": "23505", "hint": None, "details": None,
                })
            row = {"events": [], "published": False, "published_at": None, **self._insert}
            self._table.rows.append(row)
            return _Result([row])
        if self._update is not None:
            for r in self._matches():
                r.update(self._update)
            return _Result([])
        return _Result([self._project(r) for r in self._matches()])


class _Table:
    def __init__(self):
        self.rows: list[dict] = []


class _RPC:
    def __init__(self, table, params):
        self._table = table
        self._params = params

    async def execute(self):
        for r in self._table.rows:
            if r["session_id"] == self._params["p_session_id"]:
                r["events"] = [*r["events"], self._params["p_event"]]
        return _Result(None)


class _FakeSupabase:
    def __init__(self):
        self.sessions = _Table()

    def table(self, name):
        assert name == "live_sessions"
        return _Query(self.sessions)

    def rpc(self, name, params):
        assert name == "live_append_event"
        return _RPC(self.sessions, params)


@pytest.fixture
def sb(monkeypatch):
    fake = _FakeSupabase()
    monkeypatch.setattr(live_module, "get_supabase", lambda: fake)
    return fake


async def _new_session(session_id="sess0001") -> str:
    return (await create_session(SessionCreate(session_id=session_id)))["publish_token"]


async def _status_of(coro) -> int:
    with pytest.raises(HTTPException) as exc_info:
        await coro
    return exc_info.value.status_code


# ── POST /live/session ──────────────────────────────────────────────────────

async def test_create_session_returns_token_and_stores_only_its_hash(sb):
    result = await create_session(SessionCreate(session_id="sess0001"))

    token = result["publish_token"]
    assert isinstance(token, str) and len(token) >= 32
    row = sb.sessions.rows[0]
    assert row["session_token_hash"] == hashlib.sha256(token.encode()).hexdigest()
    assert token not in row.values()


async def test_duplicate_session_id_returns_409_without_token(sb):
    await _new_session("sess0001")

    with pytest.raises(HTTPException) as exc_info:
        await create_session(SessionCreate(session_id="sess0001"))

    assert exc_info.value.status_code == 409
    assert len(sb.sessions.rows) == 1


# ── POST /live/event ────────────────────────────────────────────────────────

async def test_event_without_token_returns_403(sb):
    await _new_session()
    assert await _status_of(log_event(EventLog(session_id="sess0001", event={"k": 1}))) == 403
    assert sb.sessions.rows[0]["events"] == []


async def test_event_with_wrong_token_returns_403(sb):
    await _new_session()
    body = EventLog(session_id="sess0001", event={"k": 1}, publish_token="not-the-token")
    assert await _status_of(log_event(body)) == 403
    assert sb.sessions.rows[0]["events"] == []


async def test_event_with_correct_token_is_appended(sb):
    token = await _new_session()
    await log_event(EventLog(session_id="sess0001", event={"k": 1}, publish_token=token))
    assert sb.sessions.rows[0]["events"] == [{"k": 1}]


async def test_event_for_missing_session_returns_404(sb):
    body = EventLog(session_id="nope", event={"k": 1}, publish_token="x")
    assert await _status_of(log_event(body)) == 404


async def test_grandfathered_session_without_hash_returns_410(sb):
    sb.sessions.rows.append({
        "session_id": "legacy01", "created_at": "2026-10-01T00:00:00+00:00",
        "events": [], "published": False, "published_at": None, "session_token_hash": None,
    })
    body = EventLog(session_id="legacy01", event={"k": 1}, publish_token="anything")
    assert await _status_of(log_event(body)) == 410
    assert await _status_of(publish(PublishRequest(session_id="legacy01", publish_token="anything"))) == 410


# ── POST /live/publish ──────────────────────────────────────────────────────

async def test_publish_without_or_with_wrong_token_returns_403(sb):
    await _new_session()
    assert await _status_of(publish(PublishRequest(session_id="sess0001"))) == 403
    assert await _status_of(publish(PublishRequest(session_id="sess0001", publish_token="bad"))) == 403
    assert sb.sessions.rows[0]["published"] is False


async def test_publish_with_correct_token_publishes(sb):
    token = await _new_session()
    result = await publish(PublishRequest(session_id="sess0001", publish_token=token))
    assert result["ok"] is True
    assert sb.sessions.rows[0]["published"] is True


# ── public reads never expose the hash ──────────────────────────────────────

async def test_get_session_does_not_return_token_hash(sb):
    await _new_session()
    session = await get_session("sess0001")
    assert "session_token_hash" not in session
    assert set(session) == {"session_id", "created_at", "events", "published", "published_at"}


async def test_square_does_not_return_token_hash(sb):
    token = await _new_session()
    await publish(PublishRequest(session_id="sess0001", publish_token=token))

    result = await square()

    assert len(result["sessions"]) == 1
    assert "session_token_hash" not in result["sessions"][0]
