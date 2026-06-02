"""Integration tests for the expiry lifecycle — requires RUN_DB_TESTS=true."""
import uuid

import pytest

from app.core.lifecycle import mark_expired_commitments
from app.db.connection import get_supabase
from app.tests.conftest import TEST_SOURCE_AGENT_ID, TEST_USER_ID, skip_without_db

_PAST = "2020-01-01T00:00:00+00:00"
_FUTURE = "2099-12-31T00:00:00+00:00"


async def _insert(*, status: str, due_type: str, deadline: str | None) -> uuid.UUID:
    cid = uuid.uuid4()
    due = {
        "type": due_type,
        "deadline": deadline,
        "trigger_description": "test trigger",
        "entity_ref": None,
        "match_threshold": 0.88,
        "partial_match_threshold": 0.65,
    }
    await get_supabase().table("commitments").insert({
        "id": str(cid),
        "user_id": str(TEST_USER_ID),
        "source_agent_id": str(TEST_SOURCE_AGENT_ID),
        "promise_text": "I will do the thing",
        "due_condition": due,
        "status": status,
        "confidence": 0.95,
        "idempotency_key": f"test-lifecycle-{cid}",
    }).execute()
    return cid


async def _get_status(cid: uuid.UUID) -> str:
    resp = await get_supabase().table("commitments").select("status").eq("id", str(cid)).execute()
    return resp.data[0]["status"]


@skip_without_db
@pytest.mark.asyncio
async def test_marks_overdue_time_commitments_expired(clean_db, db_pool):
    cid = await _insert(status="open", due_type="time", deadline=_PAST)
    count = await mark_expired_commitments()
    assert count >= 1
    assert await _get_status(cid) == "expired"


@skip_without_db
@pytest.mark.asyncio
async def test_does_not_expire_future_deadline(clean_db, db_pool):
    cid = await _insert(status="open", due_type="time", deadline=_FUTURE)
    await mark_expired_commitments()
    assert await _get_status(cid) == "open"


@skip_without_db
@pytest.mark.asyncio
async def test_does_not_touch_fulfilled_commitments(clean_db, db_pool):
    cid = await _insert(status="fulfilled", due_type="time", deadline=_PAST)
    await mark_expired_commitments()
    assert await _get_status(cid) == "fulfilled"


@skip_without_db
@pytest.mark.asyncio
async def test_does_not_touch_event_implicit_commitments(clean_db, db_pool):
    cid = await _insert(status="open", due_type="event_implicit", deadline=None)
    await mark_expired_commitments()
    assert await _get_status(cid) == "open"


@skip_without_db
@pytest.mark.asyncio
async def test_does_not_touch_event_external_commitments(clean_db, db_pool):
    cid = await _insert(status="open", due_type="event_external", deadline=None)
    await mark_expired_commitments()
    assert await _get_status(cid) == "open"


@skip_without_db
@pytest.mark.asyncio
async def test_returns_count_of_affected_rows(clean_db, db_pool):
    await _insert(status="open", due_type="time", deadline=_PAST)
    await _insert(status="open", due_type="time", deadline=_PAST)
    await _insert(status="open", due_type="time", deadline=_FUTURE)  # should NOT expire

    count = await mark_expired_commitments()
    assert count == 2
