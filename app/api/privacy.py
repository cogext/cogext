"""V1.7 – Privacy / redaction API."""
import logging
import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.core.privacy import redact_commitment, redact_text

logger = logging.getLogger(__name__)
router = APIRouter()


class RedactRequest(BaseModel):
    text: str | None = None


@router.post("/commitments/{commitment_id}/redact")
async def redact_commitment_endpoint(commitment_id: uuid.UUID) -> dict:
    try:
        return await redact_commitment(str(commitment_id))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error("redaction failed cid=%s: %s", commitment_id, e)
        raise HTTPException(status_code=500, detail="Redaction failed")


@router.post("/redact/text")
async def redact_text_endpoint(body: RedactRequest) -> dict:
    if not body.text:
        return {"redacted": "", "types_found": []}
    redacted, types_found = redact_text(body.text)
    return {"redacted": redacted, "types_found": types_found}
