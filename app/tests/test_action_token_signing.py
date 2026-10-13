"""Slack kill-switch action tokens must fail closed (no DB, no network).

GET /reviews/{id}/action approves or cancels a review on the strength of an
HMAC token alone, without an ownership check: the token is the authorisation.
The token secret used to fall back to a string that is in public source, so
with ACTION_TOKEN_SECRET unset any API key holder could mint a token for
another account's review. Missing, short or publicly known secrets must now
refuse to sign and never verify, exactly like audit receipt tokens.
"""
import hashlib
import hmac
import time

import pytest

import app.core.notifications as notifications
from app.core.notifications import (
    ActionTokenUnavailable,
    build_kill_switch_blocks,
    generate_action_token,
    verify_action_token,
)
from app.core.signing_keys import PUBLICLY_KNOWN_KEYS
from app.tests.test_review_ownership import (  # noqa: F401
    A_REVIEW,
    ACCOUNT_B,
    alerts,
    as_account,
    db,
)
from config import settings

GOOD_SECRET = "s" * 40
PUBLIC_DEFAULT = "cogext-action-secret-change-me"
UNUSABLE = ["", "   ", "short-secret", *sorted(PUBLICLY_KNOWN_KEYS)]


def _forge(secret: str, review_id: str, action: str) -> str:
    expires_at = int(time.time()) + 3600
    sig = hmac.new(secret.encode(), f"{review_id}|{action}|{expires_at}".encode(), hashlib.sha256).hexdigest()
    return f"{expires_at}.{sig}"


def test_public_default_is_on_the_known_key_list():
    assert PUBLIC_DEFAULT in PUBLICLY_KNOWN_KEYS


@pytest.mark.parametrize("configured", UNUSABLE)
def test_unusable_secret_refuses_to_sign(monkeypatch, configured):
    monkeypatch.setattr(settings, "ACTION_TOKEN_SECRET", configured)
    with pytest.raises(ActionTokenUnavailable):
        generate_action_token(A_REVIEW, "approve")


@pytest.mark.parametrize("configured", UNUSABLE)
def test_unusable_secret_never_verifies(monkeypatch, configured):
    monkeypatch.setattr(settings, "ACTION_TOKEN_SECRET", configured)
    for secret in (PUBLIC_DEFAULT, configured or "x"):
        assert not verify_action_token(_forge(secret, A_REVIEW, "approve"), A_REVIEW, "approve")


def test_configured_secret_round_trips_and_rejects_public_default(monkeypatch):
    monkeypatch.setattr(settings, "ACTION_TOKEN_SECRET", GOOD_SECRET)
    assert verify_action_token(generate_action_token(A_REVIEW, "approve"), A_REVIEW, "approve")
    assert not verify_action_token(generate_action_token(A_REVIEW, "approve"), A_REVIEW, "cancel")
    assert not verify_action_token(_forge(PUBLIC_DEFAULT, A_REVIEW, "approve"), A_REVIEW, "approve")


def test_secret_is_read_at_call_time(monkeypatch):
    monkeypatch.setattr(settings, "ACTION_TOKEN_SECRET", GOOD_SECRET)
    token = generate_action_token(A_REVIEW, "approve")
    monkeypatch.setattr(settings, "ACTION_TOKEN_SECRET", "")
    assert not verify_action_token(token, A_REVIEW, "approve")


def test_alert_without_usable_secret_has_no_action_buttons(monkeypatch):
    """The alert is still built (and sent), just without forgeable links."""
    monkeypatch.setattr(settings, "ACTION_TOKEN_SECRET", "")
    blocks = build_kill_switch_blocks(A_REVIEW, "c", "promise", 0.9, ["r"], "open", "agent")
    assert blocks and not any(b["type"] == "actions" for b in blocks)
    assert "token=" not in repr(blocks)


def test_alert_with_usable_secret_has_verifiable_buttons(monkeypatch):
    monkeypatch.setattr(settings, "ACTION_TOKEN_SECRET", GOOD_SECRET)
    blocks = build_kill_switch_blocks(A_REVIEW, "c", "promise", 0.9, ["r"], "open", "agent")
    urls = [e["url"] for b in blocks if b["type"] == "actions" for e in b["elements"]]
    assert len(urls) == 2
    for url, action in zip(urls, ("approve", "cancel"), strict=True):
        token = url.split("token=", 1)[1]
        assert verify_action_token(token, A_REVIEW, action)


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["approve", "cancel"])
async def test_other_account_cannot_forge_action_with_public_default(
    test_client, db, as_account, alerts, monkeypatch, action  # noqa: F811
):
    """Account B, holding only its own key, forges a token with the public
    default for account A's review. Nothing may change."""
    monkeypatch.setattr(settings, "ACTION_TOKEN_SECRET", "")
    as_account(ACCOUNT_B)
    before = db.snapshot()
    resp = await test_client.get(
        f"/api/v1/reviews/{A_REVIEW}/action",
        params={"action": action, "token": _forge(PUBLIC_DEFAULT, A_REVIEW, action)},
    )
    assert resp.status_code == 403
    assert db.snapshot() == before and db.writes == []


def test_module_holds_no_import_time_secret():
    assert not hasattr(notifications, "_SECRET")
