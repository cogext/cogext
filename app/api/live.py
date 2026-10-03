"""Live observer API — sessions, events, receipts and the public Square.

Public routes (no API key): the try-observer and the observe decorator
both stream here from processes that hold no COGEXT key, and the two
public pages (/live/:id, /r/:id, /square) read from here.

Vendor names are stripped at publish time only. The original session keeps
whatever the caller sent.
"""
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.db.connection import get_supabase

router = APIRouter()


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
    return row.data[0]


@router.post("/publish")
async def publish(body: PublishRequest):
    """Publish a session to the Square. This is where vendor names go away."""
    sb = get_supabase()
    row = await sb.table("live_sessions").select("events").eq(
        "session_id", body.session_id
    ).execute()
    if not row.data:
        raise HTTPException(404, "Session not found")
    stripped = _strip_vendors(row.data[0]["events"])
    await sb.table("live_sessions").update({
        "events": stripped,
        "published": True,
        "published_at": datetime.now(timezone.utc).isoformat(),
    }).eq("session_id", body.session_id).execute()
    return {"ok": True, "url": f"https://cogextai.com/square#{body.session_id}"}


@router.get("/square")
async def square():
    sb = get_supabase()
    rows = await sb.table("live_sessions").select(
        "session_id,events,created_at,published_at"
    ).eq("published", True).order("published_at", desc=True).limit(100).execute()
    return {"sessions": rows.data}


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
