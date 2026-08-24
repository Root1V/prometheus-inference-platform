"""Manager API client for the admin dashboard — RM-10.

Distinct from models/manager_sync.py's token-fetch logic on purpose: that
module is the working, already-shipped registry-sync poller (RM-08). This
client is for on-demand reads and writes triggered by the admin dashboard —
keeping it separate avoids touching a tested, already-merged module for an
unrelated feature.

Implements: memory/roadmap.md — RM-10 (gateway admin dashboard, phase 1)
"""

from __future__ import annotations

import time
from typing import Any

import httpx

from ..telemetry import get_logger

logger = get_logger(__name__)

_TOKEN_RENEW_BEFORE_S = 60
_REQUEST_TIMEOUT_S = 10.0


class ManagerApiClient:
    """OAuth2 client_credentials token management + HTTP calls to a manager-api node.

    One instance is shared across all configured nodes — the same service
    account token is valid on every node's manager-api (all validate against
    the same central auth-service).
    """

    def __init__(
        self,
        *,
        manager_client_id: str | None,
        manager_client_secret: str | None,
        auth_token_url: str | None,
        auth_tls_verify: bool = True,
    ) -> None:
        self._client_id = manager_client_id
        self._client_secret = manager_client_secret
        self._auth_token_url = auth_token_url
        self._auth_tls_verify = auth_tls_verify
        self._access_token: str | None = None
        self._token_expires_at: float = 0.0

    def _can_authenticate(self) -> bool:
        return bool(self._client_id and self._client_secret and self._auth_token_url)

    def _token_needs_renewal(self) -> bool:
        if not self._access_token:
            return True
        return time.time() >= self._token_expires_at - _TOKEN_RENEW_BEFORE_S

    async def _renew_token(self) -> None:
        if not self._can_authenticate():
            return
        async with httpx.AsyncClient(
            timeout=_REQUEST_TIMEOUT_S, verify=self._auth_tls_verify
        ) as client:
            resp = await client.post(
                self._auth_token_url,  # type: ignore[arg-type]
                data={
                    "grant_type": "client_credentials",
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                    "scope": "backend-registry:read backend-registry:write",
                },
            )
            resp.raise_for_status()
            body = resp.json()
        self._access_token = body["access_token"]
        expires_in = int(body.get("expires_in", 300))
        self._token_expires_at = time.time() + expires_in

    async def _headers(self) -> dict[str, str]:
        if self._token_needs_renewal():
            await self._renew_token()
        if self._access_token:
            return {"Authorization": f"Bearer {self._access_token}"}
        return {}

    async def get(
        self, node_url: str, path: str, params: dict[str, Any] | None = None
    ) -> httpx.Response:
        headers = await self._headers()
        async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT_S) as client:
            return await client.get(f"{node_url.rstrip('/')}{path}", headers=headers, params=params)

    async def post(
        self, node_url: str, path: str, json: dict[str, Any] | None = None
    ) -> httpx.Response:
        headers = await self._headers()
        async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT_S) as client:
            return await client.post(f"{node_url.rstrip('/')}{path}", headers=headers, json=json)

    async def delete(self, node_url: str, path: str) -> httpx.Response:
        headers = await self._headers()
        async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT_S) as client:
            return await client.delete(f"{node_url.rstrip('/')}{path}", headers=headers)
