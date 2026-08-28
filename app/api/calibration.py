"""V1.7 – Confidence calibration API."""
import logging
import uuid

from fastapi import APIRouter, HTTPException

from app.core.calibration import get_calibration_report

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/calibration")
async def calibration(user_id: uuid.UUID | None = None) -> dict:
    try:
        return await get_calibration_report(str(user_id) if user_id else None)
    except Exception as e:
        logger.error("calibration report failed: %s", e)
        raise HTTPException(status_code=500, detail="Failed to generate calibration report")
