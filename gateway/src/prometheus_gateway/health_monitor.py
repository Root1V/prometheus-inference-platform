"""Active backend liveness probing.

Implements: docs/roadmap.md — RM-69 (finding C)

Two existing mechanisms already notice a dead backend, and both are too slow
to stop a client paying for the discovery:

* the **circuit breaker** only learns anything from real traffic — the first
  request to a dead replica is always the one that fails;
* the **manager registry poll** runs every 30s, so an instance that dies
  between polls keeps being offered as a routable member.

This closes the gap by asking each active backend, on a short interval,
whether it is still there.

Liveness here means *the process answered*, not *the answer was 200*: sd.cpp
serves image generation but has no `/health` route and replies 404 (verified
against a running sd-server), so treating a non-200 as dead would take image
generation down entirely. Only a connection error or a timeout counts.
"""

from __future__ import annotations

import asyncio

import httpx

from .telemetry import get_logger

logger = get_logger(__name__)

# Short enough that a hung backend is noticed quickly, long enough that a
# backend busy generating tokens still answers. Probes hit `/health`, which
# every llama.cpp-family server answers without touching the model.
_PROBE_TIMEOUT_S = 2.0


class BackendHealthMonitor:
    """Tracks which backend URLs are currently unreachable.

    Deliberately conservative: a backend is only reported unreachable after a
    probe fails to get *any* answer, and `unreachable()` defaults to False for
    anything never probed, so a backend registered between probe cycles is
    routable immediately rather than being treated as dead.
    """

    def __init__(self, registry: object, interval_s: int = 10) -> None:
        self._registry = registry
        self._interval_s = interval_s
        self._unreachable: set[str] = set()
        self._task: asyncio.Task[None] | None = None
        self._client: httpx.AsyncClient | None = None

    def unreachable(self, backend_url: str) -> bool:
        return backend_url in self._unreachable

    async def start(self) -> None:
        if self._interval_s <= 0:
            logger.info("health_monitor.disabled")
            return
        self._client = httpx.AsyncClient(timeout=_PROBE_TIMEOUT_S)
        self._task = asyncio.create_task(self._loop(), name="backend-health-monitor")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _loop(self) -> None:
        while True:
            try:
                await self.probe_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # never let a probe bug kill the loop
                logger.warning("health_monitor.cycle_error", error=str(exc))
            await asyncio.sleep(self._interval_s)

    async def probe_once(self) -> None:
        """One pass over every active backend. Safe to call directly in tests."""
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=_PROBE_TIMEOUT_S)

        list_active = getattr(self._registry, "list_active_models", None)
        if list_active is None:
            return
        # Sorted list, not the set: gather() results come back positionally, so
        # the sequence probed and the sequence zipped must be the same object.
        urls = sorted({m.backend_url for m in list_active() if m.backend_url})

        results = await asyncio.gather(*(self._probe(url) for url in urls), return_exceptions=True)
        now_unreachable = {url for url, alive in zip(urls, results) if alive is not True}

        recovered = self._unreachable - now_unreachable
        newly_down = now_unreachable - self._unreachable
        for url in sorted(newly_down):
            logger.warning("health_monitor.backend_unreachable", backend_url=url)
        for url in sorted(recovered):
            logger.info("health_monitor.backend_recovered", backend_url=url)
        self._unreachable = now_unreachable

    async def _probe(self, backend_url: str) -> bool:
        assert self._client is not None
        try:
            await self._client.get(f"{backend_url.rstrip('/')}/health")
        except Exception:
            # Any answer at all — 200, 404, even 500 — means the process is
            # alive and accepting connections, which is all this asks.
            return False
        return True
