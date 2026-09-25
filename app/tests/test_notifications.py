"""Tests for signup notifications (app.core.signup_notifications) and the
idempotency guard in the POST /keys/signup handler.

These tests are hermetic: every test sets the SMTP settings it depends on via
monkeypatch, so a populated local .env can't change the outcome.
"""
import asyncio
import logging
import smtplib
import threading
import time
from datetime import datetime, timezone
from unittest.mock import Mock

import pytest
from fastapi import BackgroundTasks
from starlette.requests import Request

import app.api.keys as keys_module
from app.api.keys import SignupRequest
from app.core.signup_notifications import Notifier, notifier
from config import settings


# ── Helpers ──────────────────────────────────────────────────────────────────

class _FakeSMTP:
    """Stand-in for smtplib.SMTP_SSL usable as a context manager."""

    instances: list["_FakeSMTP"] = []

    def __init__(self, host, port):
        self.host = host
        self.port = port
        self.login = Mock()
        self.send_message = Mock()
        _FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


@pytest.fixture
def smtp_configured(monkeypatch):
    """Point settings at a working SMTP account and capture SMTP_SSL calls."""
    _FakeSMTP.instances = []
    monkeypatch.setattr(settings, "SMTP_USER", "bot@cogextai.com")
    monkeypatch.setattr(settings, "SMTP_PASSWORD", "app-password")
    monkeypatch.setattr(settings, "NOTIFY_EMAIL", "hello@cogextai.com")
    monkeypatch.setattr(settings, "SMTP_HOST", "smtp.gmail.com")
    monkeypatch.setattr(settings, "SMTP_PORT", 465)
    monkeypatch.setattr(smtplib, "SMTP_SSL", _FakeSMTP)
    return _FakeSMTP


def _make_request(host: str = "203.0.113.7") -> Request:
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/v1/keys/signup",
        "headers": [],
        "client": (host, 12345),
        "server": ("testserver", 80),
        "scheme": "http",
        "query_string": b"",
    }
    return Request(scope)


class _FakeResponse:
    def __init__(self, data):
        self.data = data


class _FakeQuery:
    """Minimal postgrest-style query builder backed by an in-memory list."""

    def __init__(self, rows):
        self._rows = rows
        self._filters: list[tuple[str, object]] = []
        self._insert_payload = None
        self._maybe_single = False

    def select(self, *_args, **_kwargs):
        return self

    def eq(self, column, value):
        self._filters.append((column, value))
        return self

    def maybe_single(self):
        self._maybe_single = True
        return self

    def insert(self, payload):
        self._insert_payload = payload
        return self

    async def execute(self):
        if self._insert_payload is not None:
            n = len(self._rows) + 1
            row = {
                "id": f"00000000-0000-0000-0000-{n:012d}",
                "account_id": f"11111111-1111-1111-1111-{n:012d}",
                "is_active": True,  # mirrors the DB default
                "created_at": datetime.now(timezone.utc).isoformat(),
                **self._insert_payload,
            }
            self._rows.append(row)
            return _FakeResponse([row])

        matches = [
            r
            for r in self._rows
            if all(r.get(col) == val for col, val in self._filters)
        ]
        if self._maybe_single:
            return _FakeResponse(matches[0] if matches else None)
        return _FakeResponse(matches)


class _FakeSupabase:
    def __init__(self):
        self.rows: list[dict] = []

    def table(self, _name):
        return _FakeQuery(self.rows)


# ── Notifier unit tests ──────────────────────────────────────────────────────

async def test_notify_no_config(monkeypatch):
    """Empty SMTP_USER/PASSWORD → skip quietly, no exception, no SMTP call."""
    smtp_ssl = Mock()
    monkeypatch.setattr(settings, "SMTP_USER", "")
    monkeypatch.setattr(settings, "SMTP_PASSWORD", "")
    monkeypatch.setattr(settings, "NOTIFY_EMAIL", "hello@cogextai.com")
    monkeypatch.setattr(smtplib, "SMTP_SSL", smtp_ssl)

    await Notifier().notify_signup(
        email="new@example.com",
        account_id="acct-1",
        key_id="key-1",
    )

    smtp_ssl.assert_not_called()


async def test_notify_no_config_missing_password_only(monkeypatch):
    """A user without a password is still 'not configured'."""
    smtp_ssl = Mock()
    monkeypatch.setattr(settings, "SMTP_USER", "bot@cogextai.com")
    monkeypatch.setattr(settings, "SMTP_PASSWORD", "")
    monkeypatch.setattr(smtplib, "SMTP_SSL", smtp_ssl)

    await Notifier().notify_signup(
        email="new@example.com", account_id="acct-1", key_id="key-1"
    )

    smtp_ssl.assert_not_called()


