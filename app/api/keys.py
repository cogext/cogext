"""API key management — signup + rotation."""
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from slowapi import _rate_limit_exceeded_handler
from app.core.rate_limit import limiter
from pydantic import BaseModel, EmailStr

from app.core.auth import Account, generate_api_key, get_current_account
from app.db.connection import get_supabase

logger = logging.getLogger(__name__)
router = APIRouter()


class SignupRequest(BaseModel):
    email: EmailStr
    label: str = "default"


class KeyResponse(BaseModel):
    api_key: str
    account_id: str
    email: str
    label: str
    message: str


@limiter.limit("5/minute")
@router.post("/keys/signup", response_model=KeyResponse, tags=["auth"])
async def signup(request: Request, body: SignupRequest) -> KeyResponse:
    """Generate a new API key. One key per email (idempotent)."""
    sb = get_supabase()

    # Check if email already has a key
    existing = await sb.table("api_keys").select(
        "key, account_id, email, label"
    ).eq("email", body.email).eq("is_active", True).maybe_single().execute()

    if existing and existing.data:
        row = existing.data
        return KeyResponse(
            api_key=row["key"],
            account_id=row["account_id"],
            email=row["email"],
            label=row["label"],
            message="Existing key returned. Keep it secret.",
        )

    key = generate_api_key()
    result = await sb.table("api_keys").insert({
        "key": key,
        "email": body.email,
        "label": body.label,
    }).execute()

    if not result.data:
        raise HTTPException(status_code=500, detail="Failed to create key")

    row = result.data[0]
    return KeyResponse(
        api_key=row["key"],
        account_id=row["account_id"],
        email=row["email"],
        label=row["label"],
        message="API key created. Keep it secret — it won't be shown again.",
    )


@router.get("/keys/me", tags=["auth"])
async def get_my_key(account: Account = Depends(get_current_account)) -> dict:
    """Return info about the current API key."""
    return {
        "account_id": account.account_id,
        "email": account.email,
    }


@router.delete("/keys/me", tags=["auth"])
async def revoke_my_key(account: Account = Depends(get_current_account)) -> dict:
    """Revoke the current API key."""
    sb = get_supabase()
    await sb.table("api_keys").update({"is_active": False}).eq("id", account.key_id).execute()
    return {"revoked": True, "message": "Key revoked. Call /keys/signup to get a new one."}
