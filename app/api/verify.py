"""Verifier Engine API — V2.0.

POST /commitments/{id}/verify   → submit evidence and trigger verification
GET  /commitments/{id}/verify   → get current verification status
"""
from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.core.auth import Account, get_current_account
from app.core.verifier import run_verification
from app.db.connection import get_supabase
from app.models.commitment import Commitment

logger = logging.getLogger(__name__)
router = APIRouter()


class VerifyRequest(BaseModel):
    """Raw event from any external system to attempt verification with."""
    raw_event: dict[str, Any]


class VerifyResponse(BaseModel):
    commitment_id: str
    verification_status: str
    score: float
    evidence_id: str | None
    reason: str


class VerifyStatusResponse(BaseModel):
    commitment_id: str
    verification_status: str
    verification_reason: str | None
    evidence_count: int


@router.post("/commitments/{commitment_id}/verify", response_model=VerifyResponse)
async def submit_evidence(
    commitment_id: UUID,
    body: VerifyRequest,
    account: Account = Depends(get_current_account),
) -> VerifyResponse:
    sb = get_supabase()

    row = await (
        sb.table("commitments")
        .select("*")
        .eq("id", str(commitment_id))
        .eq("user_id", account.account_id)
        .maybe_single()
        .execute()
    )
    if not row or not row.data:
        raise HTTPException(status_code=404, detail="Commitment not found")

    commitment = Commitment(**row.data)

    result = await run_verification(
        commitment=commitment,
        raw_event=body.raw_event,
        actor=f"api:account:{account.account_id}",
    )

    return VerifyResponse(
        commitment_id=str(commitment_id),
        verification_status=result["status"],
        score=result["score"],
        evidence_id=result.get("evidence_id"),
        reason=result.get("reason", ""),
    )


@router.get("/commitments/{commitment_id}/verify", response_model=VerifyStatusResponse)
async def get_verify_status(
    commitment_id: UUID,
    account: Account = Depends(get_current_account),
) -> VerifyStatusResponse:
    sb = get_supabase()

    row = await (
        sb.table("commitments")
        .select("id,verification_status,verification_reason")
        .eq("id", str(commitment_id))
        .eq("user_id", account.account_id)
        .maybe_single()
        .execute()
    )
    if not row or not row.data:
        raise HTTPException(status_code=404, detail="Commitment not found")

    # Count evidence records for this commitment
    ev_count_resp = await (
        sb.table("evidence")
        .select("id", count="exact")
        .eq("commitment_id", str(commitment_id))
        .execute()
    )
    ev_count = ev_count_resp.count or 0

    data = row.data
    return VerifyStatusResponse(
        commitment_id=data["id"],
        verification_status=data.get("verification_status", "unverified"),
        verification_reason=data.get("verification_reason"),
        evidence_count=ev_count,
    )
