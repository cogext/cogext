"""V1.6 – Reliability metrics API."""
import logging
import uuid
from datetime import datetime

from fastapi import APIRouter, HTTPException, Query

from app.core.reliability import get_reliability_metrics

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/reliability")
async def reliability(
    user_id: uuid.UUID,
    source_agent_id: uuid.UUID | None = None,
    since: datetime | None = None,
) -> dict:
    try:
        return await get_reliability_metrics(user_id, source_agent_id, since)
    except Exception as e:
        logger.error("reliability metrics failed: %s", e)
        raise HTTPException(status_code=500, detail="Failed to compute reliability metrics")
