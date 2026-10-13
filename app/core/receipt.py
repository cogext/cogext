"""Public Audit Receipt — V2.0.

Generates a tamper-evident, shareable receipt token for a commitment.
The token is a URL-safe base64 of a SHA-256 HMAC over the commitment's
stable identity fields (id + user_id + promise_text + created_at).

Verification: re-compute the HMAC from DB fields and compare to stored token.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import secrets

from app.core.signing_keys import usable_signing_key
from config import settings

logger = logging.getLogger(__name__)


class ReceiptSigningUnavailable(RuntimeError):
    """ACTION_TOKEN_SECRET is missing, too short or publicly known."""


def _secret() -> bytes | None:
    # No fallback: the old fallback string is public, so tokens signed with it
    # could be forged. Read at call time so a configuration change applies.
    key = usable_signing_key(settings.ACTION_TOKEN_SECRET)
    return key.encode() if key else None


def _sign(secret: bytes, commitment_id: str, user_id: str, promise_text: str, created_at: str) -> str:
    """HMAC-SHA256 over stable commitment fields → URL-safe base64 (43 chars)."""
    payload = f"{commitment_id}|{user_id}|{promise_text}|{created_at}"
    sig = hmac.new(secret, payload.encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(sig).rstrip(b"=").decode()


def generate_receipt_token(
    commitment_id: str,
    user_id: str,
    promise_text: str,
    created_at: str,
) -> str:
    """Return a receipt token that encodes identity + integrity signature.

    Format: <8-char random prefix>.<HMAC-SHA256 of stable fields>
    The random prefix makes tokens unguessable even if an attacker knows the HMAC key.
    Raises ReceiptSigningUnavailable when no usable secret is configured.
    """
    secret = _secret()
    if secret is None:
        raise ReceiptSigningUnavailable("ACTION_TOKEN_SECRET is not configured for receipt signing")
    prefix = secrets.token_urlsafe(6)  # 8 base64 chars
    sig = _sign(secret, commitment_id, user_id, promise_text, created_at)
    return f"{prefix}.{sig}"


def verify_receipt_token(
    token: str,
    commitment_id: str,
    user_id: str,
    promise_text: str,
    created_at: str,
) -> bool:
    """Verify *token* against commitment fields. Timing-safe. False without a usable secret."""
    secret = _secret()
    if secret is None:
        return False
    try:
        _, sig = token.split(".", 1)
    except ValueError:
        return False
    expected = _sign(secret, commitment_id, user_id, promise_text, created_at)
    return hmac.compare_digest(sig.encode(), expected.encode())