async def test_notify_skips_when_notify_email_empty(monkeypatch):
    """Missing NOTIFY_EMAIL is also a quiet skip."""
    smtp_ssl = Mock()
    monkeypatch.setattr(settings, "SMTP_USER", "bot@cogextai.com")
    monkeypatch.setattr(settings, "SMTP_PASSWORD", "app-password")
    monkeypatch.setattr(settings, "NOTIFY_EMAIL", "")
    monkeypatch.setattr(smtplib, "SMTP_SSL", smtp_ssl)

    await Notifier().notify_signup(
        email="new@example.com", account_id="acct-1", key_id="key-1"
    )

    smtp_ssl.assert_not_called()


async def test_notify_sends_email(smtp_configured):
    """Happy path: one SMTP session, one send_message, correct recipient."""
    await notifier.notify_signup(
        email="new@example.com",
        account_id="acct-1",
        key_id="abcdef1234567890",
        request_ip="203.0.113.7",
        country="NL",
    )

    assert len(smtp_configured.instances) == 1
    smtp = smtp_configured.instances[0]

    smtp.login.assert_called_once_with("bot@cogextai.com", "app-password")
    smtp.send_message.assert_called_once()

    msg = smtp.send_message.call_args[0][0]
    assert msg["To"] == "hello@cogextai.com"
    assert msg["From"] == "bot@cogextai.com"
    assert "new@example.com" in msg["Subject"]

    body = msg.get_payload()[0].get_payload(decode=True).decode()
    assert "new@example.com" in body
    assert "acct-1" in body
    assert "abcdef12..." in body
    assert "203.0.113.7" in body


async def test_notify_uses_configured_host_and_port(smtp_configured, monkeypatch):
    monkeypatch.setattr(settings, "SMTP_HOST", "smtp.example.com")
    monkeypatch.setattr(settings, "SMTP_PORT", 2525)

    await notifier.notify_signup(
        email="new@example.com", account_id="acct-1", key_id="key-1"
    )

    smtp = smtp_configured.instances[0]
    assert (smtp.host, smtp.port) == ("smtp.example.com", 2525)


async def test_notify_handles_failure(smtp_configured, monkeypatch):
    """A raising SMTP_SSL must not propagate out of notify_signup."""

    def _boom(host, port):
        raise smtplib.SMTPException("connection refused")

    monkeypatch.setattr(smtplib, "SMTP_SSL", _boom)

    # Must not raise.
    await notifier.notify_signup(
        email="new@example.com", account_id="acct-1", key_id="key-1"
    )


async def test_notify_handles_login_failure(smtp_configured, monkeypatch):
    """A failure after connecting is swallowed too."""
    smtp_configured.instances = []

    class _BrokenLogin(_FakeSMTP):
        def login(self, user, password):
            raise smtplib.SMTPAuthenticationError(535, b"bad credentials")

    monkeypatch.setattr(smtplib, "SMTP_SSL", _BrokenLogin)

    await notifier.notify_signup(
        email="new@example.com", account_id="acct-1", key_id="key-1"
    )


async def test_notify_logs_failure(smtp_configured, monkeypatch, caplog):
    """Failures are logged at ERROR so they're visible in production."""

    def _boom(host, port):
        raise smtplib.SMTPException("connection refused")

    monkeypatch.setattr(smtplib, "SMTP_SSL", _boom)

    with caplog.at_level(logging.ERROR):
        await notifier.notify_signup(
            email="new@example.com", account_id="acct-1", key_id="key-1"
        )

    assert any("Failed to send signup notification" in r.message for r in caplog.records)


# ── Signup handler: notification is queued, never awaited inline ─────────────

def _handler():
    """The undecorated signup coroutine (bypasses the slowapi rate limiter)."""
    return getattr(keys_module.signup, "__wrapped__", keys_module.signup)


async def _call_signup(
    email: str,
    background_tasks: BackgroundTasks,
    request: Request | None = None,
):
    """Invoke the handler using FastAPI's keyword-style injection."""
    return await _handler()(
        body=SignupRequest(email=email),
        background_tasks=background_tasks,
        request=request or _make_request(),
    )


