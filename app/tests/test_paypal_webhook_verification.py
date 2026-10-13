"""PayPal webhook signature verification (no DB, no network, no PayPal).

Every webhook must carry all five PayPal transmission headers and be confirmed
by PayPal's verify-webhook-signature API, called with an OAuth access token.
Only verification_status == "SUCCESS" lets the event through. PayPal and the
provisioning/email side effects are replaced by recorders; credentials below
are synthetic.
"""
import json

import pytest

import app.api.paypal_webhook as paypal

URL = "/api/v1/webhooks/paypal"
HEADERS = {
    "paypal-auth-algo": "SHA256withRSA",
    "paypal-cert-url": "https://api.paypal.com/v1/notifications/certs/CERT-test",
    "paypal-transmission-id": "tx-123",
    "paypal-transmission-sig": "c2lnbmF0dXJl",
    "paypal-transmission-time": "2026-10-13T10:00:00Z",
}
EVENT = {
    "id": "WH-1", "event_type": "PAYMENT.SALE.COMPLETED",
    "resource": {"payer": {"payer_info": {"email": "buyer@example.test"}}},
}


class _Resp:
    def __init__(self, status_code=200, payload=None, raise_json=False):
        self.status_code, self._payload, self._raise = status_code, payload, raise_json

    def json(self):
        if self._raise:
            raise ValueError("not json")
        return self._payload


class FakePayPal:
    """Stands in for httpx.AsyncClient; answers the OAuth and verify endpoints."""

    def __init__(self):
        self.calls = []
        self.token_response = _Resp(200, {"access_token": "test-access-token", "token_type": "Bearer"})
        self.verify_response = _Resp(200, {"verification_status": "SUCCESS"})
        self.raise_on = None

    def client(self, *_a, **_k):
        fake = self

        class _Client:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def post(self, url, **kwargs):
                fake.calls.append((url, kwargs))
                if fake.raise_on and fake.raise_on in url:
                    raise ConnectionError("simulated network failure")
                return fake.token_response if url.endswith("/v1/oauth2/token") else fake.verify_response
        return _Client()

    def verify_calls(self):
        return [kw for url, kw in self.calls if url.endswith("/verify-webhook-signature")]


@pytest.fixture
def pp(monkeypatch):
    fake = FakePayPal()
    monkeypatch.setattr(paypal.httpx, "AsyncClient", fake.client)
    monkeypatch.setenv("PAYPAL_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("PAYPAL_CLIENT_SECRET", "test-client-secret")
    fake.provisioned, fake.emailed = [], []

    async def _provision(email, plan="scale"):
        fake.provisioned.append(email)
        return {"api_key": "cg_live_test", "account_id": "acct-1", "email": email, "new": True}

    monkeypatch.setattr(paypal, "_provision_account", _provision)
    monkeypatch.setattr(paypal, "_send_welcome_email", lambda **kw: fake.emailed.append(kw["email"]))
    return fake


async def _post(client, headers=HEADERS, event=EVENT, raw=None):
    body = raw if raw is not None else json.dumps(event)
    return await client.post(URL, content=body, headers={"content-type": "application/json", **headers})


def _nothing_happened(pp):
    return pp.provisioned == [] and pp.emailed == []


# ---------------------------------------------------------------------------
# Missing headers: rejected before anything else
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_unsigned_request_is_rejected(test_client, pp):
    resp = await _post(test_client, headers={})
    assert resp.status_code == 400
    assert pp.calls == [] and _nothing_happened(pp)


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", list(HEADERS))
async def test_each_required_header_is_required(test_client, pp, missing):
    headers = {k: v for k, v in HEADERS.items() if k != missing}
    resp = await _post(test_client, headers=headers)
    assert resp.status_code == 400
    assert missing in resp.json()["detail"]
    assert pp.calls == [] and _nothing_happened(pp)


@pytest.mark.asyncio
async def test_transmission_id_alone_is_not_proof(test_client, pp):
    resp = await _post(test_client, headers={"paypal-transmission-id": "tx-123"})
    assert resp.status_code == 400
    assert pp.calls == [] and _nothing_happened(pp)


@pytest.mark.asyncio
async def test_invalid_json_is_rejected_before_calling_paypal(test_client, pp):
    resp = await _post(test_client, raw="{not json")
    assert resp.status_code == 400
    assert pp.calls == [] and _nothing_happened(pp)


# ---------------------------------------------------------------------------
# Verification outcomes
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_failed_signature_is_rejected(test_client, pp):
    pp.verify_response = _Resp(200, {"verification_status": "FAILURE"})
    resp = await _post(test_client)
    assert resp.status_code == 400
    assert _nothing_happened(pp)


@pytest.mark.asyncio
@pytest.mark.parametrize("token_response,verify_response,raise_on", [
    (None, _Resp(500, {"name": "INTERNAL_SERVICE_ERROR"}), None),       # PayPal error
    (None, _Resp(200, raise_json=True), None),                           # not JSON
    (None, _Resp(200, {"unexpected": True}), None),                      # no status
    (None, None, "verify-webhook-signature"),                            # network failure
    (_Resp(401, {"error": "invalid_client"}), None, None),               # OAuth refused
    (_Resp(200, {"no_token": True}), None, None),                        # OAuth malformed
    (None, None, "oauth2/token"),                                        # OAuth network failure
])
async def test_verification_errors_are_rejected(test_client, pp, token_response, verify_response, raise_on):
    if token_response:
        pp.token_response = token_response
    if verify_response:
        pp.verify_response = verify_response
    pp.raise_on = raise_on
    resp = await _post(test_client)
    assert resp.status_code == 503
    assert _nothing_happened(pp)


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", ["PAYPAL_CLIENT_ID", "PAYPAL_CLIENT_SECRET"])
async def test_missing_paypal_credentials_fail_closed(test_client, pp, monkeypatch, missing):
    monkeypatch.delenv(missing)
    resp = await _post(test_client)
    assert resp.status_code == 503
    assert pp.calls == [] and _nothing_happened(pp)
    assert "test-client" not in resp.text


# ---------------------------------------------------------------------------
# Verified requests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_verified_sale_provisions_and_emails(test_client, pp):
    resp = await _post(test_client)
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "ok"
    assert pp.provisioned == ["buyer@example.test"] and pp.emailed == ["buyer@example.test"]

    [verify] = pp.verify_calls()
    assert verify["headers"]["Authorization"] == "Bearer test-access-token"
    sent = verify["json"]
    assert sent["webhook_event"] == EVENT                     # the parsed event, not a string
    assert sent["webhook_id"] == paypal._webhook_id()
    assert {sent[k] for k in ("auth_algo", "cert_url", "transmission_id", "transmission_sig",
                              "transmission_time")} == set(HEADERS.values())


@pytest.mark.asyncio
async def test_verified_other_event_is_ignored_without_provisioning(test_client, pp):
    resp = await _post(test_client, event={**EVENT, "event_type": "BILLING.PLAN.CREATED"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "ignored"
    assert len(pp.verify_calls()) == 1 and _nothing_happened(pp)


@pytest.mark.asyncio
async def test_secrets_are_not_returned_or_echoed(test_client, pp):
    pp.verify_response = _Resp(200, {"verification_status": "FAILURE"})
    resp = await _post(test_client)
    assert "test-client-secret" not in resp.text and "test-access-token" not in resp.text
