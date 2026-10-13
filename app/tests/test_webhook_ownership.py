"""Webhook tenant isolation (P0-3c) – no DB, no network.

Ownership rule: a subscription belongs to the account that created it
(webhook_subscriptions.account_id, added by migration 011). Rows without an
owner (created before 011) are invisible to every account and never receive
deliveries. Events are delivered only to subscriptions of the account that
produced them.

The fake enforces partial unique indexes the way Postgres does. Endpoint
uniqueness is tested under the current global index ("pre") and the
per-account index migration 011 creates ("post"); this checks the
application against those modelled constraints, not the SQL itself.
Outbound HTTP is replaced by a recorder.
"""
import copy
import uuid

import pytest

import app.api.ingest as ingest_api
import app.api.webhooks as webhooks_api
from app.core.auth import Account, get_current_account
from app.main import app

ACCOUNT_A = "aaaaaaaa-0000-4000-8000-00000000000a"
ACCOUNT_B = "bbbbbbbb-0000-4000-8000-00000000000b"
A_HOOK = "a9000000-0000-4000-8000-000000000001"
B_HOOK = "b9000000-0000-4000-8000-000000000001"
ORPHAN_HOOK = "c9000000-0000-4000-8000-000000000001"
A_SECRET = "a-secret-only-in-this-test"
B_SECRET = "b-secret-only-in-this-test"
ORPHAN_SECRET = "orphan-secret-only-in-this-test"
A_URL = "https://hooks.a.example.test/cogext"
B_URL = "https://hooks.b.example.test/cogext"
ORPHAN_URL = "https://hooks.legacy.example.test/cogext"

PRE_011 = [("uq_webhook_endpoint_active", ("endpoint",))]
POST_011 = [("uq_webhook_account_endpoint_active", ("account_id", "endpoint"))]


class UniqueViolation(Exception):
    code = "23505"


class _Resp:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, db, table):
        self.db, self.table = db, table
        self.op, self.payload, self.filters, self.single = "select", None, [], False

    def select(self, *_a, **_k):
        self.op = "select"
        return self

    def insert(self, payload):
        self.op, self.payload = "insert", payload
        return self

    def update(self, payload):
        self.op, self.payload = "update", payload
        return self

    def upsert(self, payload, **_k):
        self.op, self.payload = "upsert", payload
        return self

    def eq(self, column, value):
        self.filters.append((column, value))
        return self

    def order(self, *_a, **_k):
        return self

    def limit(self, *_a, **_k):
        return self

    def maybe_single(self):
        self.single = True
        return self

    def _match(self, row):
        return all(str(row.get(c)) == str(v) for c, v in self.filters)

    def _check_unique(self, rows, candidate, ignore=None):
        if self.table != "webhook_subscriptions" or not candidate.get("active"):
            return
        for name, cols in self.db.endpoint_indexes:
            key = tuple(candidate.get(c) for c in cols)
            if None in key:
                continue          # NULLs never conflict
            for r in rows:
                if r is not ignore and r.get("active") and tuple(r.get(c) for c in cols) == key:
                    raise UniqueViolation(f"duplicate key value violates unique constraint {name!r}")

    async def execute(self):
        rows = self.db.tables.setdefault(self.table, [])
        if self.op == "select":
            hits = [copy.deepcopy(r) for r in rows if self._match(r)]
            self.db.selects.append((self.table, list(self.filters)))
            return _Resp((hits[0] if hits else None) if self.single else hits)
        if self.op in ("insert", "upsert"):
            if self.op == "upsert":
                existing = [r for r in rows if r.get("id") == self.payload.get("id")]
                if existing:
                    existing[0].update(copy.deepcopy(self.payload))
                    self.db.writes.append((self.op, self.table, copy.deepcopy(self.payload)))
                    return _Resp([copy.deepcopy(existing[0])])
            self._check_unique(rows, self.payload)
            rows.append(copy.deepcopy(self.payload))
            self.db.writes.append((self.op, self.table, copy.deepcopy(self.payload)))
            return _Resp([copy.deepcopy(self.payload)])
        hits = [r for r in rows if self._match(r)]
        for r in hits:
            self._check_unique(rows, {**r, **self.payload}, ignore=r)
        for r in hits:
            r.update(copy.deepcopy(self.payload))
        self.db.writes.append(("update", self.table, copy.deepcopy(self.payload)))
        return _Resp([copy.deepcopy(r) for r in hits])


def _hook(hid, owner, url, secret, types=None, active=True):
    return {"id": hid, "account_id": owner, "endpoint": url, "active": active,
            "subscribed_event_types": types or [], "secret_hash": secret, "failure_count": 0,
            "created_at": "2026-10-01T00:00:00+00:00", "updated_at": "2026-10-01T00:00:00+00:00",
            "last_delivery_at": None}


