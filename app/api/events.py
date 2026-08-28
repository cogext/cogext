"""V1.5 – Events API: read-only access to append-only event stream."""
import logging
import uuid

from fastapi import APIRouter, HTTPException, Query

from app.db.connection import get_supabase
from app.models.event import CommitmentEvent, EventListResponse

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/commitments/{commitment_id}/events", response_model=EventListResponse)
async def get_events(
    commitment_id: uuid.UUID,
    limit: int = Query(default=100, ge=1, le=500),
    event_type: str | None = None,
) -> EventListResponse:
    sb = get_supabase()

    query = sb.table("commitment_events").select("*").eq(
        "commitment_id", str(commitment_id)
    ).order("occurred_at", desc=False).limit(limit)

    if event_type:
        query = query.eq("event_type", event_type)

    try:
        resp = await query.execute()
    except Exception as e:
        logger.error("Events fetch failed: %s", e)
        raise HTTPException(status_code=500, detail="Failed to fetch events")

    events = []
    for row in resp.data or []:
        try:
            events.append(CommitmentEvent.model_validate(row))
        except Exception as e:
            logger.warning("Skipping unparseable event row: %s", e)

    return EventListResponse(events=events, total=len(events))
