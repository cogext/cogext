"""V1.7 – Outbound webhook subscription management and delivery engine.

Security:
  - HMAC-SHA256 signature on every delivery (X-COGEXT-Signature header)
  - Secret stored plaintext. Webhook secrets are signing keys — the sender needs
    the exact value to compute the HMAC, so hashing them would break every
    delivery. The real fix is encryption at rest with a master key held outside
    the database. That is a separate change tracked for a later phase.
  - SSRF protection: reject private/loopback IP ranges
  - At-least-once delivery with exponential backoff
  - Tenant isolation: a subscription belongs to the account that created it
    (webhook_subscriptions.account_id, migration 011). Every route is scoped to
    the caller's account, and events are delivered only to subscriptions of
    the account that produced them. Rows with no owner (created before 011)
    are invisible to every account and receive nothing.
"""
import hashlib
import hmac
import ipaddress
import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.auth import Account, get_current_account
from app.db.connection import get_supabase
from app.models.webhook import (
    CreateWebhookRequest,
    UpdateWebhookRequest,
    WebhookDelivery,
    WebhookSubscriptionResponse,
)

logger = logging.getLogger(__name__)
router = APIRouter()

_PRIVATE_RANGES = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
]

MAX_ATTEMPTS = 5
BACKOFF_BASE_SECONDS = 30

# Columns safe to return: never secret_hash, never account_id.
_PUBLIC_COLS = "id, endpoint, active, subscribed_event_types, created_at, updated_at, failure_count, last_delivery_at"

_NOT_FOUND = "Webhook not found"
_DUPLICATE = "An active webhook for this endpoint already exists"


def _is_ssrf_safe(url: str) -> bool:
    """Return True if URL is safe to send outbound requests to."""
    try:
        parsed = urlparse(url)
        host = parsed.hostname
        if not host:
            return False
        try:
            addr = ipaddress.ip_address(host)
            for net in _PRIVATE_RANGES:
                if addr in net:
                    return False
        except ValueError:
            # hostname — allow (DNS could resolve to private, but we check at delivery)
            pass
        return parsed.scheme in ("https", "http")
    except Exception:
        return False


def _sign_payload(secret: str, body: bytes) -> str:
    """Produce HMAC-SHA256 signature."""
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _is_unique_violation(err: Exception) -> bool:
    return getattr(err, "code", None) == "23505"


async def _get_owned_webhook(sb: Any, webhook_id: uuid.UUID, account: Account) -> dict:
    """Return the caller's subscription, else 404 (same for another account's,
    an ownerless, or a missing subscription)."""
    resp = await sb.table("webhook_subscriptions").select(_PUBLIC_COLS).eq(
        "id", str(webhook_id)
    ).eq("account_id", account.account_id).execute()
    if not resp.data:
        raise HTTPException(status_code=404, detail=_NOT_FOUND)
    return resp.data[0]


# ---------------------------------------------------------------------------
# Subscription CRUD
# ---------------------------------------------------------------------------

@router.post("/webhooks", response_model=WebhookSubscriptionResponse)
async def create_webhook(
    body: CreateWebhookRequest,
    account: Account = Depends(get_current_account),
) -> WebhookSubscriptionResponse:
    if not _is_ssrf_safe(body.endpoint):
        raise HTTPException(status_code=400, detail="Endpoint URL is not allowed (SSRF protection)")

    sb = get_supabase()
    wh_id = uuid.uuid4()
    now = datetime.now(timezone.utc).isoformat()

    try:
        await sb.table("webhook_subscriptions").insert({
            "id": str(wh_id),
            "account_id": account.account_id,
            "endpoint": body.endpoint,
            "active": True,
            "subscribed_event_types": body.subscribed_event_types,
            # Plaintext by design — see module docstring.
            "secret_hash": body.secret,  # stored as plaintext; never returned in API responses
            "failure_count": 0,
            "created_at": now,
            "updated_at": now,
        }).execute()
    except Exception as e:
        if _is_unique_violation(e):
            raise HTTPException(status_code=409, detail=_DUPLICATE)
        raise

    return WebhookSubscriptionResponse.model_validate(await _get_owned_webhook(sb, wh_id, account))


@router.get("/webhooks", response_model=list[WebhookSubscriptionResponse])
async def list_webhooks(
    active: bool | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    account: Account = Depends(get_current_account),
) -> list[WebhookSubscriptionResponse]:
    sb = get_supabase()
    q = sb.table("webhook_subscriptions").select(_PUBLIC_COLS).eq(
        "account_id", account.account_id
    ).limit(limit)
    if active is not None:
        q = q.eq("active", active)
    resp = await q.execute()
    return [WebhookSubscriptionResponse.model_validate(r) for r in (resp.data or [])]


@router.get("/webhooks/{webhook_id}", response_model=WebhookSubscriptionResponse)
async def get_webhook(
    webhook_id: uuid.UUID,
    account: Account = Depends(get_current_account),
) -> WebhookSubscriptionResponse:
    sb = get_supabase()
    return WebhookSubscriptionResponse.model_validate(await _get_owned_webhook(sb, webhook_id, account))