class WebhookDB:
    def __init__(self, endpoint_indexes):
        self.endpoint_indexes = endpoint_indexes
        self.tables = {
            "webhook_subscriptions": [
                _hook(A_HOOK, ACCOUNT_A, A_URL, A_SECRET),
                _hook(B_HOOK, ACCOUNT_B, B_URL, B_SECRET),
                _hook(ORPHAN_HOOK, None, ORPHAN_URL, ORPHAN_SECRET),   # pre-011 row, owner unknown
            ],
            "webhook_deliveries": [
                {"id": str(uuid.uuid4()), "event_id": str(uuid.uuid4()), "webhook_id": A_HOOK, "attempt": 1,
                 "status": "delivered", "response_code": 200, "truncated_response": "A-only response body",
                 "delivered_at": "2026-10-02T00:00:00+00:00", "next_retry_at": None},
            ],
        }
        self.writes, self.selects = [], []

    def table(self, name):
        return _Query(self, name)

    def hook(self, hid):
        return next(r for r in self.tables["webhook_subscriptions"] if r["id"] == hid)

    def snapshot(self):
        return copy.deepcopy(self.tables)


class Outbound:
    """Records webhook HTTP deliveries instead of sending them."""

    def __init__(self):
        self.posts = []

    def client(self, *_a, **_k):
        rec = self

        class _Client:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def post(self, url, content=None, headers=None, **_k):
                rec.posts.append({"url": url, "body": content, "headers": headers})

                class R:
                    status_code, text = 200, "ok"
                return R()
        return _Client()


@pytest.fixture
def db(monkeypatch, request):
    fake = WebhookDB(PRE_011 if getattr(request, "param", "post") == "pre" else POST_011)
    monkeypatch.setattr(webhooks_api, "get_supabase", lambda: fake)
    return fake


@pytest.fixture
def outbound(monkeypatch):
    rec = Outbound()
    monkeypatch.setattr(webhooks_api.httpx, "AsyncClient", rec.client)
    return rec


@pytest.fixture
def as_account():
    def _use(account_id):
        app.dependency_overrides[get_current_account] = lambda: Account(account_id, "x@example.test", account_id)
    yield _use
    app.dependency_overrides.pop(get_current_account, None)


NOT_FOUND = {"detail": "Webhook not found"}
A_ONLY = [A_HOOK, A_URL, A_SECRET, "A-only response body"]
SECRETS = [A_SECRET, B_SECRET, ORPHAN_SECRET]


def _ids(resp):
    return sorted(w["id"] for w in resp.json())


# ---------------------------------------------------------------------------
# Subscription management
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_records_the_owner_and_never_returns_the_secret(test_client, db, as_account, outbound):
    as_account(ACCOUNT_A)
    resp = await test_client.post("/api/v1/webhooks", json={
        "endpoint": "https://hooks.a.example.test/second", "subscribed_event_types": ["risk.high"],
        "secret": "new-a-secret-only-in-this-test"})
    assert resp.status_code == 200, resp.text
    assert "new-a-secret-only-in-this-test" not in resp.text
    assert db.hook(resp.json()["id"])["account_id"] == ACCOUNT_A


@pytest.mark.asyncio
async def test_list_returns_only_the_callers_subscriptions(test_client, db, as_account, outbound):
    as_account(ACCOUNT_A)
    a = await test_client.get("/api/v1/webhooks")
    as_account(ACCOUNT_B)
    b = await test_client.get("/api/v1/webhooks")
    assert _ids(a) == [A_HOOK] and _ids(b) == [B_HOOK]       # ownerless row listed to nobody
    assert not any(s in a.text + b.text for s in SECRETS)
    assert A_URL not in b.text and ORPHAN_URL not in a.text + b.text


@pytest.mark.asyncio
async def test_owner_reads_own_subscription_and_deliveries(test_client, db, as_account, outbound):
    as_account(ACCOUNT_A)
    hook = await test_client.get(f"/api/v1/webhooks/{A_HOOK}")
    logs = await test_client.get(f"/api/v1/webhooks/{A_HOOK}/deliveries")
    assert hook.status_code == logs.status_code == 200
    assert hook.json()["endpoint"] == A_URL and A_SECRET not in hook.text
    assert [d["webhook_id"] for d in logs.json()] == [A_HOOK]


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/api/v1/webhooks/{id}", "/api/v1/webhooks/{id}/deliveries"])
@pytest.mark.parametrize("target", [A_HOOK, ORPHAN_HOOK, "missing"])
async def test_other_or_ownerless_or_missing_is_404_without_data(test_client, db, as_account, outbound, path, target):
    as_account(ACCOUNT_B)
    hid = str(uuid.uuid4()) if target == "missing" else target
    resp = await test_client.get(path.format(id=hid))
    assert (resp.status_code, resp.json()) == (404, NOT_FOUND)
    assert not any(v in resp.text for v in A_ONLY + [ORPHAN_URL] + SECRETS)


