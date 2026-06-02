from __future__ import annotations

import uuid
from typing import Any

import httpx

from .exceptions import CogextAPIError, CogextConfigError

_DEFAULT_BASE_URL = "http://localhost:8000/api/v1"


class CogextClient:
    def __init__(
        self,
        api_key: str,
        user_id: uuid.UUID | str,
        base_url: str = _DEFAULT_BASE_URL,
        timeout: float = 5.0,
    ) -> None:
        if not api_key or not isinstance(api_key, str):
            raise CogextConfigError("api_key must be a non-empty string")
        try:
            self._user_id = str(uuid.UUID(str(user_id)))
        except (ValueError, AttributeError):
            raise CogextConfigError(f"user_id is not a valid UUID: {user_id!r}")

        self._base_url = base_url.rstrip("/")
        self._headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        self._timeout = timeout

    async def ingest(
        self,
        source_agent_id: str | uuid.UUID,
        message: str,
        target_agent_id: str | uuid.UUID | None = None,
        record_key: str | None = None,
    ) -> list[dict[str, Any]]:
        payload: dict[str, Any] = {
            "user_id": self._user_id,
            "source_agent_id": str(source_agent_id),
            "message": message,
        }
        if target_agent_id is not None:
            payload["target_agent_id"] = str(target_agent_id)
        if record_key is not None:
            payload["record_key"] = record_key

        async with httpx.AsyncClient(timeout=self._timeout) as http:
            resp = await http.post(
                f"{self._base_url}/ingest",
                json=payload,
                headers=self._headers,
            )
        _raise_for_status(resp)
        return resp.json().get("commitments", [])

    async def get_commitments(
        self,
        source_agent_id: str | uuid.UUID | None = None,
        target_agent_id: str | uuid.UUID | None = None,
        record_key: str | None = None,
        status: str = "open",
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "user_id": self._user_id,
            "status": status,
            "limit": limit,
        }
        if source_agent_id is not None:
            params["source_agent_id"] = str(source_agent_id)
        if target_agent_id is not None:
            params["target_agent_id"] = str(target_agent_id)
        if record_key is not None:
            params["record_key"] = record_key

        async with httpx.AsyncClient(timeout=self._timeout) as http:
            resp = await http.get(
                f"{self._base_url}/commitments",
                params=params,
                headers=self._headers,
            )
        _raise_for_status(resp)
        return resp.json().get("commitments", [])

    async def update_status(
        self,
        commitment_id: str | uuid.UUID,
        status: str,
    ) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self._timeout) as http:
            resp = await http.patch(
                f"{self._base_url}/commitments/{commitment_id}",
                json={"status": status},
                headers=self._headers,
            )
        _raise_for_status(resp)
        return resp.json()


def _raise_for_status(resp: httpx.Response) -> None:
    if resp.status_code >= 400:
        try:
            detail = resp.json().get("detail", resp.text)
        except Exception:
            detail = resp.text
        raise CogextAPIError(status_code=resp.status_code, message=str(detail))
