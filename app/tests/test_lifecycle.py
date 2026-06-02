"""Integration tests for the expiry lifecycle — requires RUN_DB_TESTS=true."""
import json
import uuid
from datetime import datetime, timezone

import asyncpg
import pytest

from app.core.lifecycle import mark_expired_commitments
from app.tests.conftest import TEST_SOURCE_AGENT_ID, TEST_USER_ID, skip_without_db
from config import settings

_PAST = "2020-01-01T00:00:00+00:00"
_FUTURE = "2099-12-31T00:00:00+00:00"


async def _insert(conn, *, status: str, due_type: str, deadline: str | None) -> uuid.UUID:
    cid = uuid.uuid4()
    due = {
        "type": due_type,
        "deadline": deadline,
        "trigger_description": "test trigger",
        "entity_ref": None,
        "match_threshold": 0.88,
        "partial_match_threshold": 0.65,
    }
    await conn.execute(
        """
        INSERT INTO commitments
          (id, user_id, source_agent_id, promise_text, due_condition, status, confidence, idempotency_key)
        VALUES ($1,$2,$3,$4,$5::jsonb,$6,$7,$8)
        """,
        cid,
        TEST_USER_ID,
        TEST_SOURCE_AGENT_ID,
        "I will do the thing",
        json.dumps(due),
        status,
        0.95,
        f"test-lifecycle-{cid}",
    )
    return cid


@skip_without_db
@pytest.mark.asyncio
async def test_marks_overdue_time_commitments_expired(clean_db, db_pool):
    conn = await asyncpg.connect(settings.DATABASE_URL, statement_cache_size=0)
    try:
        cid = await _insert(conn, status="open", due_type="time", deadline=_PAST)
    finally:
        await conn.close()

    count = await mark_expired_commitments()
    assert count >= 1

    conn = await asyncpg.connect(settings.DATABASE_URL, statement_cache_size=0)
    row = await conn.fetchrow("SELECT status FROM commitments WHERE id = $1", cid)
    await conn.close()
    assert row["status"] == "expired"


@skip_without_db
@pytest.mark.asyncio
async def test_does_not_expire_future_deadline(clean_db, db_pool):
    conn = await asyncpg.connect(settings.DATABASE_URL, statement_cache_size=0)
    try:
        cid = await _insert(conn, status="open", due_type="time", deadline=_FUTURE)
    finally:
        await conn.close()

    await mark_expired_commitments()

    conn = await asyncpg.connect(settings.DATABASE_URL, statement_cache_size=0)
    row = await conn.fetchrow("SELECT status FROM commitments WHERE id = $1", cid)
    await conn.close()
    assert row["status"] == "open"


@skip_without_db
@pytest.mark.asyncio
async def test_does_not_touch_fulfilled_commitments(clean_db, db_pool):
    conn = await asyncpg.connect(settings.DATABASE_URL, statement_cache_size=0)
    try:
        cid = await _insert(conn, status="fulfilled", due_type="time", deadline=_PAST)
    finally:
        await conn.close()

    await mark_expired_commitments()

    conn = await asyncpg.connect(settings.DATABASE_URL, statement_cache_size=0)
    row = await conn.fetchrow("SELECT status FROM commitments WHERE id = $1", cid)
    await conn.close()
    assert row["status"] == "fulfilled"


@skip_without_db
@pytest.mark.asyncio
async def test_does_not_touch_event_implicit_commitments(clean_db, db_pool):
    conn = await asyncpg.connect(settings.DATABASE_URL, statement_cache_size=0)
    try:
        cid = await _insert(conn, status="open", due_type="event_implicit", deadline=None)
    finally:
        await conn.close()

    await mark_expired_commitments()

    conn = await asyncpg.connect(settings.DATABASE_URL, statement_cache_size=0)
    row = await conn.fetchrow("SELECT status FROM commitments WHERE id = $1", cid)
    await conn.close()
    assert row["status"] == "open"


@skip_without_db
@pytest.mark.asyncio
async def test_does_not_touch_event_external_commitments(clean_db, db_pool):
    conn = await asyncpg.connect(settings.DATABASE_URL, statement_cache_size=0)
    try:
        cid = await _insert(conn, status="open", due_type="event_external", deadline=None)
    finally:
        await conn.close()

    await mark_expired_commitments()

    conn = await asyncpg.connect(settings.DATABASE_URL, statement_cache_size=0)
    row = await conn.fetchrow("SELECT status FROM commitments WHERE id = $1", cid)
    await conn.close()
    assert row["status"] == "open"


@skip_without_db
@pytest.mark.asyncio
async def test_returns_count_of_affected_rows(clean_db, db_pool):
    conn = await asyncpg.connect(settings.DATABASE_URL, statement_cache_size=0)
    try:
        await _insert(conn, status="open", due_type="time", deadline=_PAST)
        await _insert(conn, status="open", due_type="time", deadline=_PAST)
        await _insert(conn, status="open", due_type="time", deadline=_FUTURE)  # should NOT expire
    finally:
        await conn.close()

    count = await mark_expired_commitments()
    assert count == 2