@pytest.mark.asyncio
async def test_owner_updates_own_subscription(test_client, db, as_account, outbound):
    as_account(ACCOUNT_A)
    resp = await test_client.patch(f"/api/v1/webhooks/{A_HOOK}", json={
        "endpoint": "https://hooks.a.example.test/moved", "secret": "rotated-a-secret-only-here"})
    assert resp.status_code == 200, resp.text
    hook = db.hook(A_HOOK)
    assert (hook["endpoint"], hook["secret_hash"]) == ("https://hooks.a.example.test/moved", "rotated-a-secret-only-here")
    assert "rotated-a-secret-only-here" not in resp.text


@pytest.mark.asyncio
@pytest.mark.parametrize("target", [A_HOOK, ORPHAN_HOOK])
@pytest.mark.parametrize("change", [
    {"endpoint": "https://attacker.example.test/steal"},
    {"secret": "attacker-chosen-secret"},
    {"active": False},
    {"subscribed_event_types": ["risk.high"]},
])
async def test_other_account_cannot_change_a_subscription(test_client, db, as_account, outbound, target, change):
    as_account(ACCOUNT_B)
    before = db.snapshot()
    resp = await test_client.patch(f"/api/v1/webhooks/{target}", json=change)
    assert (resp.status_code, resp.json()) == (404, NOT_FOUND)
    assert db.snapshot() == before
    assert [w for w in db.writes if w[1] == "webhook_subscriptions"] == []


