import logging
import uuid

from fastapi import APIRouter, HTTPException

from app.core.extractor import extract_commitments
from app.core.scorer import route_by_confidence
from app.db.connection import get_supabase
from app.models.commitment import Commitment, IngestRequest, IngestResponse

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/ingest", response_model=IngestResponse)
async def ingest(body: IngestRequest) -> IngestResponse:
    sb = get_supabase()

    # Step 1: log raw message to episodic_log
    trace_id = uuid.uuid4()
    try:
        await sb.table("episodic_log").insert({
            "id": str(uuid.uuid4()),
            "user_id": str(body.user_id),
            "agent_id": str(body.source_agent_id),
            "trace_id": str(trace_id),
            "raw_content": body.message,
        }).execute()
        logger.info("episodic_log written trace_id=%s", trace_id)
    except Exception as e:
        logger.error("episodic_log insert failed: %s", e)
        raise HTTPException(status_code=500, detail="Failed to log message")

    # Step 2: extract commitments from message
    extracted = await extract_commitments(body.message)
    logger.info("extracted %d commitment(s) from message", len(extracted))

    # Step 3: score and build full Commitment objects
    commitments = route_by_confidence(
        extracted,
        user_id=body.user_id,
        source_agent_id=body.source_agent_id,
        target_agent_id=body.target_agent_id,
        record_key=body.record_key,
    )

    # Step 4: persist each commitment (idempotent via upsert ignore_duplicates)
    saved: list[Commitment] = []
    for c in commitments:
        try:
            await sb.table("commitments").upsert(
                {
                    "id": str(c.id),
                    "user_id": str(c.user_id),
                    "source_agent_id": str(c.source_agent_id),
                    "target_agent_id": str(c.target_agent_id) if c.target_agent_id else None,
                    "record_key": c.record_key,
                    "promise_text": c.promise_text,
                    "due_condition": c.due_condition.model_dump(),
                    "status": c.status,
                    "confidence": c.confidence,
                    "idempotency_key": c.idempotency_key,
                },
                on_conflict="idempotency_key",
                ignore_duplicates=True,
            ).execute()
            saved.append(c)
            logger.info("commitment saved id=%s status=%s", c.id, c.status)
        except Exception as e:
            logger.error("commitment insert failed id=%s: %s", c.id, e)
            raise HTTPException(status_code=500, detail="Failed to save commitment")

    return IngestResponse(commitments=saved)
