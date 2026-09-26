"""Keep the fleet's liveness fresh — PRM-151.

Implements: docs/roadmap.md — PRM-151.

Before this, a node's liveness was only re-examined when somebody pressed Check
in the dashboard. So the fleet's picture of itself was as old as the last time a
human looked, and `is_active` was a flag rather than an observation.

This sweeps every node on an interval and stamps `last_seen_at` on the ones that
answer. Together with the TTL in `fleet.Node.is_active` that makes liveness a
fact with an age: a node not seen for longer than the TTL is not routable, and
the timestamp says *when* it was last up — which a boolean never could.

**It is the fallback, not the design.** The direction this is meant to end up in
is the node renewing its own lease via `POST /v1/fleet/nodes/{id}/heartbeat`,
which is what Kubernetes, Nomad and Consul do and which removes the remaining
ambiguity: a probe that fails cannot distinguish "the node is down" from "I could
not reach it", while a heartbeat that never arrives is unambiguous. The sweep
exists because `manager-api` has no client credentials today, so a node cannot
authenticate to the coordinator — an operational decision, not a code one. Both
paths write through the same `mark_seen`, so the day nodes report in, this can be
turned off without anything else changing.

Neither path may touch `enabled`: whatever observes a node must never overturn
what an operator decided about it.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

import httpx
from prometheus_manager_core.fleet import FleetRegistry
from prometheus_manager_core.telemetry import get_logger

logger = get_logger(__name__)

# A small fraction of the TTL (60s), so a node has to miss several sweeps before
# it drops out — Kubernetes renews its node lease every 10s against a 40s
# duration for the same reason.
DEFAULT_SWEEP_INTERVAL_S = 15.0

_PROBE_TIMEOUT_S = 3.0


async def _probe(client: httpx.AsyncClient, manager_url: str) -> bool:
    try:
        resp = await client.get(f"{manager_url.rstrip('/')}/health")
        return resp.status_code == 200
    except Exception:
        return False


class FleetSweep:
    """Probes every registered node on an interval and stamps the ones that answer."""

    def __init__(self, fleet: FleetRegistry, interval_s: float = DEFAULT_SWEEP_INTERVAL_S) -> None:
        self._fleet = fleet
        self._interval_s = interval_s
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def sweep_once(self) -> dict[str, Any]:
        """One pass. Returns a small summary, which is what the tests assert on."""
        nodes = self._fleet.list()
        seen: list[str] = []
        missed: list[str] = []
        async with httpx.AsyncClient(timeout=_PROBE_TIMEOUT_S) as client:
            results = await asyncio.gather(*(_probe(client, node.manager_url) for node in nodes))
        for node, alive in zip(nodes, results, strict=True):
            if alive:
                self._fleet.mark_seen(node.id)
                seen.append(node.name)
            else:
                # Nothing is written. A node that did not answer keeps its old
                # `last_seen_at`, and the TTL decides — so a single missed sweep
                # is not an outage, and the row still says when it was last up
                # rather than only that it is not up now.
                missed.append(node.name)
        if missed:
            logger.warning("fleet.sweep_missed", seen=seen, missed=missed)
        else:
            logger.debug("fleet.sweep", seen=seen)
        return {"seen": seen, "missed": missed}

    async def _loop(self) -> None:
        while True:
            try:
                await self.sweep_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — a sweep that dies stops liveness
                logger.warning("fleet.sweep_failed", error=str(exc))
            await asyncio.sleep(self._interval_s)
