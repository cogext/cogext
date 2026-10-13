"""V1.7 – Confidence calibration API."""
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException

from app.core.auth import Account, get_current_account
from app.core.calibration import get_calibration_report

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/calibration")
async def calibration(
    user_id: uuid.UUID | None = None,
    account: Account = Depends(get_current_account),
) -> dict:
    """Calibration report over the authenticated account's own commitments.

    user_id is still accepted when it names the caller's own account, so
    existing clients keep working; any other value is refused.
    """
    if user_id is not None and str(user_id) != str(uuid.UUID(account.account_id)):
        raise HTTPException(status_code=403, detail="user_id must be the authenticated account")
    try:
        return await get_calibration_report(account.account_id)
    except Exception as e:
        logger.error("calibration report failed: %s", e)
        raise HTTPException(status_code=500, detail="Failed to generate calibration report")