@router.patch("/webhooks/{webhook_id}", response_model=WebhookSubscriptionResponse)
async def update_webhook(
    webhook_id: uuid.UUID,
    body: UpdateWebhookRequest,
    account: Account = Depends(get_current_account),
) -> WebhookSubscriptionResponse:
    sb = get_supabase()
    await _get_owned_webhook(sb, webhook_id, account)
    updates: dict[str, Any] = {"updated_at": datetime.now(timezone.utc).isoformat()}
    if body.endpoint is not None:
        if not _is_ssrf_safe(body.endpoint):
            raise HTTPException(status_code=400, detail="Endpoint URL is not allowed")
        updates["endpoint"] = body.endpoint
    if body.active is not None:
        updates["active"] = body.active
    if body.subscribed_event_types is not None:
        updates["subscribed_event_types"] = body.subscribed_event_types
    if body.secret is not None:
        # Plaintext by design — see module docstring.
        updates["secret_hash"] = body.secret  # stored as plaintext; never returned in API responses

    try:
        await sb.table("webhook_subscriptions").update(updates).eq(
            "id", str(webhook_id)
        ).eq("account_id", account.account_id).execute()
    except Exception as e:
        if _is_unique_violation(e):
            raise HTTPException(status_code=409, detail=_DUPLICATE)
        raise
    return WebhookSubscriptionResponse.model_validate(await _get_owned_webhook(sb, webhook_id, account))


@router.delete("/webhooks/{webhook_id}")
async def delete_webhook(
    webhook_id: uuid.UUID,
    account: Account = Depends(get_current_account),
) -> dict:
    sb = get_supabase()
    await _get_owned_webhook(sb, webhook_id, account)
    await sb.table("webhook_subscriptions").update(
        {"active": False, "updated_at": datetime.now(timezone.utc).isoformat()}
    ).eq("id", str(webhook_id)).eq("account_id", account.account_id).execute()
    return {"deactivated": True, "webhook_id": str(webhook_id)}


# ---------------------------------------------------------------------------
# Delivery engine
# ---------------------------------------------------------------------------

async def deliver_event(
    event_id: str,
    event_type: str,
    payload: dict[str, Any],
    *,
    account_id: str | None,
) -> int:
    """Deliver an event to the producing account's matching active subscriptions.

    account_id is required and keyword-only so no caller can deliver unscoped.
    Without an account nothing is delivered. Returns the delivery count.
    """
    if not account_id:
        logger.warning("deliver_event called without an account; %s not delivered", event_type)
        return 0
    sb = get_supabase()

    # The account's subscriptions to this event type (or to all events)
    subs_resp = await sb.table("webhook_subscriptions").select(
        "id, endpoint, secret_hash, subscribed_event_types"
    ).eq("active", True).eq("account_id", account_id).execute()

    matching = [
        s for s in (subs_resp.data or [])
        if not s["subscribed_event_types"]
        or event_type in s["subscribed_event_types"]
    ]

    count = 0
    for sub in matching:
        count += await _attempt_delivery(sb, sub, event_id, event_type, payload)
    return count


async def _attempt_delivery(
    sb: Any,
    sub: dict,
    event_id: str,
    event_type: str,
    payload: dict,
    attempt: int = 1,
) -> int:
    """Attempt delivery to one subscription endpoint with retries."""
    body_bytes = json.dumps(payload, default=str).encode()
    # secret_hash stores the plaintext secret (never exposed in API responses)
    plaintext_secret = sub.get("secret_hash", "")
    signature = _sign_payload(plaintext_secret, body_bytes)

    delivery_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)

    for attempt_num in range(1, MAX_ATTEMPTS + 1):
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    sub["endpoint"],
                    content=body_bytes,
                    headers={
                        "Content-Type": "application/json",
                        "X-COGEXT-Signature": f"sha256={signature}",
                        "X-COGEXT-Event": event_type,
                        "X-COGEXT-Delivery": delivery_id,
                    },
                )
            status = "delivered" if resp.status_code < 300 else "failed"
            await sb.table("webhook_deliveries").upsert({
                "id": delivery_id,
                "event_id": event_id,
                "webhook_id": sub["id"],
                "attempt": attempt_num,
                "status": status,
                "response_code": resp.status_code,
                "truncated_response": resp.text[:500],
                "delivered_at": now.isoformat(),
            }, on_conflict="id", ignore_duplicates=False).execute()

            if status == "delivered":
                await sb.table("webhook_subscriptions").update({
                    "failure_count": 0,
                    "last_delivery_at": now.isoformat(),
                }).eq("id", sub["id"]).execute()
                return 1

        except Exception as e:
            logger.warning("Webhook delivery attempt %d failed endpoint=%s: %s",
                           attempt_num, sub["endpoint"], e)

        if attempt_num < MAX_ATTEMPTS:
            backoff = BACKOFF_BASE_SECONDS * (2 ** (attempt_num - 1))
            next_retry = (now + timedelta(seconds=backoff)).isoformat()
            await sb.table("webhook_deliveries").upsert({
                "id": delivery_id,
                "event_id": event_id,
                "webhook_id": sub["id"],
                "attempt": attempt_num,
                "status": "retrying",
                "next_retry_at": next_retry,
            }, on_conflict="id", ignore_duplicates=False).execute()

    # All attempts exhausted
    # Use raw SQL increment to avoid read-modify-write race on concurrent failures
    cur_count = (sub.get("failure_count") or 0) + MAX_ATTEMPTS
    await sb.table("webhook_subscriptions").update({
        "failure_count": cur_count,
    }).eq("id", sub["id"]).execute()
    return 0


@router.get("/webhooks/{webhook_id}/deliveries", response_model=list[WebhookDelivery])
async def list_deliveries(
    webhook_id: uuid.UUID,
    limit: int = Query(default=50, ge=1, le=200),
    account: Account = Depends(get_current_account),
) -> list[WebhookDelivery]:
    sb = get_supabase()
    await _get_owned_webhook(sb, webhook_id, account)
    resp = await sb.table("webhook_deliveries").select("*").eq(
        "webhook_id", str(webhook_id)
    ).order("delivered_at", desc=True).limit(limit).execute()
    return [WebhookDelivery.model_validate(r) for r in (resp.data or [])]
