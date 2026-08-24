"""Manager-backed registry sync.

When MANAGER_URL is configured, periodically polls GET /v1/backends from the
Prometheus Manager API and refreshes the gateway's in-memory ModelRegistry.

Authentication against the Manager REST API uses OAuth2 client_credentials
(MANAGER_CLIENT_ID + MANAGER_CLIENT_SECRET + AUTH_SERVICE_TOKEN_URL).
The token is obtained automatically on startup and renewed when it is within
60 seconds of expiry — no manual token management required.

Implements: memory/specs/008-llama-server-manager.md — AC-23
Implements: memory/specs/018-observability-telemetry.md — AC-28 (X-Trace-ID propagation)
"""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any, Literal
from urllib.parse import urlparse

import httpx
import structlog

from .registry import ModelEntry, ModelRegistry
from ..telemetry import get_logger

logger = get_logger(__name__)

_ALLOWED_BACKEND_HOSTS: frozenset[str] = frozenset(
    {"127.0.0.1", "::1", "host.docker.internal", "host.containers.internal"}
)

# Renew the token this many seconds before it expires.
_TOKEN_RENEW_BEFORE_S = 60


class ManagerRegistrySync:
    """Background task that keeps ModelRegistry in sync with the Manager API.

    Supports two authentication modes (in priority order):
      1. Auto-renew: manager_client_id + manager_client_secret + auth_token_url
         Gateway obtains and renews the token automatically via client_credentials.
      2. Static JWT: manager_jwt (for testing / manual bootstrap only).

    Implements: memory/specs/008-llama-server-manager.md — AC-23
    """

    def __init__(
        self,
        manager_url: str,
        registry: ModelRegistry,
        *,
        poll_interval_s: int = 30,
        # Preferred: auto-renew via client_credentials
        manager_client_id: str | None = None,
        manager_client_secret: str | None = None,
        auth_token_url: str | None = None,
        auth_tls_verify: bool = True,
        # Fallback: static JWT (deprecated)
        manager_jwt: str | None = None,
    ) -> None:
        self._manager_url = manager_url.rstrip("/")
        self._registry = registry
        self._poll_interval_s = poll_interval_s
        # Auto-renew credentials
        self._client_id = manager_client_id
        self._client_secret = manager_client_secret
        self._auth_token_url = auth_token_url
        self._auth_tls_verify = auth_tls_verify
        # Token cache
        self._access_token: str | None = manager_jwt  # static fallback
        self._token_expires_at: float = 0.0  # epoch seconds; 0 = unknown/expired
        self._task: asyncio.Task[None] | None = None

    # ── Token management ──────────────────────────────────────────────────────

    def _can_auto_renew(self) -> bool:
        return bool(self._client_id and self._client_secret and self._auth_token_url)

    def _token_needs_renewal(self) -> bool:
        if not self._access_token:
            return True
        return time.time() >= self._token_expires_at - _TOKEN_RENEW_BEFORE_S

    async def _renew_token(self) -> None:
        """Fetch a new access token from the auth-service."""
        if not self._can_auto_renew():
            return
        async with httpx.AsyncClient(timeout=10.0, verify=self._auth_tls_verify) as client:
            resp = await client.post(
                self._auth_token_url,  # type: ignore[arg-type]
                data={
                    "grant_type": "client_credentials",
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                    "scope": "backend-registry:read",
                },
            )
            resp.raise_for_status()
            body = resp.json()
        self._access_token = body["access_token"]
        expires_in: int = int(body.get("expires_in", 300))
        self._token_expires_at = time.time() + expires_in
        logger.info("manager_sync.token_renewed", expires_in=expires_in)

    async def _get_auth_headers(self) -> dict[str, str]:
        """Return Authorization header, renewing the token if needed."""
        if self._can_auto_renew() and self._token_needs_renewal():
            await self._renew_token()
        if self._access_token:
            return {"Authorization": f"Bearer {self._access_token}"}
        return {}

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Start background polling task.

        The static registry (loaded from registry.yaml) is cleared immediately so
        stale models never appear in the combobox while the manager is unreachable.
        The poll loop keeps retrying until the manager API becomes available.
        """
        # Clear static bootstrap data — manager is the single source of truth.
        self._registry._models = {}
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(
            service="gateway", trace_id=f"startup-{str(uuid.uuid4())[:8]}"
        )
        try:
            await self._sync()
        except Exception as exc:
            logger.warning(
                "manager_sync.initial_sync_failed",
                error=str(exc) or repr(exc),
                exc_type=type(exc).__name__,
            )
        self._task = asyncio.create_task(self._poll_loop(), name="manager-registry-sync")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _poll_loop(self) -> None:
        while True:
            await asyncio.sleep(self._poll_interval_s)
            # Give each poll cycle its own short trace_id for log correlation
            structlog.contextvars.bind_contextvars(trace_id=f"poll-{str(uuid.uuid4())[:8]}")
            try:
                await self._sync()
            except Exception as exc:
                logger.warning(
                    "manager_sync.poll_error",
                    error=str(exc) or repr(exc),
                    exc_type=type(exc).__name__,
                )

    # ── Registry sync ─────────────────────────────────────────────────────────

    async def _sync(self) -> None:
        headers = await self._get_auth_headers()

        # AC-28 (018): propagate trace_id from current structlog context if available
        ctx_trace_id = structlog.contextvars.get_contextvars().get("trace_id")
        if ctx_trace_id and ctx_trace_id != "none":
            headers["X-Trace-ID"] = ctx_trace_id

        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{self._manager_url}/v1/backends",
                headers=headers,
            )
            resp.raise_for_status()
            data = resp.json()

        backends: list[dict[str, Any]] = data.get("backends", [])
        new_models: dict[str, ModelEntry] = {}

        for b in backends:
            model_id: str = b.get("id", "")
            if not model_id:
                continue

            # The manager already filters by discovery=true, so every entry here
            # is eligible for the gateway.  We preserve all of them but only set
            # backend_url when the process is actually running (state ready/loading)
            # so that the proxy rejects requests to stopped models while the login
            # combobox still shows all discovery-enabled models.
            state = b.get("state", "stopped")
            raw_url: str = b.get("backend_url", "")
            parsed = urlparse(raw_url)

            if state in ("ready", "loading") and parsed.hostname in _ALLOWED_BACKEND_HOSTS:
                backend_url: str | None = raw_url
                backend_status: Literal["active", "inactive", "invalid"] = "active"
            elif raw_url and parsed.hostname not in _ALLOWED_BACKEND_HOSTS:
                logger.warning("manager_sync.invalid_backend_url", id=model_id, url=raw_url)
                backend_url = None
                backend_status = "invalid"
            else:
                backend_url = None
                backend_status = "inactive"

            new_models[model_id] = ModelEntry(
                id=model_id,
                path=b.get("path", ""),
                context_length=int(b.get("context_length", 4096)),
                family=b.get("family", ""),
                quantization=b.get("quantization", ""),
                backend_url=backend_url,
                backend_status=backend_status,
                discovery=True,  # manager already filtered by discovery=true
            )

        # Atomically replace in-memory models
        self._registry._models = new_models
        logger.info("manager_sync.refreshed", count=len(new_models))