@pytest.fixture
def notify_spy(monkeypatch):
    """Capture notifier.notify_signup calls without sending anything."""
    calls: list[dict] = []

    async def _fake_notify(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(notifier, "notify_signup", _fake_notify, raising=False)
    return calls


async def test_signup_returns_before_notification_runs(monkeypatch, notify_spy):
    """The regression: the handler must not await the SMTP round-trip."""
    sb = _FakeSupabase()
    monkeypatch.setattr(keys_module, "get_supabase", lambda: sb)

    background_tasks = BackgroundTasks()
    response = await _call_signup("new@example.com", background_tasks)

    # The response is fully built while the send is still only queued.
    assert response.api_key.startswith("cg_live_")
    assert notify_spy == []
    assert len(background_tasks.tasks) == 1

    # Draining the queue is what actually performs the send.
    await background_tasks()

    assert len(notify_spy) == 1
    assert notify_spy[0]["email"] == "new@example.com"
    assert notify_spy[0]["request_ip"] == "203.0.113.7"


async def test_signup_notifies_once_on_repeat_signup(monkeypatch, notify_spy):
    """Signing up twice with the same email queues exactly one notification."""
    sb = _FakeSupabase()
    monkeypatch.setattr(keys_module, "get_supabase", lambda: sb)

    background_tasks = BackgroundTasks()
    first = await _call_signup("new@example.com", background_tasks)
    second = await _call_signup("new@example.com", background_tasks)

    assert len(background_tasks.tasks) == 1

    await background_tasks()

    assert len(notify_spy) == 1

    # Idempotent: the same key comes back, and only one row was ever inserted.
    assert first.api_key == second.api_key
    assert len(sb.rows) == 1


async def test_signup_notifies_once_for_distinct_emails(monkeypatch, notify_spy):
    """Two different emails → two separate notifications."""
    sb = _FakeSupabase()
    monkeypatch.setattr(keys_module, "get_supabase", lambda: sb)

    background_tasks = BackgroundTasks()
    await _call_signup("a@example.com", background_tasks)
    await _call_signup("b@example.com", background_tasks)

    await background_tasks()

    assert [c["email"] for c in notify_spy] == ["a@example.com", "b@example.com"]


async def test_signup_response_unaffected_by_notifier_failure(monkeypatch):
    """A raising notifier still yields a valid response.

    The error surfaces only when the queue drains — i.e. after the client has
    already been answered. Starlette does not swallow background exceptions.
    """
    sb = _FakeSupabase()
    monkeypatch.setattr(keys_module, "get_supabase", lambda: sb)

    async def _boom(**_kwargs):
        raise RuntimeError("smtp exploded")

    monkeypatch.setattr(notifier, "notify_signup", _boom, raising=False)

    background_tasks = BackgroundTasks()
    response = await _call_signup("new@example.com", background_tasks)

    assert response.email == "new@example.com"
    assert response.api_key.startswith("cg_live_")

    with pytest.raises(RuntimeError):
        await background_tasks()


async def test_signup_does_not_notify_when_row_has_no_created_at(
    monkeypatch, notify_spy
):
    """If created_at is absent we can't prove it's new → stay silent."""
    sb = _FakeSupabase()
    monkeypatch.setattr(keys_module, "get_supabase", lambda: sb)

    original_execute = _FakeQuery.execute

    async def _execute_without_created_at(self):
        response = await original_execute(self)
        for row in response.data or []:
            row.pop("created_at", None)
        return response

    monkeypatch.setattr(_FakeQuery, "execute", _execute_without_created_at)

    background_tasks = BackgroundTasks()
    await _call_signup("new@example.com", background_tasks)
    await background_tasks()

    assert notify_spy == []
    assert background_tasks.tasks == []


async def test_signup_survives_unparseable_created_at(monkeypatch, notify_spy, caplog):
    """A malformed created_at must not cost the user their API key."""
    sb = _FakeSupabase()
    monkeypatch.setattr(keys_module, "get_supabase", lambda: sb)

    original_execute = _FakeQuery.execute

    async def _execute_malformed(self):
        response = await original_execute(self)
        for row in response.data or []:
            row["created_at"] = "not-a-timestamp"
        return response

    monkeypatch.setattr(_FakeQuery, "execute", _execute_malformed)

    background_tasks = BackgroundTasks()
    with caplog.at_level(logging.WARNING):
        response = await _call_signup("new@example.com", background_tasks)

    # Signup still succeeds and returns a usable key.
    assert response.api_key.startswith("cg_live_")
    assert response.email == "new@example.com"

    # Nothing was queued, and the reason was logged.
    assert background_tasks.tasks == []
    await background_tasks()
    assert notify_spy == []
    assert any("Could not parse created_at" in r.message for r in caplog.records)


async def test_signup_does_not_notify_for_stale_row(monkeypatch, notify_spy):
    """A row older than the 10s window is treated as a re-signup."""
    from datetime import timedelta

    sb = _FakeSupabase()
    monkeypatch.setattr(keys_module, "get_supabase", lambda: sb)

    original_execute = _FakeQuery.execute

    async def _execute_stale(self):
        response = await original_execute(self)
        stale = (datetime.now(timezone.utc) - timedelta(seconds=60)).isoformat()
        for row in response.data or []:
            row["created_at"] = stale
        return response

    monkeypatch.setattr(_FakeQuery, "execute", _execute_stale)

    background_tasks = BackgroundTasks()
    await _call_signup("new@example.com", background_tasks)
    await background_tasks()

    assert notify_spy == []
    assert background_tasks.tasks == []


# ── Blocking SMTP must not stall the event loop ──────────────────────────────

async def test_send_email_async_runs_in_worker_thread(monkeypatch):
    """_send_email executes off the event-loop thread."""
    seen: dict = {}

    def _record(subject, html):
        seen["thread"] = threading.current_thread()

    instance = Notifier()
    monkeypatch.setattr(instance, "_send_email", _record)

    await instance._send_email_async("subject", "<html></html>")

    assert "thread" in seen
    assert seen["thread"] is not threading.current_thread()


async def test_send_email_async_keeps_event_loop_responsive(monkeypatch):
    """A slow SMTP send must not block other coroutines."""
    instance = Notifier()
    monkeypatch.setattr(instance, "_send_email", lambda s, h: time.sleep(0.2))

    ticks = 0

    async def _ticker():
        nonlocal ticks
        while True:
            await asyncio.sleep(0.01)
            ticks += 1

    task = asyncio.create_task(_ticker())
    try:
        await instance._send_email_async("subject", "<html></html>")
    finally:
        task.cancel()

    # A direct (blocking) call would have starved the loop: ticks would be 0.
    assert ticks >= 5


# ── Regression: the response must be flushed before SMTP runs ────────────────

async def test_response_flushed_before_smtp_over_real_asgi_stack(monkeypatch):
    """End-to-end through FastAPI + slowapi + BackgroundTasks.

    Guards the reported regression: the client used to hang on
    "Generating key..." because the handler awaited the SMTP round-trip.
    """
    import json

    from fastapi import FastAPI
    from slowapi import _rate_limit_exceeded_handler
    from slowapi.errors import RateLimitExceeded
    from slowapi.middleware import SlowAPIMiddleware

    from app.core.rate_limit import limiter

    sb = _FakeSupabase()
    monkeypatch.setattr(keys_module, "get_supabase", lambda: sb)

    order: list[str] = []

    class _RecordingSMTP:
        def __init__(self, host, port):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            return False

        def login(self, user, password):
            pass

        def send_message(self, msg):
            order.append("SMTP")

    monkeypatch.setattr(smtplib, "SMTP_SSL", _RecordingSMTP)
    monkeypatch.setattr(settings, "SMTP_USER", "bot@cogextai.com")
    monkeypatch.setattr(settings, "SMTP_PASSWORD", "app-password")
    monkeypatch.setattr(settings, "NOTIFY_EMAIL", "hello@cogextai.com")

    app = FastAPI()
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    app.add_middleware(SlowAPIMiddleware)
    app.include_router(keys_module.router, prefix="/api/v1")

    body = json.dumps({"email": "brand-new@example.com"}).encode()
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/api/v1/keys/signup",
        "raw_path": b"/api/v1/keys/signup",
        "query_string": b"",
        "headers": [
            (b"host", b"testserver"),
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode()),
        ],
        "client": ("203.0.113.9", 5555),
        "server": ("testserver", 80),
        "root_path": "",
    }

    captured: dict = {}

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message):
        if message["type"] == "http.response.start":
            captured["status"] = message["status"]
        elif message["type"] == "http.response.body":
            order.append("RESPONSE")
            captured["body"] = captured.get("body", b"") + message.get("body", b"")

    await app(scope, receive, send)

    assert captured["status"] == 200
    assert json.loads(captured["body"])["api_key"].startswith("cg_live_")

    # Every response chunk precedes the SMTP send. (slowapi's
    # BaseHTTPMiddleware may emit more than one body message.)
    assert order.count("SMTP") == 1
    response_indexes = [i for i, event in enumerate(order) if event == "RESPONSE"]
    assert response_indexes, "no response body was ever sent"
    assert max(response_indexes) < order.index("SMTP")

