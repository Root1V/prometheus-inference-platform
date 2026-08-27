"""Manager-backed registry sync.

When the admin dashboard is enabled (RM-20: node topology lives in auth-service's
node registry, not a static env var), periodically polls GET /v1/backends from
each registered node's Prometheus Manager API and refreshes the gateway's
in-memory ModelRegistry with the combined results. The node list itself is
re-fetched at the start of every poll cycle (see `_refresh_nodes`), so adding or
removing a node via the dashboard takes effect within one poll interval — no
gateway restart needed.

Authentication against every node's Manager REST API uses the same OAuth2
client_credentials service account (MANAGER_CLIENT_ID + MANAGER_CLIENT_SECRET
+ AUTH_SERVICE_TOKEN_URL) — every node validates against the same central
auth-service, so one token works for all of them. The token is obtained
automatically on startup and renewed when it is within 60 seconds of expiry.

Implements: memory/specs/008-llama-server-manager.md — AC-23
Implements: memory/specs/018-observability-telemetry.md — AC-28 (X-Trace-ID propagation)
Implements: docs/roadmap.md — RM-08 phase 2 (distributed inference)
Implements: docs/roadmap.md — RM-20 (dynamic node registry)
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
from ..admin.nodes_client import fetch_nodes
from ..telemetry import get_logger

logger = get_logger(__name__)

# Always trusted regardless of configured nodes — loopback and the container
# host aliases used by the existing single-host container deployment.
_BASE_ALLOWED_BACKEND_HOSTS: frozenset[str] = frozenset(
    {"127.0.0.1", "::1", "host.docker.internal", "host.containers.internal"}
)

# Renew the token this many seconds before it expires.
_TOKEN_RENEW_BEFORE_S = 60


class ManagerRegistrySync:
    """Background task that keeps ModelRegistry in sync with one or more Manager APIs.

    Supports two authentication modes (in priority order):
      1. Auto-renew: manager_client_id + manager_client_secret + auth_token_url
         Gateway obtains and renews the token automatically via client_credentials.
      2. Static JWT: manager_jwt (for testing / manual bootstrap only).

    Implements: memory/specs/008-llama-server-manager.md — AC-23
    Implements: docs/roadmap.md — RM-08 phase 2
    """

    def __init__(
        self,
        auth_service_admin_url: str,
        auth_service_admin_api_key: str,
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
        self._auth_service_admin_url = auth_service_admin_url
        self._auth_service_admin_api_key = auth_service_admin_api_key
        # (node_name, manager_url) pairs — refreshed from the node registry at
        # the start of every sync cycle (see `_refresh_nodes`), not fixed here.
        self._nodes: list[tuple[str, str]] = []
        self._allowed_backend_hosts: frozenset[str] = _BASE_ALLOWED_BACKEND_HOSTS
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

    async def _refresh_nodes(self) -> None:
        """Re-fetch the node list from auth-service's registry (RM-20).

        Called at the start of every sync cycle so an admin-added/removed node
        takes effect within one poll interval, without a gateway restart.
        """
        try:
            nodes = await fetch_nodes(
                self._auth_service_admin_url,
                self._auth_service_admin_api_key,
                tls_verify=self._auth_tls_verify,
            )
        except Exception as exc:
            logger.warning(
                "manager_sync.node_registry_unreachable",
                error=str(exc) or repr(exc),
                exc_type=type(exc).__name__,
            )
            return  # keep the previous node list rather than wiping it on a blip

        self._nodes = [(name, url.rstrip("/")) for name, url in nodes]

        # RM-08 phase 2: trust the specific hostnames of registered nodes, in
        # addition to loopback/container aliases — not "any remote host". Each
        # node's own manager-api must set PMGR_PROXY_HOST to this same
        # reachable hostname/IP so its backend_url values match what's trusted
        # here.
        node_hosts: set[str] = set()
        for _, url in self._nodes:
            hostname = urlparse(url).hostname
            if hostname:
                node_hosts.add(hostname)
        self._allowed_backend_hosts = _BASE_ALLOWED_BACKEND_HOSTS | node_hosts

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

    async def _fetch_node_backends(self, node_name: str, manager_url: str) -> list[dict[str, Any]]:
        """GET /v1/backends from one node. Returns [] on failure — one down node
        must not block the others (partial availability, not all-or-nothing)."""
        headers = await self._get_auth_headers()
        ctx_trace_id = structlog.contextvars.get_contextvars().get("trace_id")
        if ctx_trace_id and ctx_trace_id != "none":
            headers["X-Trace-ID"] = ctx_trace_id

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(f"{manager_url}/v1/backends", headers=headers)
                resp.raise_for_status()
                data = resp.json()
            backends: list[dict[str, Any]] = data.get("backends", [])
            return backends
        except Exception as exc:
            logger.warning(
                "manager_sync.node_unreachable",
                node=node_name,
                manager_url=manager_url,
                error=str(exc) or repr(exc),
                exc_type=type(exc).__name__,
            )
            return []

    def _to_model_entry(self, node_name: str, b: dict[str, Any]) -> ModelEntry | None:
        model_id: str = b.get("id", "")
        if not model_id:
            return None

        # The manager already filters by discovery=true, so every entry here
        # is eligible for the gateway.  We preserve all of them but only set
        # backend_url when the process is actually running (state ready/loading)
        # so that the proxy rejects requests to stopped models while the login
        # combobox still shows all discovery-enabled models.
        state = b.get("state", "stopped")
        raw_url: str = b.get("backend_url", "")
        parsed = urlparse(raw_url)

        if state in ("ready", "loading") and parsed.hostname in self._allowed_backend_hosts:
            backend_url: str | None = raw_url
            backend_status: Literal["active", "inactive", "invalid"] = "active"
        elif raw_url and parsed.hostname not in self._allowed_backend_hosts:
            logger.warning(
                "manager_sync.invalid_backend_url", id=model_id, url=raw_url, node=node_name
            )
            backend_url = None
            backend_status = "invalid"
        else:
            backend_url = None
            backend_status = "inactive"

        return ModelEntry(
            id=model_id,
            path=b.get("path", ""),
            context_length=int(b.get("context_length", 4096)),
            family=b.get("family", ""),
            quantization=b.get("quantization", ""),
            backend_url=backend_url,
            backend_status=backend_status,
            discovery=True,  # manager already filtered by discovery=true
            node=node_name,
            modality=b.get("modality", "text"),
        )

    async def _sync(self) -> None:
        # RM-20: pick up any node added/removed via the dashboard before polling.
        await self._refresh_nodes()

        # RM-08 phase 2: poll every configured node concurrently. One
        # unreachable node degrades to "its models disappear" rather than
        # blocking the whole registry refresh.
        results = await asyncio.gather(
            *(self._fetch_node_backends(name, url) for name, url in self._nodes)
        )

        new_models: dict[str, ModelEntry] = {}
        for (node_name, _url), backends in zip(self._nodes, results, strict=True):
            for b in backends:
                entry = self._to_model_entry(node_name, b)
                if entry is None:
                    continue
                existing = new_models.get(entry.id)
                if existing is not None and existing.node != entry.node:
                    # Same model_id served by two different nodes — ambiguous
                    # routing. Keep whichever was seen first and warn loudly
                    # instead of silently picking one (RM-08 phase 2).
                    logger.warning(
                        "manager_sync.model_id_collision",
                        model_id=entry.id,
                        kept_node=existing.node,
                        dropped_node=entry.node,
                    )
                    continue
                new_models[entry.id] = entry

        # Atomically replace in-memory models
        self._registry._models = new_models
        logger.info(
            "manager_sync.refreshed",
            count=len(new_models),
            nodes=[name for name, _ in self._nodes],
        )
