"""COGEXT Python SDK Client – V1.7.

Backward-compatible extension of V1.0 client.
All new methods are additive; existing call signatures unchanged.
"""
from __future__ import annotations

import uuid
from typing import Any

import httpx

from .exceptions import CogextAPIError, CogextConfigError

_DEFAULT_BASE_URL = "https://cogext.onrender.com/api/v1"


class CogextClient:
    def __init__(
        self,
        api_key: str,
        user_id: uuid.UUID | str | None = None,  # auto-derived from API key
        base_url: str = _DEFAULT_BASE_URL,
        timeout: float = 5.0,
    ) -> None:
        if not api_key or not isinstance(api_key, str):
            raise CogextConfigError("api_key must be a non-empty string")
        if user_id is not None:
            try:
                self._user_id = str(uuid.UUID(str(user_id)))
            except (ValueError, AttributeError):
                raise CogextConfigError(f"user_id is not a valid UUID: {user_id!r}")
        else:
            self._user_id = None

        self._base_url = base_url.rstrip("/")
        self._headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        self._timeout = timeout

    # -------------------------------------------------------------------------
    # V1.0 – Original methods (unchanged)
    # -------------------------------------------------------------------------

    async def ingest(
        self,
        source_agent_id: str | uuid.UUID,
        message: str,
        target_agent_id: str | uuid.UUID | None = None,
        record_key: str | None = None,
        source_type: str = "agent_message",
        source_timestamp: str | None = None,
        source_message_id: str | None = None,
    ) -> list[dict[str, Any]]:
        payload: dict[str, Any] = {
            "source_agent_id": str(source_agent_id),
            "message": message,
            "source_type": source_type,
        }
        if target_agent_id is not None:
            payload["target_agent_id"] = str(target_agent_id)
        if record_key is not None:
            payload["record_key"] = record_key
        if source_timestamp is not None:
            payload["source_timestamp"] = source_timestamp
        if source_message_id is not None:
            payload["source_message_id"] = source_message_id

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
        actor: str = "api",
        reason: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"status": status, "actor": actor}
        if reason:
            payload["reason"] = reason
        async with httpx.AsyncClient(timeout=self._timeout) as http:
            resp = await http.patch(
                f"{self._base_url}/commitments/{commitment_id}",
                json=payload,
                headers=self._headers,
            )
        _raise_for_status(resp)
        return resp.json()

    # -------------------------------------------------------------------------
    # V1.5 – Events
    # -------------------------------------------------------------------------

    async def get_events(
        self,
        commitment_id: str | uuid.UUID,
        limit: int = 100,
        event_type: str | None = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"limit": limit}
        if event_type:
            params["event_type"] = event_type
        async with httpx.AsyncClient(timeout=self._timeout) as http:
            resp = await http.get(
                f"{self._base_url}/commitments/{commitment_id}/events",
                params=params,
                headers=self._headers,
            )
        _raise_for_status(resp)
        return resp.json().get("events", [])

    # -------------------------------------------------------------------------
    # V1.6 – Evidence
    # -------------------------------------------------------------------------

    async def submit_evidence(
        self,
        commitment_id: str | uuid.UUID,
        source: str,
        data: dict[str, Any],
        occurred_at: str | None = None,
        external_system: str | None = None,
        external_event_id: str | None = None,
        actor: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "commitment_id": str(commitment_id),
            "source": source,
            "data": data,
        }
        if occurred_at:
            payload["occurred_at"] = occurred_at
        if external_system:
            payload["external_system"] = external_system
        if external_event_id:
            payload["external_event_id"] = external_event_id
        if actor:
            payload["actor"] = actor
        async with httpx.AsyncClient(timeout=self._timeout) as http:
            resp = await http.post(
                f"{self._base_url}/commitments/{commitment_id}/evidence",
                json=payload,
                headers=self._headers,
            )
        _raise_for_status(resp)
        return resp.json()

    async def get_evidence_aggregate(
        self, commitment_id: str | uuid.UUID
    ) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self._timeout) as http:
            resp = await http.get(
                f"{self._base_url}/commitments/{commitment_id}/evidence/aggregate",
                headers=self._headers,
            )
        _raise_for_status(resp)
        return resp.json()

    # -------------------------------------------------------------------------
    # V1.6 – Dependencies
    # -------------------------------------------------------------------------

    async def add_dependency(
        self,
        source_commitment_id: str | uuid.UUID,
        target_commitment_id: str | uuid.UUID,
        dependency_type: str = "blocks",
    ) -> dict[str, Any]:
        payload = {
            "source_commitment_id": str(source_commitment_id),
            "target_commitment_id": str(target_commitment_id),
            "dependency_type": dependency_type,
        }
        async with httpx.AsyncClient(timeout=self._timeout) as http:
            resp = await http.post(
                f"{self._base_url}/dependencies",
                json=payload,
                headers=self._headers,
            )
        _raise_for_status(resp)
        return resp.json()

    async def get_dependencies(
        self, commitment_id: str | uuid.UUID
    ) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self._timeout) as http:
            resp = await http.get(
                f"{self._base_url}/commitments/{commitment_id}/dependencies",
                headers=self._headers,
            )
        _raise_for_status(resp)
        return resp.json()

    # -------------------------------------------------------------------------
    # V1.6 – Reliability
    # -------------------------------------------------------------------------

    async def get_reliability(
        self,
        source_agent_id: str | uuid.UUID | None = None,
        since: str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {}
        if source_agent_id:
            params["source_agent_id"] = str(source_agent_id)
        if since:
            params["since"] = since
        async with httpx.AsyncClient(timeout=self._timeout) as http:
            resp = await http.get(
                f"{self._base_url}/reliability",
                params=params,
                headers=self._headers,
            )
        _raise_for_status(resp)
        return resp.json()

    # -------------------------------------------------------------------------
    # V1.7 – Reviews
    # -------------------------------------------------------------------------

    async def create_review(
        self,
        commitment_id: str | uuid.UUID,
        reason_code: str,
        reason: str | None = None,
    ) -> dict[str, Any]:
        payload = {
            "commitment_id": str(commitment_id),
            "reason_code": reason_code,
        }
        if reason:
            payload["reason"] = reason
        async with httpx.AsyncClient(timeout=self._timeout) as http:
            resp = await http.post(
                f"{self._base_url}/reviews",
                json=payload,
                headers=self._headers,
            )
        _raise_for_status(resp)
        return resp.json()

    async def accept_review(
        self,
        review_id: str | uuid.UUID,
        resolution: str | None = None,
    ) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self._timeout) as http:
            resp = await http.post(
                f"{self._base_url}/reviews/{review_id}/accept",
                json={"resolution": resolution, "proposed_changes": {}},
                headers=self._headers,
            )
        _raise_for_status(resp)
        return resp.json()

    # -------------------------------------------------------------------------
    # V1.7 – Webhooks
    # -------------------------------------------------------------------------

    async def create_webhook(
        self,
        endpoint: str,
        secret: str,
        subscribed_event_types: list[str] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "endpoint": endpoint,
            "secret": secret,
            "subscribed_event_types": subscribed_event_types or [],
        }
        async with httpx.AsyncClient(timeout=self._timeout) as http:
            resp = await http.post(
                f"{self._base_url}/webhooks",
                json=payload,
                headers=self._headers,
            )
        _raise_for_status(resp)
        return resp.json()

    async def list_webhooks(self) -> list[dict[str, Any]]:
        async with httpx.AsyncClient(timeout=self._timeout) as http:
            resp = await http.get(
                f"{self._base_url}/webhooks",
                headers=self._headers,
            )
        _raise_for_status(resp)
        return resp.json()

    # -------------------------------------------------------------------------
    # V1.7 – External evidence (adapters)
    # -------------------------------------------------------------------------

    async def submit_external_evidence(
        self,
        commitment_id: str | uuid.UUID,
        external_system: str,
        external_event_id: str,
        data: dict[str, Any],
        source: str | None = None,
        occurred_at: str | None = None,
        actor: str | None = None,
    ) -> dict[str, Any]:
        return await self.submit_evidence(
            commitment_id=commitment_id,
            source=source or f"webhook:{external_system}",
            data=data,
            occurred_at=occurred_at,
            external_system=external_system,
            external_event_id=external_event_id,
            actor=actor,
        )

    # -------------------------------------------------------------------------
    # V1.7 – Calibration
    # -------------------------------------------------------------------------

    async def get_calibration(self) -> dict[str, Any]:
        params = {}
        async with httpx.AsyncClient(timeout=self._timeout) as http:
            resp = await http.get(
                f"{self._base_url}/calibration",
                params=params,
                headers=self._headers,
            )
        _raise_for_status(resp)
        return resp.json()

    # -------------------------------------------------------------------------
    # V1.7 – Privacy
    # -------------------------------------------------------------------------

    async def redact_commitment(self, commitment_id: str | uuid.UUID) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self._timeout) as http:
            resp = await http.post(
                f"{self._base_url}/commitments/{commitment_id}/redact",
                headers=self._headers,
            )
        _raise_for_status(resp)
        return resp.json()

    # -------------------------------------------------------------------------
    # V1.7 – Refinements
    # -------------------------------------------------------------------------

    async def apply_refinement(
        self,
        commitment_id: str | uuid.UUID,
        changes: list[dict[str, Any]],
        actor: str = "sdk",
    ) -> dict[str, Any]:
        payload = {
            "commitment_id": str(commitment_id),
            "changes": changes,
            "actor": actor,
        }
        async with httpx.AsyncClient(timeout=self._timeout) as http:
            resp = await http.post(
                f"{self._base_url}/commitments/{commitment_id}/refinements",
                json=payload,
                headers=self._headers,
            )
        _raise_for_status(resp)
        return resp.json()

    async def get_refinements(
        self, commitment_id: str | uuid.UUID
    ) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self._timeout) as http:
            resp = await http.get(
                f"{self._base_url}/commitments/{commitment_id}/refinements",
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