@pytest.mark.asyncio
async def test_owner_deactivates_own_subscription(test_client, db, as_account, outbound):
    as_account(ACCOUNT_A)
    resp = await test_client.delete(f"/api/v1/webhooks/{A_HOOK}")
    assert resp.status_code == 200
    assert resp.json() == {"deactivated": True, "webhook_id": A_HOOK}
    assert db.hook(A_HOOK)["active"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("target", [A_HOOK, ORPHAN_HOOK, "missing"])
async def test_other_account_cannot_delete(test_client, db, as_account, outbound, target):
    as_account(ACCOUNT_B)
    hid = str(uuid.uuid4()) if target == "missing" else target
    before = db.snapshot()
    resp = await test_client.delete(f"/api/v1/webhooks/{hid}")
    assert (resp.status_code, resp.json()) == (404, NOT_FOUND)
    assert db.snapshot() == before


@pytest.mark.asyncio
async def test_ssrf_check_still_applies(test_client, db, as_account, outbound):
    as_account(ACCOUNT_A)
    resp = await test_client.post("/api/v1/webhooks", json={"endpoint": "http://127.0.0.1/x", "secret": "s"})
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_two_accounts_can_use_the_same_endpoint_after_011(test_client, db, as_account, outbound):
    as_account(ACCOUNT_B)
    resp = await test_client.post("/api/v1/webhooks", json={"endpoint": A_URL, "secret": "b2-secret"})
    assert resp.status_code == 200, resp.text
    assert db.hook(resp.json()["id"])["account_id"] == ACCOUNT_B


@pytest.mark.asyncio
@pytest.mark.parametrize("db", ["pre"], indirect=True)
async def test_without_011_the_global_endpoint_index_blocks_another_account(test_client, db, as_account, outbound):
    """Documents why 011 replaces the global index: B gets a conflict caused by A's row."""
    as_account(ACCOUNT_B)
    before = db.snapshot()
    resp = await test_client.post("/api/v1/webhooks", json={"endpoint": A_URL, "secret": "b2-secret"})
    assert resp.status_code == 409
    assert A_SECRET not in resp.text
    assert db.snapshot() == before


@pytest.mark.asyncio
async def test_same_account_duplicate_active_endpoint_is_409(test_client, db, as_account, outbound):
    as_account(ACCOUNT_A)
    before = db.snapshot()
    resp = await test_client.post("/api/v1/webhooks", json={"endpoint": A_URL, "secret": "dup"})
    assert resp.status_code == 409
    assert db.snapshot() == before


@pytest.mark.asyncio
@pytest.mark.parametrize("method,path,body", [
    ("POST", "/api/v1/webhooks", {"endpoint": A_URL, "secret": "s"}),
    ("GET", "/api/v1/webhooks", None),
    ("GET", f"/api/v1/webhooks/{A_HOOK}", None),
    ("PATCH", f"/api/v1/webhooks/{A_HOOK}", {"active": False}),
    ("DELETE", f"/api/v1/webhooks/{A_HOOK}", None),
    ("GET", f"/api/v1/webhooks/{A_HOOK}/deliveries", None),
])
async def test_missing_authentication_is_rejected(test_client, db, outbound, method, path, body):
    app.dependency_overrides.pop(get_current_account, None)
    before = db.snapshot()
    resp = await test_client.request(method, path, json=body)
    assert resp.status_code == 401
    assert db.snapshot() == before
    assert not any(v in resp.text for v in A_ONLY + SECRETS)


# ---------------------------------------------------------------------------
# Delivery isolation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_event_is_delivered_only_to_the_producing_accounts_subscriptions(db, outbound):
    count = await webhooks_api.deliver_event(str(uuid.uuid4()), "risk.high",
                                            {"promise_text": "A-only promise"}, account_id=ACCOUNT_A)
    assert count == 1
    assert [p["url"] for p in outbound.posts] == [A_URL]
    logged = [w[2]["webhook_id"] for w in db.writes if w[1] == "webhook_deliveries"]
    assert logged and set(logged) == {A_HOOK}


@pytest.mark.asyncio
async def test_b_event_never_reaches_a_or_ownerless_endpoints(db, outbound):
    await webhooks_api.deliver_event(str(uuid.uuid4()), "contradiction.detected", {"x": 1}, account_id=ACCOUNT_B)
    assert [p["url"] for p in outbound.posts] == [B_URL]


@pytest.mark.asyncio
async def test_signature_uses_the_owning_subscriptions_secret(db, outbound):
    import hashlib
    import hmac
    await webhooks_api.deliver_event("evt-1", "risk.high", {"k": "v"}, account_id=ACCOUNT_A)
    [post] = outbound.posts
    expected = hmac.new(A_SECRET.encode(), post["body"], hashlib.sha256).hexdigest()
    assert post["headers"]["X-COGEXT-Signature"] == f"sha256={expected}"


@pytest.mark.asyncio
async def test_event_type_filter_still_applies_within_the_account(db, outbound):
    db.hook(A_HOOK)["subscribed_event_types"] = ["contradiction.detected"]
    assert await webhooks_api.deliver_event("e", "risk.high", {}, account_id=ACCOUNT_A) == 0
    assert await webhooks_api.deliver_event("e", "contradiction.detected", {}, account_id=ACCOUNT_A) == 1


@pytest.mark.asyncio
async def test_inactive_subscription_gets_nothing(db, outbound):
    db.hook(A_HOOK)["active"] = False
    assert await webhooks_api.deliver_event("e", "risk.high", {}, account_id=ACCOUNT_A) == 0
    assert outbound.posts == []


@pytest.mark.asyncio
@pytest.mark.parametrize("account_id", ["", None])
async def test_event_without_an_account_is_delivered_nowhere(db, outbound, account_id):
    assert await webhooks_api.deliver_event("e", "risk.high", {}, account_id=account_id) == 0
    assert outbound.posts == [] and db.selects == []


@pytest.mark.asyncio
async def test_deliver_event_cannot_be_called_unscoped(db, outbound):
    with pytest.raises(TypeError):
        await webhooks_api.deliver_event("e", "risk.high", {})
    assert outbound.posts == []


# ---------------------------------------------------------------------------
# Ingest passes the producing account
# ---------------------------------------------------------------------------

@pytest.fixture
def ingest_events(monkeypatch):
    """Run ingest with an in-memory store and capture deliver_event calls."""
    from app.tests.test_tenant_dedup_isolation import EVIDENCE_POST, EXTRACTED, ConstraintDB

    store = ConstraintDB(EVIDENCE_POST)
    store.tables["commitments"] = []
    monkeypatch.setattr(ingest_api, "get_supabase", lambda: store)
    monkeypatch.setattr("app.core.extractor.extract_completion", lambda _p: EXTRACTED)

    async def _contradiction(commitment, user_id):
        return [{"old_id": "old", "new_id": str(commitment.id), "reason": "r",
                 "old_promise": "p0", "new_promise": "p1"}]

    async def _high_risk(commitment, user_id):
        return 0.99, ["test"]

    calls = []

    async def _deliver(event_id, event_type, payload, *, account_id):
        calls.append((event_type, account_id, payload.get("user_id")))
        return 1

    monkeypatch.setattr(ingest_api, "detect_contradictions", _contradiction)
    monkeypatch.setattr(ingest_api, "score_risk", _high_risk)
    monkeypatch.setattr(ingest_api, "deliver_event", _deliver)
    return calls


@pytest.mark.asyncio
@pytest.mark.parametrize("account", [ACCOUNT_A, ACCOUNT_B])
async def test_ingest_delivers_events_scoped_to_the_caller(test_client, as_account, ingest_events, account):
    as_account(account)
    resp = await test_client.post("/api/v1/ingest", json={
        "source_agent_id": "5a5a5a5a-0000-4000-8000-000000000000", "message": "I will send the report."})
    assert resp.status_code == 200, resp.text
    assert sorted(ingest_events) == sorted([
        ("contradiction.detected", account, account),
        ("risk.high", account, account),
    ])
