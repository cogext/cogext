"""Live observer API — sessions, events, receipts and the public Square.

Public routes (no API key): the try-observer and the observe decorator
both stream here from processes that hold no COGEXT key, and the two
public pages (/live/:id, /r/:id, /square) read from here.

Vendor names are stripped on the read path, never on write. The stored
session always keeps exactly what the caller sent, so publishing never
rewrites the events column and the raw record stays auditable.
"""
import json
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.db.connection import get_supabase
from app.core.live_receipt_verify import verify_live_receipt as _verify

router = APIRouter()

# ---------------------------------------------------------------------------
# event size guard
#
# /event is public and unauthenticated, so its payload cannot be trusted. One
# oversized event is how a session reached 14.8 MB and made publish hit
# Supabase's statement timeout (Postgres 57014). Every string is capped first;
# because bulk is usually structural rather than a single long string, the
# whole event is then bounded as well.
# ---------------------------------------------------------------------------
MAX_EVENT_BYTES = 20_000
MAX_FIELD_CHARS = 2_000


def _truncate_event(obj, limit: int = MAX_FIELD_CHARS):
    """Recursively truncate any string inside an event payload."""
    if isinstance(obj, str):
        return obj if len(obj) <= limit else obj[:limit] + "... [truncated]"
    if isinstance(obj, dict):
        return {k: _truncate_event(v, limit) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_truncate_event(v, limit) for v in obj]
    return obj


def _event_size(obj) -> int:
    try:
        return len(json.dumps(obj))
    except Exception:
        return MAX_EVENT_BYTES + 1


