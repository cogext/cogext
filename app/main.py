import json
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query

from app.api.ingest import router as ingest_router
from app.api.recall import router as recall_router
from app.api.status import router as status_router
from app.core.extractor import extract_commitments
from app.core.lifecycle import router as lifecycle_router
from app.db.connection import close_supabase, get_supabase, init_supabase
from app.llm.provider import extract_completion


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_supabase()
    yield
    await close_supabase()


app = FastAPI(title="COGEXT", version="0.1.0", lifespan=lifespan)
app.include_router(ingest_router, prefix="/api/v1", tags=["ingest"])
app.include_router(recall_router, prefix="/api/v1", tags=["recall"])
app.include_router(status_router, prefix="/api/v1", tags=["status"])
app.include_router(lifecycle_router, prefix="/api/v1", tags=["admin"])


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/db-check")
async def db_check():
    sb = get_supabase()
    await sb.table("commitments").select("id").limit(1).execute()
    return {"db": "ok"}


@app.get("/llm-check")
async def llm_check():
    try:
        result = extract_completion('Return JSON: {"hello": "world"}')
        return {"llm": "ok", "response": json.loads(result)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


_DEFAULT_TEST_MSG = (
    "I'll send the quarterly report by Tuesday end of day and loop in Sarah "
    "after the sync. Once legal review is done I'll forward the contract."
)


@app.get("/extract-test")
async def extract_test(message: str = Query(default=_DEFAULT_TEST_MSG)):
    commitments = await extract_commitments(message)
    return {
        "count": len(commitments),
        "commitments": [c.model_dump() for c in commitments],
    }
