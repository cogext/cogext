"""COGEXT V1.9 – Application entry point."""
import json
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from app.api.calibration import router as calibration_router
from app.api.dependencies import router as dependencies_router
from app.api.evidence import router as evidence_router
from app.api.events import router as events_router
from app.api.ingest import router as ingest_router
from app.api.keys import router as keys_router
from app.api.privacy import router as privacy_router
from app.api.recall import router as recall_router
from app.api.refinements import router as refinements_router
from app.api.reliability import router as reliability_router
from app.api.reviews import router as reviews_router
from app.api.status import router as status_router
from app.api.webhooks import router as webhooks_router
from app.api.paypal_webhook import router as paypal_webhook_router
from app.core.auth import Account, get_current_account
from app.core.extractor import extract_commitments
from app.core.lifecycle import router as lifecycle_router
from app.db.connection import close_supabase, get_supabase, init_supabase
from app.llm.provider import extract_completion


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_supabase()
    yield
    await close_supabase()


app = FastAPI(title="COGEXT", version="1.9.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://cogextai.com", "https://www.cogextai.com", "https://cogextai.pages.dev"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_V1 = "/api/v1"

# ── Public routes (no auth) ───────────────────────────────────────────────────
app.include_router(keys_router, prefix=_V1)  # /api/v1/keys/signup is public
app.include_router(paypal_webhook_router, prefix=_V1, tags=["billing"])  # public — PayPal webhook

# ── Protected routes (require API key) ────────────────────────────────────────
_auth = {"dependencies": [Depends(get_current_account)]}

app.include_router(ingest_router,       prefix=_V1, tags=["ingest"],       **_auth)
app.include_router(recall_router,       prefix=_V1, tags=["recall"],       **_auth)
app.include_router(status_router,       prefix=_V1, tags=["status"],       **_auth)
app.include_router(lifecycle_router,    prefix=_V1, tags=["admin"],        **_auth)
app.include_router(events_router,       prefix=_V1, tags=["events"],       **_auth)
app.include_router(evidence_router,     prefix=_V1, tags=["evidence"],     **_auth)
app.include_router(dependencies_router, prefix=_V1, tags=["dependencies"], **_auth)
app.include_router(reliability_router,  prefix=_V1, tags=["reliability"],  **_auth)
app.include_router(reviews_router,      prefix=_V1, tags=["reviews"],      **_auth)
app.include_router(webhooks_router,     prefix=_V1, tags=["webhooks"],     **_auth)
app.include_router(calibration_router,  prefix=_V1, tags=["calibration"],  **_auth)
app.include_router(refinements_router,  prefix=_V1, tags=["refinements"],  **_auth)
app.include_router(privacy_router,      prefix=_V1, tags=["privacy"],      **_auth)


@app.get("/")
async def root():
    """Service info — no auth required."""
    return {
        "name": "COGEXT",
        "version": "1.9.0",
        "status": "ok",
        "description": "AI agent commitment-tracking infrastructure",
        "docs": "/docs",
        "health": "/health",
    }


@app.get("/health")
async def health():
    return {"status": "ok", "version": "1.9.0"}


@app.get("/db-check")
async def db_check(_: Account = Depends(get_current_account)):
    sb = get_supabase()
    await sb.table("commitments").select("id").limit(1).execute()
    return {"db": "ok"}


@app.get("/llm-check")
async def llm_check(_: Account = Depends(get_current_account)):
    try:
        result = extract_completion('Return JSON: {"hello": "world"}')
        return {"llm": "ok", "response": json.loads(result)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


_DEFAULT_TEST_MSG = (
    "I'll send the deployment report to Sarah by Friday at 5pm and loop in "
    "the legal team after the sync. Once CI passes I'll merge the PR."
)


@app.get("/extract-test")
async def extract_test(message: str = Query(default=_DEFAULT_TEST_MSG), _: Account = Depends(get_current_account)):
    commitments = await extract_commitments(message)
    return {
        "count": len(commitments),
        "commitments": [c.model_dump() for c in commitments],
    }