def _bound_event(event, limit: int = MAX_EVENT_BYTES):
    """Return an event guaranteed to serialise to at most `limit` bytes.

    Capping individual fields is not enough on its own: a payload whose bulk is
    structural (many medium fields, a large list) stays over the ceiling no
    matter how short each string is, so the oversized values are replaced by a
    preview as a last resort.
    """
    original = _event_size(event)
    if original <= limit:
        return event

    event = _truncate_event(event)
    if _event_size(event) <= limit:
        return event

    out: dict = {}
    for key, value in (event.items() if isinstance(event, dict) else []):
        if _event_size(value) > 2_000:
            out[key] = {
                "_truncated": True,
                "_original_bytes": _event_size(value),
                "preview": json.dumps(value)[:1_000],
            }
        else:
            out[key] = value
    out["_truncated"] = True
    out["_original_bytes"] = original

    if _event_size(out) > limit:
        return {
            "_truncated": True,
            "_original_bytes": original,
            "preview": json.dumps(event)[: limit // 2],
        }
    return out


class SessionCreate(BaseModel):
    session_id: str


class EventLog(BaseModel):
    session_id: str
    event: dict


class PublishRequest(BaseModel):
    session_id: str


class Receipt(BaseModel):
    receipt_id: str
    session_id: str
    tool: str
    tool_response: dict
    agent_claim: str
    verdict: str
    reason: str
    recorded_at: str
    signature: str
    verify_url: str


VENDOR_STRIP = {
    "Claude": "the agent",
    "claude": "the agent",
    "GPT": "the agent",
    "Gemini": "the agent",
    "Anthropic": "the model provider",
    "OpenAI": "the model provider",
    "Google": "the model provider",
    "gpt-4": "the model",
    "gpt-3": "the model",
    "claude-3": "the model",
}


def _strip_vendors(value: Any) -> Any:
    if isinstance(value, str):
        for k, v in VENDOR_STRIP.items():
            value = value.replace(k, v)
        return value
    if isinstance(value, list):
        return [_strip_vendors(v) for v in value]
    if isinstance(value, dict):
        return {k: _strip_vendors(v) for k, v in value.items()}
    return value


@router.post("/session")
async def create_session(body: SessionCreate):
    """Idempotent session create. The observer calls this before its first tool."""
    sb = get_supabase()
    existing = await sb.table("live_sessions").select("session_id").eq(
        "session_id", body.session_id
    ).execute()
    created_at = datetime.now(timezone.utc).isoformat()
    if not existing.data:
        await sb.table("live_sessions").insert({
            "session_id": body.session_id,
            "created_at": created_at,
        }).execute()
    return {"session_id": body.session_id, "created_at": created_at}


@router.post("/event")
async def log_event(body: EventLog):
    """Append one event. Creates the session on first write.

    The append happens inside the live_append_event SQL function so two
    in-flight tool calls from the same observer cannot overwrite each other.
    """
    # Guard against oversized events.
    event_str = json.dumps(body.event)
    if len(event_str) > MAX_EVENT_BYTES:
        body.event = _truncate_event(body.event)
    # Individual fields are capped above; this enforces the ceiling on the
    # event as a whole, for payloads whose bulk is structural rather than one
    # long string.
    body.event = _bound_event(body.event)

    sb = get_supabase()
    await sb.rpc("live_append_event", {
        "p_session_id": body.session_id,
        "p_event": body.event,
    }).execute()
    return {"ok": True}


@router.get("/session/{session_id}")
async def get_session(session_id: str):
    sb = get_supabase()
    row = await sb.table("live_sessions").select("*").eq(
        "session_id", session_id
    ).execute()
    if not row.data:
        raise HTTPException(404, "Session not found")
    session = row.data[0]
    if session.get("published"):
        # The row keeps its raw events; stripping is a display concern and
        # applies only once the session is on the Square.
        session["events"] = _strip_vendors(session.get("events") or [])
    return session


@router.post("/publish")
async def publish(body: PublishRequest):
    """Publish a session to the Square. This is where vendor names go away.

    Only the two small flags are written. The events column is deliberately
    never touched: rewriting a large JSONB array here exceeded Supabase's
    statement timeout (Postgres 57014), and it also destroyed the original
    events. Vendor names are removed on the read path instead.
    """
    sb = get_supabase()
    row = await sb.table("live_sessions").select("session_id").eq(
        "session_id", body.session_id
    ).execute()
    if not row.data:
        raise HTTPException(404, "Session not found")

    await sb.table("live_sessions").update(
        {
            "published": True,
            "published_at": datetime.now(timezone.utc).isoformat(),
        },
        # Without this, PostgREST answers with the whole updated row, dragging
        # the events JSONB back over the wire on every publish. Only flags
        # changed, so nothing needs to come back.
        returning="minimal",
    ).eq("session_id", body.session_id).execute()

    return {"ok": True, "url": f"https://cogextai.com/square#{body.session_id}"}


@router.get("/square")
async def square():
    sb = get_supabase()
    rows = await sb.table("live_sessions").select(
        "session_id,events,created_at,published_at"
    ).eq("published", True).order("published_at", desc=True).limit(100).execute()

    sessions = []
    for row in rows.data:
        sessions.append({
            "session_id": row["session_id"],
            "created_at": row["created_at"],
            "published_at": row["published_at"],
            "events": _strip_vendors(row.get("events") or []),
        })

    return {"sessions": sessions}


@router.post("/receipt")
async def store_receipt(receipt: Receipt):
    sb = get_supabase()
    await sb.table("live_receipts").upsert({
        "receipt_id": receipt.receipt_id,
        "session_id": receipt.session_id,
        "tool": receipt.tool,
        "tool_response": receipt.tool_response,
        "agent_claim": receipt.agent_claim,
        "verdict": receipt.verdict,
        "reason": receipt.reason,
        "recorded_at": receipt.recorded_at,
        "signature": receipt.signature,
    }).execute()
    return {"ok": True, "verify_url": receipt.verify_url}


@router.get("/receipt/{receipt_id}")
async def get_receipt(receipt_id: str):
    sb = get_supabase()
    row = await sb.table("live_receipts").select("*").eq(
        "receipt_id", receipt_id
    ).execute()
    if not row.data:
        raise HTTPException(404, "Receipt not found")
    return row.data[0]


@router.get("/receipt/{receipt_id}/verify")
async def verify_receipt_route(receipt_id: str):
    receipt = await get_receipt(receipt_id)
    return {"valid": _verify(receipt)}
