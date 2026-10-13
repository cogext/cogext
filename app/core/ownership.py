"""Resource ownership checks for authenticated routes.

Authentication (app.core.auth) proves which account is calling. It does not
prove the caller owns the record named in the URL; every route that loads a
commitment by id must also scope the lookup to the caller's account.

A commitment belongs to the account whose id is stored in commitments.user_id
(set from the API key at ingest). A record owned by another account gets the
same 404 as a record that does not exist, so callers cannot probe for ids.
"""
from typing import Any

from fastapi import HTTPException

from app.core.auth import Account


async def get_owned_commitment(sb: Any, commitment_id: Any, account: Account, columns: str = "*") -> dict:
    """Return the commitment row if the caller owns it, else raise 404.

    `sb` is the caller's Supabase client, so routes keep resolving it the way
    they already do.
    """
    resp = await (
        sb.table("commitments")
        .select(columns)
        .eq("id", str(commitment_id))
        .eq("user_id", account.account_id)
        .execute()
    )
    if not resp or not resp.data:
        raise HTTPException(status_code=404, detail="Commitment not found")
    return resp.data[0]
