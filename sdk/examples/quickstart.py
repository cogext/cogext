
"""
COGEXT quickstart — no external agent framework required.

Run:
    python examples/quickstart.py

Prerequisites:
    - COGEXT_BASE_URL defaults to https://cogext.onrender.com/api/v1 (production)
    - pip install -e sdk/
"""

import asyncio
import os
import time

from cogext import CogextClient, track

API_KEY = os.environ.get("COGEXT_API_KEY", "demo-key")
USER_ID = os.environ.get("COGEXT_USER_ID", "11111111-1111-1111-1111-111111111111")
AGENT_ID = os.environ.get("COGEXT_AGENT_ID", "22222222-2222-2222-2222-222222222222")
BASE_URL = os.environ.get("COGEXT_BASE_URL", "https://cogext.onrender.com/api/v1")


class SimpleAgent:
    """A plain Python 'agent' — no frameworks, just a class with a run() method."""

    def run(self, prompt: str) -> str:
        print(f"[agent] received prompt: {prompt!r}")
        return (
            "I'll have the proposal draft ready by Thursday morning. "
            "Once the design team approves the mockups I'll send the final version to the client."
        )


def main() -> None:
    agent = SimpleAgent()
    tracked = track(
        agent,
        api_key=API_KEY,
        user_id=USER_ID,
        agent_id=AGENT_ID,
        base_url=BASE_URL,
    )

    print("--- Calling tracked agent ---")
    output = tracked.run("Draft a proposal for the Q3 campaign")
    print(f"[output] {output}\n")

    print("Waiting for background ingest to complete...")
    time.sleep(2)

    async def show_commitments() -> None:
        client = CogextClient(api_key=API_KEY, user_id=USER_ID, base_url=BASE_URL)
        commitments = await client.get_commitments(source_agent_id=AGENT_ID, status="open")
        print(f"--- Open commitments ({len(commitments)}) ---")
        for c in commitments:
            print(f"  [{c['status']}] {c['promise_text']}")
            dc = c.get("due_condition", {})
            if dc.get("trigger_description"):
                print(f"         trigger: {dc['trigger_description']}")
            print(f"         confidence: {c['confidence']:.2f}")

    asyncio.run(show_commitments())


if __name__ == "__main__":
    main()
