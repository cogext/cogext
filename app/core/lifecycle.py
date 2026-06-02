import logging

from fastapi import APIRouter, HTTPException

from app.db.connection import get_pool

logger = logging.getLogger(__name__)
router = APIRouter()


async def mark_expired_commitments() -> int:
    pool = get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            """
            UPDATE commitments
               SET status = 'expired',
                   resolved_at = NOW(),
                   updated_at = NOW()
             WHERE status = 'open'
               AND (due_condition->>'type') = 'time'
               AND (due_condition->>'deadline')::timestamptz < NOW()
            """
        )
    # asyncpg returns "UPDATE N" as a string
    count = int(result.split()[-1])
    logger.info("mark_expired_commitments: %d row(s) expired", count)
    return count


@router.post("/admin/run-expiry")
async def run_expiry() -> dict:
    try:
        expired_count = await mark_expired_commitments()
    except Exception as e:
        logger.error("run-expiry failed: %s", e)
        raise HTTPException(status_code=500, detail="Expiry job failed")
    return {"expired_count": expired_count}
