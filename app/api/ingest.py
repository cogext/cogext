"""V1.5 – Ingest API: extract commitments and persist via state machine."""
import logging
import uuid
from datetime import datetime, timezone

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
    now = datetime.now(timezone.utc).isoformat()
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
        source_type=body.source_type,
        source_timestamp=body.source_timestamp,
        source_message_id=body.source_message_id,
    )

    # Step 4: persist each commitment (idempotent via upsert on idempotency_key)
    saved: list[Commitment] = []
    for c in commitments:
        try:
            row = {
                "id": str(c.id),
                "user_id": str(c.user_id),
                "source_agent_id": str(c.source_agent_id),
                "target_agent_id": str(c.target_agent_id) if c.target_agent_id else None,
                "record_key": c.record_key,
                "source_type": c.source_type,
                "source_timestamp": c.source_timestamp.isoformat() if c.source_timestamp else None,
                "source_message_id": c.source_message_id,
                "promise_text": c.promise_text,
                "action": c.action,
                "object": c.object,
                "recipient": c.recipient,
                "due_condition": c.due_condition.model_dump(mode="json"),
                "deadline_expression": c.deadline_expression,
                "conditions": c.conditions,
                "status": c.status,
                "confidence": c.confidence,
                "classification": c.classification,
                "idempotency_key": c.idempotency_key,
                "created_at": now,
                "updated_at": now,
            }
            await sb.table("commitments").upsert(
                row,
                on_conflict="idempotency_key",
                ignore_duplicates=True,
            ).execute()
            saved.append(c)
            logger.info("commitment saved id=%s status=%s", c.id, c.status)

            # Step 5: insert 'detected' event (best-effort)
            try:
                await sb.table("commitment_events").insert({
                    "id": str(uuid.uuid4()),
                    "commitment_id": str(c.id),
                    "event_type": "detected",
                    "actor": "ingest_api",
                    "data": {
                        "confidence": c.confidence,
                        "classification": c.classification,
                        "trace_id": str(trace_id),
                    },
                    "occurred_at": now,
                    "recorded_at": now,
                }).execute()
            except Exception as ev_err:
                logger.warning("Event insert failed cid=%s: %s", c.id, ev_err)

        except Exception as e:
            logger.error("commitment insert failed id=%s: %s", c.id, e)
            raise HTTPException(status_code=500, detail="Failed to save commitment")

    return IngestResponse(commitments=saved)
