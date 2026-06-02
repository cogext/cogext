import json
import logging
import uuid

from fastapi import APIRouter, HTTPException

from app.core.extractor import extract_commitments
from app.core.scorer import route_by_confidence
from app.db.connection import get_pool
from app.models.commitment import Commitment, IngestRequest, IngestResponse

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/ingest", response_model=IngestResponse)
async def ingest(body: IngestRequest) -> IngestResponse:
    pool = get_pool()

    async with pool.acquire() as conn:
        # Step 1: log raw message to episodic_log
        trace_id = uuid.uuid4()
        try:
            await conn.execute(
                """
                INSERT INTO episodic_log (id, user_id, agent_id, trace_id, raw_content)
                VALUES ($1, $2, $3, $4, $5)
                """,
                uuid.uuid4(),
                body.user_id,
                body.source_agent_id,
                trace_id,
                body.message,
            )
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

        # Step 4: persist each commitment (idempotent)
        saved: list[Commitment] = []
        for c in commitments:
            try:
                await conn.execute(
                    """
                    INSERT INTO commitments (
                        id, user_id, source_agent_id, target_agent_id,
                        record_key, promise_text, due_condition, status,
                        confidence, idempotency_key
                    )
                    VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8, $9, $10)
                    ON CONFLICT (idempotency_key) DO NOTHING
                    """,
                    c.id,
                    c.user_id,
                    c.source_agent_id,
                    c.target_agent_id,
                    c.record_key,
                    c.promise_text,
                    json.dumps(c.due_condition.model_dump()),
                    c.status,
                    c.confidence,
                    c.idempotency_key,
                )
                saved.append(c)
                logger.info("commitment saved id=%s status=%s", c.id, c.status)
            except Exception as e:
                logger.error("commitment insert failed id=%s: %s", c.id, e)
                raise HTTPException(status_code=500, detail="Failed to save commitment")

    return IngestResponse(commitments=saved)
