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


@pytest.fixture
def auth_override():
    """Bypass API-key auth for validation-only tests.

    Protected routers are mounted with `Depends(get_current_account)` in
    app/main.py, which raises 401 *before* request validation runs. Tests that
    assert on 422 responses must therefore authenticate first. No DB hit is
    needed because the real dependency is replaced outright.
    """
    from app.core.auth import Account, get_current_account
    from app.main import app

    def _fake_account() -> Account:
        return Account(
            account_id=str(TEST_USER_ID),
            email="test@example.com",
            key_id=str(TEST_USER_ID),
        )

    app.dependency_overrides[get_current_account] = _fake_account
    yield
    app.dependency_overrides.pop(get_current_account, None)


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
        sb.rpc("cogext_test_cleanup", {"p_user_id": str(TEST_USER_ID)}).execute()

    _wipe()
    yield
    _wipe()
