"""V1.6 – Reliability metrics API."""
import logging
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.auth import Account, get_current_account
from app.core.reliability import get_reliability_metrics

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/reliability")
async def reliability(
    source_agent_id: uuid.UUID | None = None,
    since: datetime | None = None,
    account: Account = Depends(get_current_account),
) -> dict:
    """Return reliability metrics for the authenticated account.

    Optionally filter by a specific agent via source_agent_id.
    user_id is always scoped to the authenticated API key — never caller-supplied.
    """
    try:
        return await get_reliability_metrics(
            uuid.UUID(account.account_id),
            source_agent_id,
            since,
        )
    except Exception as e:
        logger.error("reliability metrics failed: %s", e)
        raise HTTPException(status_code=500, detail="Failed to compute reliability metrics")
