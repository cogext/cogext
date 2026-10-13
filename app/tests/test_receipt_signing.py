"""Receipt signing without public fallback keys (no DB, no network).

Two signing systems:
  * live receipts, signed by observers and verified with OBSERVE_RECEIPT_KEY;
  * audit receipt tokens, signed and verified by the backend with ACTION_TOKEN_SECRET.
Neither may fall back to a publicly known key. A missing, short or publicly
known key means "cannot verify" (never "valid") and "cannot sign" (503).
Test keys below are synthetic and exist only in this file.
"""
import hashlib
import hmac
import json
import uuid

import pytest

import app.api.audit as audit_api
import app.api.live as live_api
import app.core.live_receipt_verify as live_verify
import app.core.receipt as audit_receipt
from app.core.auth import Account, get_current_account
from app.main import app
from app.tests.test_commitment_ownership import A_OPEN, ACCOUNT_A, FakeDB
from config import settings

GOOD_KEY = "test-only-live-receipt-key-0123456789abcdef"      # 43 chars, synthetic
GOOD_SECRET = "test-only-audit-receipt-secret-0123456789ab"   # 43 chars, synthetic
PUBLIC_OBSERVER_KEY = "cogext-observe-receipt-v1"
PUBLIC_RECEIPT_FALLBACK = "cogext-receipt-secret-change-me"
PUBLIC_ACTION_FALLBACK = "cogext-action-secret-change-me"
BAD_KEYS = ["", "   ", PUBLIC_OBSERVER_KEY, PUBLIC_RECEIPT_FALLBACK, PUBLIC_ACTION_FALLBACK, "short-key"]

IGNORED = {"published", "created_at", "id", "views", "_id", "verify_url"}


