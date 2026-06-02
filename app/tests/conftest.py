import os
import uuid

import asyncpg
import pytest
import pytest_asyncio
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

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
    """Initialize the pool directly for tests that call backend functions without HTTP."""
    from app.db.connection import close_pool, init_pool
    await init_pool()
    yield
    await close_pool()


@pytest_asyncio.fixture
async def clean_db():
    """Truncate test-owned rows before each DB test. Only active when RUN_DB_TESTS=true."""
    if not _DB_TESTS:
        yield
        return

    conn = await asyncpg.connect(settings.DATABASE_URL, statement_cache_size=0)
    try:
        await conn.execute(
            "DELETE FROM commitments WHERE user_id = $1", TEST_USER_ID
        )
        await conn.execute(
            "DELETE FROM episodic_log WHERE user_id = $1", TEST_USER_ID
        )
    finally:
        await conn.close()
    yield
    # cleanup after too
    conn = await asyncpg.connect(settings.DATABASE_URL, statement_cache_size=0)
    try:
        await conn.execute(
            "DELETE FROM commitments WHERE user_id = $1", TEST_USER_ID
        )
        await conn.execute(
            "DELETE FROM episodic_log WHERE user_id = $1", TEST_USER_ID
        )
    finally:
        await conn.close()
