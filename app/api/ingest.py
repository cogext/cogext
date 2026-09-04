"""V1.9 – Ingest API: user_id auto-scoped from API key; timezone-aware deadlines."""
import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException

from app.core.auth import Account, get_current_account
from app.core.extractor import extract_commitments
from app.core.scorer import route_by_confidence
from app.core.temporal import resolve_deadline
from app.db.connection import get_supabase
from app.models.commitment import Commitment, IngestRequest, IngestResponse

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/ingest", response_model=IngestResponse)
async def ingest(body: IngestRequest, account: Account = Depends(get_current_account)) -> IngestResponse:
    sb = get_supabase()

    # user_id always comes from the API key — ignore whatever is in the body
    user_id = uuid.UUID(account.account_id)

    trace_id = uuid.uuid4()
    now_dt = datetime.now(timezone.utc)
    now = now_dt.isoformat()
    actor_timezone = body.timezone or "UTC"
    try:
        await sb.table("episodic_log").insert({
            "id": str(uuid.uuid4()),
            "user_id": str(user_id),
            "agent_id": str(body.source_agent_id),
            "trace_id": str(trace_id),
            "raw_content": body.message,
        }).execute()
        logger.info("episodic_log written trace_id=%s", trace_id)
    except Exception as e:
        logger.error("episodic_log insert failed: %s", e)
        raise HTTPException(status_code=500, detail="Failed to log message")

    extracted = await extract_commitments(body.message)
    logger.info("extracted %d commitment(s) from message", len(extracted))

    commitments = route_by_confidence(
        extracted,
        user_id=user_id,
        source_agent_id=body.source_agent_id,
        target_agent_id=body.target_agent_id,
        record_key=body.record_key,
        source_type=body.source_type,
        source_timestamp=body.source_timestamp,
        source_message_id=body.source_message_id,
        actor_timezone=actor_timezone,
    )

    # Resolve deadline expressions with actor timezone when LLM left deadline null
    for c in commitments:
        if (
            c.deadline_expression
            and c.due_condition
            and c.due_condition.deadline is None
            and c.due_condition.type == "time"
        ):
            try:
                resolution = resolve_deadline(
                    c.deadline_expression,
                    c.source_timestamp or now_dt,
                    actor_timezone,
                )
                if resolution.resolved_deadline:
                    c.due_condition.deadline = resolution.resolved_deadline
            except Exception as tr_err:
                logger.warning("Temporal resolution failed cid=%s: %s", c.id, tr_err)

    saved: list[Commitment] = []
    for c in commitments:
        try:
            row = {
                "id": str(c.id),
                "user_id": str(user_id),
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
                "conditions": c.conditions or [],
                "status": c.status,
                "confidence": c.confidence,
                "classification": c.classification,
                "idempotency_key": c.idempotency_key,
                "created_at": now,
                "updated_at": now,
                "child_commitment_ids": [str(x) for x in c.child_commitment_ids] if c.child_commitment_ids else [],
                "evidence_requirements": [e.model_dump() for e in c.evidence_requirements] if c.evidence_requirements else [],
                "evidence_found": c.evidence_found or [],
                "verification_status": c.verification_status or "unverified",
                "priority": c.priority or "medium",
                "metadata": c.metadata or {},
                "shape": c.shape,
                "verifier_query": c.verifier_query,
                "timezone": actor_timezone,
            }
            result = await sb.table("commitments").upsert(
                row, on_conflict="idempotency_key", ignore_duplicates=True,
            ).execute()

            is_new = bool(result.data)
            if is_new:
                commitment_to_save = c
            else:
                existing = await sb.table("commitments").select("*") \
                    .eq("idempotency_key", c.idempotency_key).maybe_single().execute()
                if existing and existing.data:
                    commitment_to_save = Commitment(**existing.data)
                else:
                    commitment_to_save = c
                logger.info("commitment deduplicated id=%s", commitment_to_save.id)

            saved.append(commitment_to_save)

            if is_new:
                try:
                    await sb.table("commitment_events").insert({
                        "id": str(uuid.uuid4()),
                        "commitment_id": str(commitment_to_save.id),
                        "event_type": "detected",
                        "actor": "ingest_api",
                        "data": {
                            "confidence": commitment_to_save.confidence,
                            "classification": commitment_to_save.classification,
                            "trace_id": str(trace_id),
                        },
                        "occurred_at": now,
                        "recorded_at": now,
                    }).execute()
                except Exception as ev_err:
                    logger.warning("Event insert failed cid=%s: %s", commitment_to_save.id, ev_err)

        except Exception as e:
            logger.error("commitment insert failed id=%s: %s", c.id, e)
            raise HTTPException(status_code=500, detail="Failed to save commitment")

    return IngestResponse(commitments=saved)
