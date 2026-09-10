"""Public Audit Receipt API — V2.0.

POST /commitments/{id}/receipt  → generate & store receipt token (auth required)
GET  /audit/{token}             → public, no auth — returns commitment snapshot
"""
from __future__ import annotations

import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.core.auth import Account, get_current_account
from app.core.receipt import generate_receipt_token, verify_receipt_token
from app.db.connection import get_supabase

logger = logging.getLogger(__name__)
router = APIRouter()


class ReceiptResponse(BaseModel):
    receipt_token: str
    audit_url: str


class AuditSnapshot(BaseModel):
    commitment_id: str
    promise_text: str
    status: str
    confidence: float
    created_at: str | None
    deadline: str | None
    agent_id: str
    receipt_valid: bool


# ── Protected: generate receipt ──────────────────────────────────────────────

@router.post("/commitments/{commitment_id}/receipt", response_model=ReceiptResponse)
async def create_receipt(
    commitment_id: UUID,
    account: Account = Depends(get_current_account),
) -> ReceiptResponse:
    sb = get_supabase()

    row = await (
        sb.table("commitments")
        .select("id,user_id,promise_text,created_at,receipt_token")
        .eq("id", str(commitment_id))
        .eq("user_id", account.account_id)
        .maybe_single()
        .execute()
    )
    if not row or not row.data:
        raise HTTPException(status_code=404, detail="Commitment not found")

    data = row.data

    # Return existing token if already generated
    if data.get("receipt_token"):
        token = data["receipt_token"]
    else:
        token = generate_receipt_token(
            commitment_id=data["id"],
            user_id=data["user_id"],
            promise_text=data["promise_text"],
            created_at=str(data.get("created_at", "")),
        )
        await sb.table("commitments").update({"receipt_token": token}).eq("id", data["id"]).execute()

    return ReceiptResponse(
        receipt_token=token,
        audit_url=f"https://cogextai.com/receipt?token={token}",
    )


# ── Public: verify + display receipt ────────────────────────────────────────

@router.get("/audit/{token}", response_model=AuditSnapshot)
async def get_audit(token: str) -> AuditSnapshot:
    sb = get_supabase()

    row = await (
        sb.table("commitments")
        .select("id,user_id,promise_text,status,confidence,created_at,due_condition,source_agent_id,receipt_token")
        .eq("receipt_token", token)
        .maybe_single()
        .execute()
    )
    if not row or not row.data:
        raise HTTPException(status_code=404, detail="Receipt not found or invalid")

    data = row.data
    due = data.get("due_condition") or {}
    deadline = due.get("deadline") if isinstance(due, dict) else None

    valid = verify_receipt_token(
        token=token,
        commitment_id=data["id"],
        user_id=data["user_id"],
        promise_text=data["promise_text"],
        created_at=str(data.get("created_at", "")),
    )

    return AuditSnapshot(
        commitment_id=data["id"],
        promise_text=data["promise_text"],
        status=data["status"],
        confidence=data["confidence"],
        created_at=data.get("created_at"),
        deadline=deadline,
        agent_id=data.get("source_agent_id", ""),
        receipt_valid=valid,
    )
