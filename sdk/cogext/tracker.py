from __future__ import annotations

import asyncio
import inspect
import logging
import threading
import uuid
from typing import Any

from .client import CogextClient
from .exceptions import CogextConfigError

logger = logging.getLogger(__name__)

_INTERCEPT = frozenset({"run", "invoke", "chat", "complete"})


def track(
    agent: Any,
    api_key: str,
    user_id: str | uuid.UUID,
    agent_id: str | uuid.UUID,
    base_url: str | None = None,
) -> "TrackedAgent":
    kwargs: dict[str, Any] = {"api_key": api_key, "user_id": user_id}
    if base_url:
        kwargs["base_url"] = base_url
    client = CogextClient(**kwargs)
    return TrackedAgent(agent, client, str(agent_id))


class TrackedAgent:
    """Transparent proxy that intercepts agent output and ingests it to COGEXT."""

    __slots__ = ("_agent", "_client", "_agent_id")

    def __init__(self, agent: Any, client: CogextClient, agent_id: str) -> None:
        object.__setattr__(self, "_agent", agent)
        object.__setattr__(self, "_client", client)
        object.__setattr__(self, "_agent_id", agent_id)

    # ----- transparent attribute delegation -----

    def __getattr__(self, name: str) -> Any:
        agent = object.__getattribute__(self, "_agent")
        attr = getattr(agent, name)
        if name in _INTERCEPT and callable(attr):
            client = object.__getattribute__(self, "_client")
            agent_id = object.__getattribute__(self, "_agent_id")
            return _make_wrapper(attr, client, agent_id)
        return attr

    def __setattr__(self, name: str, value: Any) -> None:
        agent = object.__getattribute__(self, "_agent")
        setattr(agent, name, value)

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        agent = object.__getattribute__(self, "_agent")
        client = object.__getattribute__(self, "_client")
        agent_id = object.__getattribute__(self, "_agent_id")
        if not callable(agent):
            raise TypeError(f"{agent!r} is not callable")
        return _make_wrapper(agent, client, agent_id)(*args, **kwargs)

    def __repr__(self) -> str:
        agent = object.__getattribute__(self, "_agent")
        return f"TrackedAgent({agent!r})"


# ----- wrapping helpers -----

def _make_wrapper(method: Any, client: CogextClient, agent_id: str):
    if inspect.iscoroutinefunction(method):
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            # Inject open commitments into context before the call (best-effort)
            kwargs = await _inject_context(kwargs, client)
            result = await method(*args, **kwargs)
            if isinstance(result, str) and result.strip():
                _fire_and_forget(client, agent_id, result)
            return result
        return async_wrapper
    else:
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            # Context injection requires async — skip for sync agents
            result = method(*args, **kwargs)
            if isinstance(result, str) and result.strip():
                _fire_and_forget(client, agent_id, result)
            return result
        return sync_wrapper


async def _inject_context(kwargs: dict, client: CogextClient) -> dict:
    """Prepend open commitments to a 'context' or 'system_prompt' kwarg if present."""
    try:
        commitments = await client.get_commitments()
        if not commitments:
            return kwargs
        lines = ["[COGEXT: open commitments]"]
        for c in commitments:
            lines.append(f"- {c.get('promise_text', '')} (status: {c.get('status', '')})")
        header = "\n".join(lines) + "\n\n"

        for key in ("context", "system_prompt", "system"):
            if key in kwargs and isinstance(kwargs[key], str):
                kwargs[key] = header + kwargs[key]
                return kwargs
    except Exception as e:
        logger.warning("cogext context injection failed: %s", e)
    return kwargs


def _fire_and_forget(client: CogextClient, agent_id: str, message: str) -> None:
    """Schedule ingest without blocking. Works in both sync and async contexts."""
    async def _ingest() -> None:
        try:
            await client.ingest(source_agent_id=agent_id, message=message)
            logger.debug("cogext ingest ok agent_id=%s", agent_id)
        except Exception as e:
            logger.warning("cogext background ingest failed: %s", e)

    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_ingest())
    except RuntimeError:
        # No running event loop — use a daemon thread
        def _run() -> None:
            asyncio.run(_ingest())
        threading.Thread(target=_run, daemon=True).start()
