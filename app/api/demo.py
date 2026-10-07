from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import Optional
import re, hashlib, uuid
from datetime import datetime, timezone

from app.core.temporal import resolve_deadline

router = APIRouter(prefix="/demo", tags=["demo"])


class TrackRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000)


class CommitmentObject(BaseModel):
    id: str
    obligation: str
    deadline_expression: Optional[str]
    deadline_resolved: Optional[str]
    state: str
    content_hash: str
    created_at: str


class TrackResponse(BaseModel):
    commitments: list[CommitmentObject]


_REFUND = re.compile(r"refund\s+(?:\w+\s+)*?\$([\d,]+(?:\.\d{2})?)", re.I)
_SEND = re.compile(r"\bsend\s+(?:the\s+)?(.+?)\s+to\s+\S+", re.I)
_DEPLOY = re.compile(r"\bdeploy\s+(?:the\s+)?(.+?)\s+to\s+\S+", re.I)
_UPGRADE = re.compile(r"(upgrade|downgrade|scheduled?)\s+(?:to\s+)?(?:tier\s+)?(\w+)", re.I)
_MIGRATE = re.compile(r"(migrate|snapshot|backup)\s+(?:the\s+)?(db|database|cluster)?", re.I)
_PART_OF_DAY = r"(?:morning|afternoon|evening|night)"
_DAY_QUALIFIER = r"(?:\s+(?:" + _PART_OF_DAY + r"|EOD|end of day))?"

_BY_TIME = re.compile(
    r"(?:by\s+)?("
    r"end of (?:day|billing cycle)"
    r"|\d{1,2}(?::\d{2})?\s*(?:am|pm)(?:\s+(?:today|tomorrow))?"
    r"|\w+day" + _DAY_QUALIFIER +
    r"|tomorrow" + _DAY_QUALIFIER +
    r"|EOD"
    r")",
    re.I,
)

_CLOCK_TIME = re.compile(r"\d{1,2}(?::\d{2})?\s*(?:am|pm)|\d{1,2}:\d{2}", re.I)
_PART_OF_DAY_WORD = re.compile(r"\b" + _PART_OF_DAY + r"\b", re.I)


def _is_ambiguous(expression: str) -> bool:
    """A part-of-day qualifier with no clock time cannot be resolved."""
    return bool(_PART_OF_DAY_WORD.search(expression)) and not _CLOCK_TIME.search(expression)


_CLOCK_OR_EOD = re.compile(
    r"\d{1,2}(?::\d{2})?\s*(?:am|pm)"
    r"|\d{1,2}:\d{2}"
    r"|\bEOD\b"
    r"|\bend of day\b",
    re.I,
)

_HEDGE = re.compile(
    r"\b(?:maybe|might|could|perhaps|possibly)\b"
    r"|\bthink about\b"
    r"|\bat some point\b"
    r"|\beventually\b",
    re.I,
)


def _extract(raw: str) -> Optional[dict]:
    """
    Extract a commitment from raw text.

    content_hash = sha256(f"{text}|{obligation}|{deadline}")[:32]

    The hash is stable for identical input. It exists so a caller can identify
    the same commitment across calls without trusting a database.
    """
    text = raw.strip()
    obligation = None
    deadline = None

    if _HEDGE.search(text):
        return None

    m = _REFUND.search(text)
    if m:
        obligation = f"refund ${m.group(1)}"
    elif _SEND.search(text):
        obligation = f"send {_SEND.search(text).group(1)}"
    elif _DEPLOY.search(text):
        obligation = f"deploy {_DEPLOY.search(text).group(1)}"
    elif _UPGRADE.search(text):
        action, target = _UPGRADE.search(text).groups()
        obligation = f"{action.lower()} plan {target}"
    elif _MIGRATE.search(text):
        verb, target = _MIGRATE.search(text).groups()
        obligation = f"{verb.lower()} {target or 'database'}"

    if obligation is None:
        return None

    dl = _BY_TIME.search(text)
    if dl:
        deadline = dl.group(1)

    canonical = f"{text}|{obligation}|{deadline}"
    content_hash = hashlib.sha256(canonical.encode()).hexdigest()[:32]

    return {
        "id": str(uuid.uuid4()),
        "obligation": obligation,
        "deadline_expression": deadline,
        "deadline_resolved": None,
        "state": "AWAITING_EXTERNAL_EVIDENCE",
        "content_hash": content_hash,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


@router.post("/track", response_model=TrackResponse)
async def track(payload: TrackRequest):
    if not payload.message.strip():
        raise HTTPException(status_code=400, detail="message is empty")
    commitment = _extract(payload.message)
    if commitment is None:
        return TrackResponse(commitments=[])

    expression = commitment["deadline_expression"]
    if expression and not _is_ambiguous(expression):
        resolution = resolve_deadline(
            expression,
            datetime.now(timezone.utc),
            "UTC",
            allow_llm_fallback=False,
        )
        if resolution.resolved_deadline and not _CLOCK_OR_EOD.search(expression):
            commitment["deadline_resolved"] = (
                resolution.resolved_deadline.isoformat().replace("+00:00", "Z")
            )

    return TrackResponse(commitments=[commitment])
