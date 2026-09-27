"""A node reporting that it is up — PRM-152.

Implements: docs/roadmap.md — PRM-152.

PRM-151 built `POST /v1/fleet/nodes/{id}/heartbeat` and no node could call it:
`manager-api` held a JWKS URL for *validating* tokens and nothing to obtain one
with. So liveness stayed a sweep from the coordinator, which cannot tell "the
node is down" from "I could not reach it" — the ambiguity the heartbeat exists to
end. This is the other half.

## One credential per node

Each node has its own OAuth2 client, granted `fleet:heartbeat` and `node:<its
id>`, and the coordinator refuses a report that the token does not name (see
`auth.assert_may_heartbeat`). That is Consul's agent token and Kubernetes'
NodeRestriction: identity per node, authorized to act on itself alone. The
alternative — one secret shared by the fleet — would let any node forge liveness
for any other and would make PRM-157's audit trail unable to say which node acted.

## The coordinator does not call itself

It is a node too and needs `last_seen_at` like the rest, but it owns `fleet.db`,
so it stamps its own row in process. No HTTP to itself, no token, and no
credential issued so a service can talk to itself.

## Why it is told its own id

The coordinator assigns a UUID at registration, so a node cannot derive its own
identity — it is configured with it (`PMGR_FLEET_NODE_ID`). The alternatives were
a second endpoint keyed on node *name*, which would make two ways to name one
node, and discovery by matching its own `manager_url` against the registry, which
makes the URL a second identity key that can disagree with the first.

## A node that cannot report still serves

Missing credentials disable the heartbeat and are logged with the names of what is
absent; they never stop the process. Inference does not depend on this, and PRM-147
was two hours spent on an unset environment variable that produced silence — so the
reason is on the record at startup, once, by name.
"""

from __future__ import annotations

import asyncio
import contextlib
import time

import httpx
from prometheus_manager_core.config import FleetConfig
from prometheus_manager_core.fleet import FleetRegistry
from prometheus_manager_core.telemetry import get_logger

logger = get_logger(__name__)

# Renew this many seconds before expiry — the gateway's manager_sync uses the
# same margin against the same auth-service.
_TOKEN_RENEW_BEFORE_S = 60.0

_REQUEST_TIMEOUT_S = 5.0


class NodeHeartbeat:
    """Reports this node's liveness on an interval, by HTTP or in process."""

    def __init__(self, cfg: FleetConfig, fleet: FleetRegistry | None = None) -> None:
        self._cfg = cfg
        # Present only on the coordinator, which stamps its own row directly.
        self._fleet = fleet
        self._task: asyncio.Task[None] | None = None
        self._token: str | None = None
        self._token_expires_at = 0.0

    @property
    def local(self) -> bool:
        return self._fleet is not None

    def missing(self) -> list[str]:
        """What this node needs to report in and does not have, by name.

        Empty means it can. The coordinator needs only its own id, because it
        writes to `fleet.db` rather than authenticating to itself.
        """
        absent: list[str] = []
        if not self._cfg.node_id:
            absent.append("PMGR_FLEET_NODE_ID")
        if self.local:
            return absent
        if not self._cfg.client_id:
            absent.append("PMGR_FLEET_CLIENT_ID")
        if not self._cfg.client_secret:
            absent.append("PMGR_FLEET_CLIENT_SECRET")
        if not self._cfg.coordinator_url:
            absent.append("[fleet] coordinator_url")
        if not self._cfg.auth_token_url:
            absent.append("[fleet] auth_token_url")
        return absent

    # ── Token ────────────────────────────────────────────────────────────────

    async def _headers(self) -> dict[str, str]:
        if self._token is None or time.time() >= self._token_expires_at - _TOKEN_RENEW_BEFORE_S:
            async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT_S) as client:
                resp = await client.post(
                    self._cfg.auth_token_url,
                    data={
                        "grant_type": "client_credentials",
                        "client_id": self._cfg.client_id,
                        "client_secret": self._cfg.client_secret,
                        # Exactly what a heartbeat needs and nothing else: this
                        # node's own grant. Asking for more than the job requires
                        # is how a credential grows into one nobody can retire.
                        "scope": f"fleet:heartbeat node:{self._cfg.node_id}",
                    },
                )
                resp.raise_for_status()
                body = resp.json()
            self._token = body["access_token"]
            self._token_expires_at = time.time() + int(body.get("expires_in", 300))
            logger.info("fleet.heartbeat_token_renewed", node_id=self._cfg.node_id)
        return {"Authorization": f"Bearer {self._token}"}

    # ── One report ───────────────────────────────────────────────────────────

    async def beat_once(self) -> bool:
        """Report once. True when the sighting was recorded.

        Never raises: a node that cannot report is still serving, and a failed
        report is what the TTL is for.
        """
        node_id = self._cfg.node_id
        if self._fleet is not None:
            # bool() because manager-core is a separate project to mypy here, so
            # its return type arrives as Any.
            return bool(self._fleet.mark_seen(node_id))
        try:
            headers = await self._headers()
            async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT_S) as client:
                resp = await client.post(
                    f"{self._cfg.coordinator_url.rstrip('/')}/v1/fleet/nodes/{node_id}/heartbeat",
                    headers=headers,
                )
        except Exception as exc:
            logger.warning("fleet.heartbeat_failed", node_id=node_id, error=str(exc))
            return False

        if resp.status_code == 200:
            return True
        if resp.status_code == 401:
            # The token was rejected. Drop it so the next beat mints a fresh one
            # rather than retrying a credential the coordinator already refused.
            self._token = None
        logger.warning(
            "fleet.heartbeat_refused",
            node_id=node_id,
            status=resp.status_code,
            detail={
                404: (
                    "this fleet does not hold a node with this id — register the node "
                    "with the coordinator, or correct PMGR_FLEET_NODE_ID"
                ),
                403: (
                    "this node's credential does not name this node — it needs the "
                    f"grant 'node:{node_id}' alongside 'fleet:heartbeat'"
                ),
                409: "the configured coordinator_url points at a node that is not the coordinator",
            }.get(resp.status_code, resp.text[:200]),
        )
        return False

    # ── Lifecycle ────────────────────────────────────────────────────────────

    async def _loop(self) -> None:
        while True:
            await self.beat_once()
            await asyncio.sleep(self._cfg.heartbeat_interval_s)

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
