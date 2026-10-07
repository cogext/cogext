from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import Optional
import re, hashlib, uuid
from datetime import datetime, timezone

router = APIRouter(prefix="/demo", tags=["demo"])


class TrackRequest(BaseModel):
    rawText: str = Field(..., min_length=1, max_length=4000)


class CommitmentObject(BaseModel):
    id: str
    obligation: str
    deadline: Optional[str]
    state: str
    tx_hash: str
    created_at: str


_REFUND = re.compile(r"refund\s+(?:of\s+)?\$?([\d,]+(?:\.\d{2})?)", re.I)
_UPGRADE = re.compile(r"(upgrade|downgrade|scheduled?)\s+(?:to\s+)?(?:tier\s+)?(\w+)", re.I)
_MIGRATE = re.compile(r"(migrate|snapshot|backup)\s+(?:the\s+)?(?:db|database|cluster)?\s*(\S+)?", re.I)
_BY_TIME = re.compile(r"by\s+(\d{1,2}(?::\d{2})?\s*(?:am|pm)?|\w+day|tomorrow|EOD|end of (?:day|billing cycle))", re.I)


def _extract(raw: str) -> dict:
    text = raw.strip()
    obligation = "unclassified_obligation"
    deadline = None

    m = _REFUND.search(text)
    if m:
        obligation = f"refund ${m.group(1)}"
    elif _UPGRADE.search(text):
        action, target = _UPGRADE.search(text).groups()
        obligation = f"{action.lower()} plan {target}"
    elif _MIGRATE.search(text):
        verb, target = _MIGRATE.search(text).groups()
        obligation = f"{verb.lower()} {target or 'database'}"

    dl = _BY_TIME.search(text)
    if dl:
        deadline = dl.group(1)

    canonical = f"{text}|{obligation}|{deadline}"
    tx_hash = hashlib.sha256(canonical.encode()).hexdigest()[:32]

    return {
        "id": str(uuid.uuid4()),
        "obligation": obligation,
        "deadline": deadline,
        "state": "AWAITING_EXTERNAL_EVIDENCE",
        "tx_hash": tx_hash,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


@router.post("/track", response_model=CommitmentObject)
async def track(payload: TrackRequest):
    if not payload.rawText.strip():
        raise HTTPException(status_code=400, detail="rawText is empty")
    return CommitmentObject(**_extract(payload.rawText))
