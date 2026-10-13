"""Kill Switch — V2.0: Slack notifications with one-click approve/cancel actions.

When a high-risk commitment is created, or when a human review is requested,
fire a Slack Block Kit message with:
  • A summary of the commitment
  • An [APPROVE] button  → calls GET /api/v1/reviews/{id}/action?action=approve&token=…
  • A [CANCEL] button   → calls GET /api/v1/reviews/{id}/action?action=cancel&token=…

Action tokens are HMAC-signed so they can't be forged.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import time
from typing import Any

import httpx

from app.core.signing_keys import usable_signing_key
from config import settings

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.cogextai.com/api/v1"


class ActionTokenUnavailable(RuntimeError):
    """ACTION_TOKEN_SECRET is missing, too short or publicly known."""


# ── Token helpers ────────────────────────────────────────────────────────────

def _secret() -> bytes | None:
    # No fallback: the old fallback string is public, so tokens signed with it
    # could be forged by any caller. Read at call time so a configuration
    # change applies.
    key = usable_signing_key(settings.ACTION_TOKEN_SECRET)
    return key.encode() if key else None


def _make_action_token(secret: bytes, review_id: str, action: str, expires_at: int) -> str:
    """HMAC-SHA256 token: review_id|action|expires_at → hex digest (64 chars)."""
    payload = f"{review_id}|{action}|{expires_at}"
    return hmac.new(secret, payload.encode(), hashlib.sha256).hexdigest()


def generate_action_token(review_id: str, action: str, ttl_seconds: int = 86400) -> str:
    """Return a signed token valid for *ttl_seconds*.

    Raises ActionTokenUnavailable when no usable secret is configured.
    """
    secret = _secret()
    if secret is None:
        raise ActionTokenUnavailable("ACTION_TOKEN_SECRET is not configured for action tokens")
    expires_at = int(time.time()) + ttl_seconds
    sig = _make_action_token(secret, review_id, action, expires_at)
    return f"{expires_at}.{sig}"


def verify_action_token(token: str, review_id: str, action: str) -> bool:
    """Validate a token from query params. Timing-safe. False without a usable secret."""
    secret = _secret()
    if secret is None:
        return False
    try:
        expires_str, sig = token.split(".", 1)
        expires_at = int(expires_str)
    except (ValueError, AttributeError):
        return False
    if int(time.time()) > expires_at:
        return False
    expected = _make_action_token(secret, review_id, action, expires_at)
    return hmac.compare_digest(sig.encode(), expected.encode())


# ── Slack Block Kit builder ──────────────────────────────────────────────────

def _action_url(review_id: str, action: str) -> str:
    token = generate_action_token(review_id, action)
    return f"{_BASE_URL}/reviews/{review_id}/action?action={action}&token={token}"


def build_kill_switch_blocks(
    review_id: str,
    commitment_id: str,
    promise_text: str,
    risk_score: float | None,
    risk_reasons: list[str] | None,
    status: str,
    agent_id: str,
) -> list[dict[str, Any]]:
    risk_pct = f"{round((risk_score or 0) * 100)}%" if risk_score else "N/A"
    reasons_text = " • ".join(risk_reasons or ["unspecified"]) if risk_reasons else "N/A"

    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": "⚠️ COGEXT — High-Risk Commitment Flagged"},
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Promise:*\n_{promise_text}_",
            },
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Risk Score:*\n{risk_pct}"},
                {"type": "mrkdwn", "text": f"*Status:*\n{status}"},
                {"type": "mrkdwn", "text": f"*Agent:*\n`{agent_id[:16]}…`"},
                {"type": "mrkdwn", "text": f"*Review ID:*\n`{review_id[:8]}…`"},
            ],
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*Risk Reasons:*\n{reasons_text}"},
        },
        {"type": "divider"},
    ]
    try:
        approve_url = _action_url(review_id, "approve")
        cancel_url = _action_url(review_id, "cancel")
    except ActionTokenUnavailable:
        # Still alert, but without one-click links that could not be trusted.
        logger.warning("ACTION_TOKEN_SECRET is missing, too short or publicly known; "
                       "kill switch alert sent without action buttons")
        return blocks
    blocks.append(
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "✅ Approve"},
                    "style": "primary",
                    "url": approve_url,
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "🛑 Cancel"},
                    "style": "danger",
                    "url": cancel_url,
                },
            ],
        }
    )
    return blocks


async def send_kill_switch_alert(
    review_id: str,
    commitment_id: str,
    promise_text: str,
    risk_score: float | None = None,
    risk_reasons: list[str] | None = None,
    status: str = "open",
    agent_id: str = "",
) -> bool:
    """Send a Block Kit Slack message. Returns True on success."""
    webhook_url = settings.SLACK_WEBHOOK_URL
    if not webhook_url:
        logger.info("SLACK_WEBHOOK_URL not set — skipping kill switch alert")
        return False

    blocks = build_kill_switch_blocks(
        review_id=review_id,
        commitment_id=commitment_id,
        promise_text=promise_text,
        risk_score=risk_score,
        risk_reasons=risk_reasons,
        status=status,
        agent_id=agent_id,
    )
    payload = {
        "text": f"⚠️ High-risk commitment flagged: {promise_text[:80]}…",
        "blocks": blocks,
    }
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(webhook_url, json=payload)
            if resp.status_code != 200:
                logger.warning("Slack webhook returned %s: %s", resp.status_code, resp.text)
                return False
        return True
    except Exception as e:
        logger.warning("Slack alert failed: %s", e)
        return False
