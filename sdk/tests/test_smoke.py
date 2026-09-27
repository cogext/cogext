import asyncio
import os
import time

import pytest

from cogext import CogextClient, track

_USER_ID = "11111111-1111-1111-1111-111111111111"
_AGENT_ID = "22222222-2222-2222-2222-222222222222"
_API_KEY = "test-key"
_BASE_URL = "http://localhost:8000/api/v1"

# The end-to-end test needs a live API on _BASE_URL. Gate it behind an env var,
# mirroring how the backend gates its database tests behind RUN_DB_TESTS.
_RUN_E2E = os.environ.get("RUN_E2E_TESTS", "").lower() == "true"
skip_without_api = pytest.mark.skipif(
    not _RUN_E2E,
    reason="RUN_E2E_TESTS not set — requires a live API on localhost:8000",
)


class FakeAgent:
    def run(self, prompt: str) -> str:
        return "I will send the status report by Friday end of day"


@pytest.fixture
def client():
    return CogextClient(api_key=_API_KEY, user_id=_USER_ID, base_url=_BASE_URL)


def test_track_does_not_raise():
    """track() wraps an agent without changing its return value."""
    agent = FakeAgent()
    tracked = track(
        agent, api_key=_API_KEY, user_id=_USER_ID, agent_id=_AGENT_ID, base_url=_BASE_URL
    )
    result = tracked.run("write a summary")
    assert result == "I will send the status report by Friday end of day"


def test_track_passes_through_output():
    """The wrapped agent's output is returned verbatim to the caller."""
    agent = FakeAgent()
    tracked = track(
        agent, api_key=_API_KEY, user_id=_USER_ID, agent_id=_AGENT_ID, base_url=_BASE_URL
    )
    assert tracked.run("anything") == agent.run("anything")


@skip_without_api
def test_track_and_ingest_end_to_end():
    """Run a tracked agent, wait for the background ingest, then read it back."""
    agent = FakeAgent()
    tracked = track(
        agent, api_key=_API_KEY, user_id=_USER_ID, agent_id=_AGENT_ID, base_url=_BASE_URL
    )

    result = tracked.run("please send a summary")
    assert isinstance(result, str)

    # Give the background ingest thread time to complete
    time.sleep(2)

    async def check():
        client = CogextClient(api_key=_API_KEY, user_id=_USER_ID, base_url=_BASE_URL)
        commitments = await client.get_commitments(source_agent_id=_AGENT_ID)
        return commitments

    commitments = asyncio.run(check())
    assert len(commitments) >= 1, f"Expected at least 1 commitment, got: {commitments}"


def test_config_error_missing_api_key():
    from cogext.exceptions import CogextConfigError
    with pytest.raises(CogextConfigError):
        CogextClient(api_key="", user_id=_USER_ID)


def test_config_error_bad_user_id():
    from cogext.exceptions import CogextConfigError
    with pytest.raises(CogextConfigError):
        CogextClient(api_key=_API_KEY, user_id="not-a-uuid")
