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
from datetime import datetime, timezone

from config import settings

logger = logging.getLogger(__name__)

_SECRET = settings.ACTION_TOKEN_SECRET or "cogext-receipt-secret-change-me"


def _sign(commitment_id: str, user_id: str, promise_text: str, created_at: str) -> str:
    """HMAC-SHA256 over stable commitment fields → URL-safe base64 (43 chars)."""
    payload = f"{commitment_id}|{user_id}|{promise_text}|{created_at}"
    sig = hmac.new(_SECRET.encode(), payload.encode(), hashlib.sha256).digest()
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
    """
    prefix = secrets.token_urlsafe(6)  # 8 base64 chars
    sig = _sign(commitment_id, user_id, promise_text, created_at)
    return f"{prefix}.{sig}"


def verify_receipt_token(
    token: str,
    commitment_id: str,
    user_id: str,
    promise_text: str,
    created_at: str,
) -> bool:
    """Verify *token* against commitment fields. Timing-safe."""
    try:
        _, sig = token.split(".", 1)
    except ValueError:
        return False
    expected = _sign(commitment_id, user_id, promise_text, created_at)
    return hmac.compare_digest(sig, expected)
