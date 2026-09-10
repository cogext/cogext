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
import json
import logging
import time
from typing import Any

import httpx

from config import settings

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.cogextai.com/api/v1"
_SECRET = (settings.ACTION_TOKEN_SECRET or "cogext-action-secret-change-me").encode()


# ── Token helpers ────────────────────────────────────────────────────────────

def _make_action_token(review_id: str, action: str, expires_at: int) -> str:
    """HMAC-SHA256 token: review_id|action|expires_at → hex digest (64 chars)."""
    payload = f"{review_id}|{action}|{expires_at}"
    return hmac.new(_SECRET, payload.encode(), hashlib.sha256).hexdigest()


def generate_action_token(review_id: str, action: str, ttl_seconds: int = 86400) -> str:
    """Return a signed token valid for *ttl_seconds*."""
    expires_at = int(time.time()) + ttl_seconds
    sig = _make_action_token(review_id, action, expires_at)
    return f"{expires_at}.{sig}"


def verify_action_token(token: str, review_id: str, action: str) -> bool:
    """Validate a token from query params. Timing-safe."""
    try:
        expires_str, sig = token.split(".", 1)
        expires_at = int(expires_str)
    except (ValueError, AttributeError):
        return False
    if int(time.time()) > expires_at:
        return False
    expected = _make_action_token(review_id, action, expires_at)
    return hmac.compare_digest(sig, expected)


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

    return [
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
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "✅ Approve"},
                    "style": "primary",
                    "url": _action_url(review_id, "approve"),
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "🛑 Cancel"},
                    "style": "danger",
                    "url": _action_url(review_id, "cancel"),
                },
            ],
        },
    ]


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
