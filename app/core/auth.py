"""API key authentication for COGEXT."""
import logging
import secrets
from datetime import datetime, timezone

from fastapi import HTTPException, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.db.connection import get_supabase

logger = logging.getLogger(__name__)

_bearer = HTTPBearer(auto_error=False)


class Account:
    def __init__(self, account_id: str, email: str, key_id: str):
        self.account_id = account_id
        self.email = email
        self.key_id = key_id


def generate_api_key() -> str:
    """Generate a new API key like cg_live_<32 hex chars>."""
    return f"cg_live_{secrets.token_hex(16)}"


async def get_current_account(
    credentials: HTTPAuthorizationCredentials | None = Security(_bearer),
) -> Account:
    """FastAPI dependency — validates Bearer token, returns Account."""
    if not credentials or not credentials.credentials:
        raise HTTPException(status_code=401, detail="Missing API key. Pass Authorization: Bearer cg_live_xxx")

    key = credentials.credentials
    if not key.startswith("cg_live_"):
        raise HTTPException(status_code=401, detail="Invalid API key format.")

    sb = get_supabase()
    try:
        result = await sb.table("api_keys").select(
            "id, account_id, email, is_active"
        ).eq("key", key).maybe_single().execute()
    except Exception as e:
        logger.error("auth DB error: %s", e)
        raise HTTPException(status_code=500, detail="Auth check failed")

    if not result or not result.data:
        raise HTTPException(status_code=401, detail="Invalid API key.")

    row = result.data
    if not row["is_active"]:
        raise HTTPException(status_code=403, detail="API key has been revoked.")

    # Update last_used_at in background (fire and forget)
    try:
        await sb.table("api_keys").update(
            {"last_used_at": datetime.now(timezone.utc).isoformat()}
        ).eq("id", row["id"]).execute()
    except Exception:
        pass  # non-critical

    return Account(
        account_id=row["account_id"],
        email=row["email"],
        key_id=row["id"],
    )
