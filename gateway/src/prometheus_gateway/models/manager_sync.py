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
from datetime import datetime, timezone
from typing import Any, Literal
from urllib.parse import urlparse

import httpx
import structlog

from .registry import ModelEntry, ModelRegistry
from .. import db, pricing
from ..admin.nodes_client import fetch_nodes
from ..telemetry import get_logger

logger = get_logger(__name__)

# RM-97: how long to ask a manager to hold a request. Under the manager's own
# 90s ceiling, so the manager is the one that decides when to answer.
_BLOCKING_WAIT_S = 60

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
        # RM-97: the registry fingerprint each node last handed back, so the next
        # request can ask to be held until it changes.
        self._node_index: dict[str, str] = {}
        # Turned off the first time a node answers without the header, which is
        # what a manager predating RM-97 does. Then this degrades to the poll it
        # replaced rather than to a busy loop.
        self._blocking_supported = True
        # True only after a request the manager actually held. Anything else —
        # a failure, a node that does not support blocking, the first cycle
        # before we hold an index — leaves it False, so the loop sleeps.
        self._held_last_request = False
        # RM-98: the last entries each node successfully reported, kept so a
        # node that cannot be asked contributes what it last said instead of
        # nothing.
        self._last_good: dict[str, list[ModelEntry]] = {}
        # RM-99: the same, as the manager reported it, for the disk snapshot.
        self._raw_last_good: dict[str, list[dict[str, Any]]] = {}
        # When the catalog first went stale, so the log can say for how long.
        self._stale_since: float | None = None
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
        if not self._registry._models:
            await self._restore_from_snapshot()
        self._task = asyncio.create_task(self._poll_loop(), name="manager-registry-sync")

    async def _restore_from_snapshot(self) -> None:
        """Start from the last catalog on disk — RM-99.

        Called in exactly one situation: the first sync of this process produced
        nothing, meaning no node could be asked. Never called again. The manager
        is the source of truth, and the moment it answers, its answer replaces
        all of this — including dropping anything it no longer serves, because a
        successful sync rebuilds the catalog from scratch rather than merging.
        """
        try:
            stored = await db.load_catalog_snapshot()
        except Exception as exc:
            logger.warning("manager_sync.snapshot_read_failed", error=str(exc))
            return
        if stored is None:
            logger.info("manager_sync.no_snapshot")
            return

        payload, written_at = stored
        age_s = int((datetime.now(timezone.utc) - written_at).total_seconds())
        restored: dict[str, ModelEntry] = {}
        for node_name, backends in payload.items():
            entries = [self._to_model_entry(node_name, b) for b in backends]
            fresh = [e for e in entries if e is not None]
            self._last_good[node_name] = fresh
            self._raw_last_good[node_name] = backends
            for entry in fresh:
                restored[entry.id] = entry
        self._registry._models = restored

        # Loud on purpose, and with the age: this is the gateway saying it is
        # routing on something nobody has confirmed. Whether an hour-old
        # snapshot is fine and a three-week-old one means "go fix the manager
        # first" is a judgement, and the operator can only make it if the number
        # is in front of them. What the snapshot claims is alive is verified
        # independently within seconds by health probing either way.
        logger.warning(
            "manager_sync.restored_from_snapshot",
            count=len(restored),
            snapshot_age_s=age_s,
            written_at=written_at.isoformat(),
        )

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _poll_loop(self) -> None:
        while True:
            # RM-97: when the manager held the request, it already did the
            # waiting, so going straight round is right — sleeping on top would
            # put back the interval this exists to remove. Every other case
            # sleeps, and the default is to sleep: the first version of this
            # inverted the test and a failed request skipped the sleep, which
            # turned an unreachable manager into a busy loop at 85% of a core,
            # 1455 cycles in ten seconds. An outage is exactly when a gateway
            # must not spin.
            if not self._held_last_request:
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

    async def _fetch_node_backends(
        self, node_name: str, manager_url: str
    ) -> list[dict[str, Any]] | None:
        """GET /v1/backends from one node.

        RM-98: returns **None** when the node could not be asked, and a list —
        possibly empty — when it answered. The two used to be the same `[]`, and
        that is what emptied the whole catalog the moment a manager went down: a
        node that failed was indistinguishable from one that genuinely serves
        nothing. A caller cannot keep the last known good state for a node if it
        cannot tell that the node failed.
        """
        headers = await self._get_auth_headers()
        ctx_trace_id = structlog.contextvars.get_contextvars().get("trace_id")
        if ctx_trace_id and ctx_trace_id != "none":
            headers["X-Trace-ID"] = ctx_trace_id

        # RM-97: hand back the index this node last gave us and let the manager
        # hold the request until it stops matching. A change now lands in about
        # a second instead of waiting out a poll interval, and a quiet registry
        # costs one held request a minute rather than one every interval.
        params: dict[str, Any] = {}
        known_index = self._node_index.get(node_name)
        if known_index:
            params = {"index": known_index, "wait": _BLOCKING_WAIT_S}

        try:
            # The read timeout has to outlast the wait the manager was asked to
            # hold, with room for the round trip. Shorter and we would time out
            # on our own request every quiet minute.
            timeout = httpx.Timeout(10.0, read=_BLOCKING_WAIT_S + 15.0)
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.get(
                    f"{manager_url}/v1/backends", headers=headers, params=params
                )
                resp.raise_for_status()
                data = resp.json()
            new_index = resp.headers.get("X-Registry-Index")
            if new_index:
                self._node_index[node_name] = new_index
            else:
                # A node that predates RM-97 answers immediately and without the
                # header. Stop asking it to block and fall back to sleeping.
                self._blocking_supported = False
            # Held means the manager did the waiting for us, whether it came
            # back on a change or on the wait expiring. Either way the loop has
            # nothing to add by sleeping again.
            self._held_last_request = bool(params) and self._blocking_supported
            backends: list[dict[str, Any]] = data.get("backends", [])
            return backends
        except Exception as exc:
            # Back to sleeping. A manager we cannot reach must be asked slowly.
            self._held_last_request = False
            logger.warning(
                "manager_sync.node_unreachable",
                node=node_name,
                manager_url=manager_url,
                error=str(exc) or repr(exc),
                exc_type=type(exc).__name__,
            )
            return None

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
            # RM-95: which engine serves this instance, so health probing can ask
            # where the engine actually answers. The default is the fallback for
            # a node predating this field, not a guess about this one.
            backend=b.get("backend") or "llama_cpp",
            # RM-51: falls back to this instance's own id for a not-yet-
            # upgraded manager node during a rolling deploy (pre-RM-51
            # /v1/backends responses don't send model_id at all).
            model_id=b.get("model_id") or model_id,
            # RM-70: same rolling-deploy fallback — a node that predates the
            # catalog slug keeps resolving under its model_id.
            model_slug=b.get("model_slug") or b.get("model_id") or model_id,
            label=b.get("label", ""),
        )

    async def _sync(self) -> None:
        # RM-20: pick up any node added/removed via the dashboard before polling.
        await self._refresh_nodes()

        # RM-08 phase 2: poll every configured node concurrently, so one
        # unreachable node does not block refreshing the others.
        results = await asyncio.gather(
            *(self._fetch_node_backends(name, url) for name, url in self._nodes)
        )

        new_models: dict[str, ModelEntry] = {}
        stale_nodes: list[str] = []
        for (node_name, _url), backends in zip(self._nodes, results, strict=True):
            if backends is None:
                # RM-98: fail static. The manager is a control plane — where a
                # human registers and retires models — and inference is the data
                # plane. A control plane being restarted must not stop the data
                # plane, which is what Envoy does with its last known good
                # configuration and what AWS calls static stability. Previously
                # this replaced the catalog with nothing and the gateway served
                # zero models.
                #
                # Safe here for a reason that would not hold elsewhere: these
                # backends are ours and we verify their liveness ourselves every
                # few seconds, so a stale entry pointing at something that died
                # is caught by health probing rather than by this refresh. What
                # stale cannot catch is a model retired on purpose — and the
                # control for that is stopping the backend or revoking the
                # scope, neither of which needs the manager.
                stale_nodes.append(node_name)
                for kept in self._last_good.get(node_name, []):
                    new_models[kept.id] = kept
                continue

            fresh: list[ModelEntry] = []
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
                fresh.append(entry)
            # Only a node that answered updates its last known good. A node that
            # answered with nothing genuinely has nothing, and that is a real
            # update rather than a failure.
            self._last_good[node_name] = fresh
            # The snapshot stores what the manager said, not what we made of it,
            # so a restart rebuilds entries through the same code path as a live
            # sync rather than a second one that can drift from it.
            self._raw_last_good[node_name] = backends

        # Atomically replace in-memory models
        self._registry._models = new_models

        # PRM-120: every catalogued model starts with a price. Seeded here
        # rather than at creation because models are created in the manager and
        # prices live in the gateway's own database — and doing it on each sync
        # makes it self-healing for models catalogued before this existed.
        # Failure is logged, never raised: an unpriced model is a billing gap,
        # but a sync that dies on it is an outage.
        try:
            catalogued = {
                (e.model_id or e.id, e.modality) for e in new_models.values() if e.model_id or e.id
            }
            seeded = await db.seed_default_prices(catalogued)
            if seeded:
                # Apply to the live table too, the same way an admin write
                # does — otherwise the model stays unpriced until a restart,
                # which is the gap this exists to close.
                table = pricing.get_pricing_table()
                for row in await db.list_model_price_configs():
                    if row.model_id in seeded:
                        table.set_price(
                            row.model_id,
                            prompt_price_per_1m=row.prompt_price_per_1m,
                            completion_price_per_1m=row.completion_price_per_1m,
                            image_price=row.image_price,
                        )
                logger.info("manager_sync.seeded_default_prices", models=seeded)
        except Exception as exc:  # noqa: BLE001
            logger.warning("manager_sync.seed_default_prices_failed", error=str(exc))

        # RM-98: serving stale has to be visible. The difference between a
        # system that degrades gracefully and one that is quietly broken is
        # whether anyone can tell — and "it still works" is exactly what stops
        # people from looking.
        if stale_nodes:
            if self._stale_since is None:
                self._stale_since = time.monotonic()
            logger.warning(
                "manager_sync.serving_stale",
                nodes=stale_nodes,
                stale_for_s=int(time.monotonic() - self._stale_since),
                count=len(new_models),
            )
        elif self._stale_since is not None:
            logger.info(
                "manager_sync.stale_cleared",
                was_stale_for_s=int(time.monotonic() - self._stale_since),
            )
            self._stale_since = None

        logger.info(
            "manager_sync.refreshed",
            count=len(new_models),
            nodes=[name for name, _ in self._nodes],
            stale_nodes=stale_nodes or None,
        )

        # RM-99: keep a copy on disk for the one case memory cannot cover — this
        # process restarting while the manager is down. Only nodes that actually
        # answered are written, so an outage never overwrites a good snapshot
        # with the emptiness it caused.
        if len(stale_nodes) < len(self._nodes):
            try:
                await db.save_catalog_snapshot(
                    {node: self._raw_last_good.get(node, []) for node in self._last_good}
                )
            except Exception as exc:
                # A snapshot that cannot be written is worth a line, not a
                # failed sync: the live catalog is already correct in memory.
                logger.warning("manager_sync.snapshot_write_failed", error=str(exc))