def _observer_sign(payload: dict, key: str) -> str:
    """Same canonical form as cogext_observe.receipt (sorted keys, compact, UTF-8 kept)."""
    clean = {k: v for k, v in payload.items() if k != "signature" and k not in IGNORED}
    canonical = json.dumps(clean, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hmac.new(key.encode(), canonical.encode(), hashlib.sha256).hexdigest()


def _receipt(key: str, **overrides) -> dict:
    payload = {
        "receipt_id": "r-1", "session_id": "s-1", "tool": "send_email",
        "tool_response": {"status": 500}, "agent_claim": "Sent the e-mail – done ✓",
        "verdict": "MISMATCH", "reason": "tool failed", "recorded_at": "2026-10-10T10:00:00+00:00",
        "verify_url": "https://cogextai.com/r/r-1",
    }
    payload["signature"] = _observer_sign(payload, key)
    payload.update(overrides)
    return payload


# ---------------------------------------------------------------------------
# Live receipts
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("configured", BAD_KEYS)
def test_live_receipt_unverifiable_without_a_usable_key(monkeypatch, configured):
    monkeypatch.setattr(settings, "OBSERVE_RECEIPT_KEY", configured)
    forged = _receipt(PUBLIC_OBSERVER_KEY)            # anyone can produce this
    assert live_verify.live_receipt_status(forged) == "unverifiable"
    assert live_verify.verify_live_receipt(forged) is False


def test_live_receipt_valid_with_configured_key(monkeypatch):
    monkeypatch.setattr(settings, "OBSERVE_RECEIPT_KEY", GOOD_KEY)
    receipt = _receipt(GOOD_KEY, created_at="2026-10-10T10:00:01Z", published=True)   # backend envelope ignored
    assert live_verify.live_receipt_status(receipt) == "valid"
    assert live_verify.verify_live_receipt(receipt) is True


@pytest.mark.parametrize("tamper", [
    {"verdict": "MATCH"},
    {"agent_claim": "something else"},
    {"signature": "0" * 64},
])
def test_live_receipt_invalid_when_tampered_or_badly_signed(monkeypatch, tamper):
    monkeypatch.setattr(settings, "OBSERVE_RECEIPT_KEY", GOOD_KEY)
    assert live_verify.live_receipt_status(_receipt(GOOD_KEY, **tamper)) == "invalid"


@pytest.mark.parametrize("signature", ["", None, 12345])
def test_live_receipt_without_a_signature_is_unsigned(monkeypatch, signature):
    """No signature is not evidence of tampering, and never of authenticity."""
    monkeypatch.setattr(settings, "OBSERVE_RECEIPT_KEY", GOOD_KEY)
    assert live_verify.live_receipt_status(_receipt(GOOD_KEY, signature=signature)) == "unsigned"


def test_receipt_signed_with_public_default_is_unsigned_under_a_real_key(monkeypatch):
    monkeypatch.setattr(settings, "OBSERVE_RECEIPT_KEY", GOOD_KEY)
    monkeypatch.setattr(settings, "LIVE_RECEIPT_LEGACY_CUTOFF", "")
    status = live_verify.live_receipt_status(_receipt(PUBLIC_OBSERVER_KEY))
    assert status == "unsigned"
    assert live_verify.verify_live_receipt(_receipt(PUBLIC_OBSERVER_KEY)) is False


# ---------------------------------------------------------------------------
# Legacy receipts: stored before the trusted key, signed with the public key
# ---------------------------------------------------------------------------

CUTOFF = "2026-10-13T00:00:00+00:00"
BEFORE = "2026-10-12T20:13:26.1764+00:00"     # PostgREST timestamptz format
AFTER = "2026-10-13T00:00:01+00:00"


def _stored(key: str, created_at: str, **overrides) -> dict:
    """A live_receipts row as the backend serves it: server created_at, no verify_url."""
    receipt = _receipt(key, **overrides)
    receipt.pop("verify_url")
    receipt["created_at"] = created_at
    receipt["published"] = False
    return receipt


@pytest.fixture
def legacy_cutoff(monkeypatch):
    monkeypatch.setattr(settings, "OBSERVE_RECEIPT_KEY", GOOD_KEY)
    monkeypatch.setattr(settings, "LIVE_RECEIPT_LEGACY_CUTOFF", CUTOFF)


def test_public_key_receipt_stored_before_cutoff_is_legacy_not_valid(legacy_cutoff):
    receipt = _stored(PUBLIC_OBSERVER_KEY, BEFORE)
    assert live_verify.live_receipt_status(receipt) == "legacy"
    assert live_verify.verify_live_receipt(receipt) is False


def test_public_key_receipt_stored_after_cutoff_is_unsigned(legacy_cutoff):
    """A receipt forged with the public key today cannot pass as legacy."""
    assert live_verify.live_receipt_status(_stored(PUBLIC_OBSERVER_KEY, AFTER)) == "unsigned"


def test_trusted_receipt_is_valid_on_either_side_of_the_cutoff(legacy_cutoff):
    for created_at in (BEFORE, AFTER):
        assert live_verify.live_receipt_status(_stored(GOOD_KEY, created_at)) == "valid"


@pytest.mark.parametrize("signed_at,served_at", [
    ("2026-10-12T20:13:26.176400+00:00", "2026-10-12T20:13:26.1764+00:00"),
    ("2026-10-12T20:13:26.500000+00:00", "2026-10-12T20:13:26.5+00:00"),
    ("2026-10-12T20:13:26.123456+00:00", "2026-10-12T20:13:26.123456+00:00"),
])
def test_legacy_receipt_whose_timestamp_postgres_trimmed_is_still_legacy(legacy_cutoff, signed_at, served_at):
    """Old observers signed microseconds; storage drops trailing zeros. That is
    not tampering, so it must not show as invalid."""
    receipt = _stored(PUBLIC_OBSERVER_KEY, BEFORE, recorded_at=signed_at)
    receipt["signature"] = _observer_sign(receipt, PUBLIC_OBSERVER_KEY)
    receipt["recorded_at"] = served_at
    assert live_verify.live_receipt_status(receipt) == "legacy"


def test_timestamp_padding_never_makes_a_receipt_valid(legacy_cutoff):
    """The padding retry exists only for legacy receipts."""
    receipt = _stored(GOOD_KEY, BEFORE, recorded_at="2026-10-12T20:13:26.176400+00:00")
    receipt["signature"] = _observer_sign(receipt, GOOD_KEY)
    receipt["recorded_at"] = "2026-10-12T20:13:26.1764+00:00"
    assert live_verify.live_receipt_status(receipt) == "invalid"


def test_legacy_receipt_altered_after_signing_is_invalid(legacy_cutoff):
    receipt = _stored(PUBLIC_OBSERVER_KEY, BEFORE)
    receipt["verdict"] = "MATCH"
    assert live_verify.live_receipt_status(receipt) == "invalid"


def test_client_recorded_at_cannot_make_a_receipt_legacy(legacy_cutoff):
    """Only the server's created_at decides; recorded_at is client-supplied."""
    receipt = _stored(PUBLIC_OBSERVER_KEY, AFTER, recorded_at="2020-01-01T00:00:00+00:00")
    receipt["signature"] = _observer_sign(receipt, PUBLIC_OBSERVER_KEY)
    assert live_verify.live_receipt_status(receipt) == "unsigned"


@pytest.mark.parametrize("created_at", [None, "", "yesterday", "2026-10-12T20:13:26"])
def test_missing_or_naive_created_at_is_never_legacy(legacy_cutoff, created_at):
    receipt = _stored(PUBLIC_OBSERVER_KEY, BEFORE)
    receipt["created_at"] = created_at
    assert live_verify.live_receipt_status(receipt) == "unsigned"


@pytest.mark.parametrize("cutoff", ["", "  ", "not-a-date", "2026-10-13T00:00:00", "2999-01-01T00:00:00+00:00"])
def test_unusable_cutoff_makes_nothing_legacy(monkeypatch, cutoff):
    """Unset, unparseable, timezone-less or future cutoffs are ignored."""
    monkeypatch.setattr(settings, "OBSERVE_RECEIPT_KEY", GOOD_KEY)
    monkeypatch.setattr(settings, "LIVE_RECEIPT_LEGACY_CUTOFF", cutoff)
    assert live_verify.live_receipt_status(_stored(PUBLIC_OBSERVER_KEY, BEFORE)) == "unsigned"


def test_legacy_needs_a_trusted_key_configured(monkeypatch):
    monkeypatch.setattr(settings, "OBSERVE_RECEIPT_KEY", "")
    monkeypatch.setattr(settings, "LIVE_RECEIPT_LEGACY_CUTOFF", CUTOFF)
    assert live_verify.live_receipt_status(_stored(PUBLIC_OBSERVER_KEY, BEFORE)) == "unverifiable"


def test_legacy_cutoff_default_is_unset():
    assert type(settings).model_fields["LIVE_RECEIPT_LEGACY_CUTOFF"].default == ""


def test_no_hardcoded_default_key_remains():
    assert type(settings).model_fields["OBSERVE_RECEIPT_KEY"].default == ""
    assert not hasattr(live_verify, "DEFAULT_KEY")


class _LiveDB(FakeDB):
    def __init__(self, receipt):
        super().__init__()
        self.tables["live_receipts"] = [{**receipt, "id": receipt["receipt_id"]}]


@pytest.mark.asyncio
@pytest.mark.parametrize("configured,signing_key,expected", [
    ("", PUBLIC_OBSERVER_KEY, {"valid": False, "status": "unverifiable"}),
    (GOOD_KEY, GOOD_KEY, {"valid": True, "status": "valid"}),
    (GOOD_KEY, PUBLIC_OBSERVER_KEY, {"valid": False, "status": "unsigned"}),
    (GOOD_KEY, "legacy", {"valid": False, "status": "legacy"}),
    (GOOD_KEY, "tampered", {"valid": False, "status": "invalid"}),
])
async def test_live_verify_route(test_client, monkeypatch, configured, signing_key, expected):
    monkeypatch.setattr(settings, "OBSERVE_RECEIPT_KEY", configured)
    monkeypatch.setattr(settings, "LIVE_RECEIPT_LEGACY_CUTOFF", "")
    if signing_key == "legacy":
        monkeypatch.setattr(settings, "LIVE_RECEIPT_LEGACY_CUTOFF", CUTOFF)
        receipt = _stored(PUBLIC_OBSERVER_KEY, BEFORE)
    elif signing_key == "tampered":
        receipt = _stored(GOOD_KEY, AFTER)
        receipt["agent_claim"] = "edited after signing"
    else:
        receipt = _receipt(signing_key)
        receipt.pop("verify_url")
        receipt["signature"] = _observer_sign(receipt, signing_key)
    db = _LiveDB(receipt)
    db.tables["live_receipts"][0]["receipt_id"] = "r-1"
    monkeypatch.setattr(live_api, "get_supabase", lambda: db)
    resp = await test_client.get("/api/v1/live/receipt/r-1/verify")
    assert resp.status_code == 200, resp.text
    assert resp.json() == expected
    assert configured not in resp.text or configured == ""


# ---------------------------------------------------------------------------
# Audit receipt tokens
# ---------------------------------------------------------------------------

FIELDS = dict(commitment_id="c-1", user_id=ACCOUNT_A, promise_text="Deliver", created_at="2026-10-01")


@pytest.mark.parametrize("configured", BAD_KEYS)
def test_audit_token_cannot_be_signed_without_a_usable_secret(monkeypatch, configured):
    monkeypatch.setattr(settings, "ACTION_TOKEN_SECRET", configured)
    with pytest.raises(audit_receipt.ReceiptSigningUnavailable):
        audit_receipt.generate_receipt_token(**FIELDS)


@pytest.mark.parametrize("configured", BAD_KEYS)
def test_audit_token_signed_with_public_fallback_never_verifies(monkeypatch, configured):
    sig = hmac.new(PUBLIC_RECEIPT_FALLBACK.encode(),
                   "c-1|{}|Deliver|2026-10-01".format(ACCOUNT_A).encode(), hashlib.sha256).digest()
    import base64
    forged = "abcdefgh." + base64.urlsafe_b64encode(sig).rstrip(b"=").decode()
    monkeypatch.setattr(settings, "ACTION_TOKEN_SECRET", configured)
    assert audit_receipt.verify_receipt_token(forged, **FIELDS) is False


def test_audit_token_round_trip_and_tamper(monkeypatch):
    monkeypatch.setattr(settings, "ACTION_TOKEN_SECRET", GOOD_SECRET)
    token = audit_receipt.generate_receipt_token(**FIELDS)
    assert audit_receipt.verify_receipt_token(token, **FIELDS) is True
    assert audit_receipt.verify_receipt_token(token, **{**FIELDS, "promise_text": "Changed"}) is False
    assert audit_receipt.verify_receipt_token("no-dot-token", **FIELDS) is False
    monkeypatch.setattr(settings, "ACTION_TOKEN_SECRET", GOOD_SECRET[::-1])
    assert audit_receipt.verify_receipt_token(token, **FIELDS) is False


@pytest.fixture
def audit_db(monkeypatch):
    db = FakeDB()
    db.row("commitments", A_OPEN)["receipt_token"] = None
    monkeypatch.setattr(audit_api, "get_supabase", lambda: db)
    app.dependency_overrides[get_current_account] = lambda: Account(ACCOUNT_A, "a@example.test", ACCOUNT_A)
    yield db
    app.dependency_overrides.pop(get_current_account, None)


@pytest.mark.asyncio
@pytest.mark.parametrize("configured", ["", PUBLIC_RECEIPT_FALLBACK])
async def test_create_receipt_refuses_without_secret_and_writes_nothing(test_client, monkeypatch, audit_db, configured):
    monkeypatch.setattr(settings, "ACTION_TOKEN_SECRET", configured)
    resp = await test_client.post(f"/api/v1/commitments/{A_OPEN}/receipt")
    assert resp.status_code == 503
    assert audit_db.row("commitments", A_OPEN)["receipt_token"] is None
    assert audit_db.writes == []
    assert (configured or "cogext") not in resp.json()["detail"]


@pytest.mark.asyncio
async def test_create_and_audit_receipt_with_secret(test_client, monkeypatch, audit_db):
    monkeypatch.setattr(settings, "ACTION_TOKEN_SECRET", GOOD_SECRET)
    created = await test_client.post(f"/api/v1/commitments/{A_OPEN}/receipt")
    assert created.status_code == 200, created.text
    token = created.json()["receipt_token"]
    assert audit_db.row("commitments", A_OPEN)["receipt_token"] == token

    audited = await test_client.get(f"/api/v1/audit/{token}")
    assert audited.status_code == 200 and audited.json()["receipt_valid"] is True

    monkeypatch.setattr(settings, "ACTION_TOKEN_SECRET", "")
    unconfigured = await test_client.get(f"/api/v1/audit/{token}")
    assert unconfigured.status_code == 200 and unconfigured.json()["receipt_valid"] is False
