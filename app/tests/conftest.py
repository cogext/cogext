import os
import uuid

import pytest
import pytest_asyncio
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient
from supabase import create_client

from app.main import app
from config import settings

TEST_USER_ID = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
TEST_SOURCE_AGENT_ID = uuid.UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
TEST_TARGET_AGENT_ID = uuid.UUID("cccccccc-cccc-cccc-cccc-cccccccccccc")

_DB_TESTS = os.environ.get("RUN_DB_TESTS", "").lower() == "true"
skip_without_db = pytest.mark.skipif(not _DB_TESTS, reason="RUN_DB_TESTS not set")


@pytest.fixture(scope="session")
def test_user_id() -> uuid.UUID:
    return TEST_USER_ID


@pytest.fixture(scope="session")
def test_source_agent_id() -> uuid.UUID:
    return TEST_SOURCE_AGENT_ID


@pytest_asyncio.fixture
async def test_client():
    async with LifespanManager(app) as manager:
        transport = ASGITransport(app=manager.app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client


@pytest_asyncio.fixture
async def db_pool():
    """Initialize the supabase client for tests that call backend functions without HTTP."""
    from app.db.connection import close_supabase, init_supabase
    await init_supabase()
    yield
    await close_supabase()


@pytest.fixture
def clean_db():
    """Truncate test-owned rows before (and after) each DB test."""
    if not _DB_TESTS:
        yield
        return

    # Sync client so this fixture can run before the app lifespan starts
    sb = create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_ROLE_KEY)

    def _wipe():
        sb.table("commitments").delete().eq("user_id", str(TEST_USER_ID)).execute()
        sb.table("episodic_log").delete().eq("user_id", str(TEST_USER_ID)).execute()

    _wipe()
    yield
    _wipe()
