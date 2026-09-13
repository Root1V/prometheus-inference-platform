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

Liveness means a **success status from an endpoint the engine actually serves**
— RM-95. It used to mean "the process answered anything at all", including a
404, on the grounds that sd.cpp has no `/health`. That was a workaround for a
problem that did not exist: sd-server answers `GET /` with 200 and the body
"Stable Diffusion Server is running". Nobody had looked.

The old rule was wrong in a way worth naming, because it cost us elsewhere on
the same day it was written. A 404 proves only that *something* speaks HTTP on
that port. It cannot tell a healthy backend from a broken one, nor from an
entirely different process that took the port — and that last case is real: a
container from another project bound 0.0.0.0:9000 here and the manager spent
hours parsing its XML error page as a JSON key set. A probe that accepts any
answer is built to miss exactly that.

Every load balancer and orchestrator treats 200-399 as success and everything
else as failure. So do we now.
"""

from __future__ import annotations

import asyncio

import httpx
from opentelemetry.context import _SUPPRESS_INSTRUMENTATION_KEY, attach, detach, set_value

from .telemetry import get_logger

logger = get_logger(__name__)

# RM-95: which path each engine actually serves as a health signal, and whether
# it reports concurrency. sd.cpp has neither `/health` nor `/slots`; it has `/`.
_ENGINE_PROBES: dict[str, tuple[str, str | None]] = {
    "llama_cpp": ("/health", "/slots"),
    "sd_cpp": ("/", None),
}
_DEFAULT_PROBE = _ENGINE_PROBES["llama_cpp"]

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

    def __init__(self, registry: object, interval_s: int = 10, pool: object = None) -> None:
        self._registry = registry
        # RM-72: where measured capacity goes, so routing can weigh a backend's
        # load against what it can actually take. Optional — the monitor is
        # useful on its own, and tests build it without a pool.
        self._pool = pool
        self._interval_s = interval_s
        self._unreachable: set[str] = set()
        # RM-71: concurrent slots each backend reports, for the capacity figure
        # shown beside the configured rate limit. Absent for engines that don't
        # report any — sd.cpp has no /slots at all.
        self._slots: dict[str, int] = {}
        self._task: asyncio.Task[None] | None = None
        self._client: httpx.AsyncClient | None = None

    def unreachable(self, backend_url: str) -> bool:
        return backend_url in self._unreachable

    def capacity(self) -> dict[str, int]:
        """Concurrent slots across reachable backends — RM-71 (decision #8).

        A fact, not an estimate: llama.cpp reports how many requests it can
        genuinely work on at once. Deliberately *not* turned into a suggested
        rate limit — inferring one from model size, quantization and slots is
        guesswork, and a wrong formula throttles or over-admits in silence. The
        operator sets the limit; this just stops them setting it blind.

        `reporting` says how many backends the number actually covers, so an
        engine that reports nothing (sd.cpp) is visible as a gap rather than
        silently counted as zero capacity.
        """
        live = {u: n for u, n in self._slots.items() if u not in self._unreachable}
        return {"slots": sum(live.values()), "reporting": len(live)}

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
        entries = [m for m in list_active() if m.backend_url]
        # RM-95: one engine per URL. Two models served by the same process share
        # a URL and an engine, so the first entry answers for the address.
        engines: dict[str, str] = {}
        for m in entries:
            engines.setdefault(str(m.backend_url), getattr(m, "backend", "") or "llama_cpp")
        urls = sorted(engines)

        # RM-95: a span per probe answers no question anyone asks, and there are
        # a lot of them — six backends on a ten-second interval was 57% of every
        # span this gateway produced, measured by the team receiving them.
        # Suppressed at the source rather than filtered downstream, so nothing
        # is built, serialised or shipped to be discarded at the far end.
        # Whether a backend is up is a metric; `capacity()` and the unreachable
        # set are where that lives.
        token = attach(set_value(_SUPPRESS_INSTRUMENTATION_KEY, True))
        try:
            results = await asyncio.gather(
                *(self._probe(url, engines[url]) for url in urls), return_exceptions=True
            )
        finally:
            detach(token)
        now_unreachable = {url for url, alive in zip(urls, results) if alive is not True}

        recovered = self._unreachable - now_unreachable
        newly_down = now_unreachable - self._unreachable
        for url in sorted(newly_down):
            logger.warning("health_monitor.backend_unreachable", backend_url=url)
        for url in sorted(recovered):
            logger.info("health_monitor.backend_recovered", backend_url=url)
        self._unreachable = now_unreachable

        # RM-72: hand the measured capacity to the pool, keyed by backend id
        # rather than url — that's what routing and the circuit breaker use.
        if self._pool is not None:
            for entry in entries:
                slots = self._slots.get(str(entry.backend_url).rstrip("/"))
                self._pool.set_slot_capacity(entry.id, slots or 0)  # type: ignore[attr-defined]

    async def _probe(self, backend_url: str, engine: str = "llama_cpp") -> bool:
        assert self._client is not None
        base = backend_url.rstrip("/")
        health_path, slots_path = _ENGINE_PROBES.get(engine, _DEFAULT_PROBE)
        try:
            resp = await self._client.get(f"{base}{health_path}")
        except Exception:
            return False
        if not resp.is_success and not resp.is_redirect:
            # 200-399 is success, anything else is failure — the same rule every
            # orchestrator and load balancer applies. A 404 here now means the
            # thing on that port is not the backend we think it is.
            logger.warning(
                "health_monitor.backend_unhealthy",
                backend_url=base,
                engine=engine,
                path=health_path,
                status_code=resp.status_code,
            )
            return False
        if slots_path:
            await self._read_slots(base, slots_path)
        return True

    async def _read_slots(self, base: str, slots_path: str = "/slots") -> None:
        """Record how many concurrent slots this backend has, if it says.

        Failure is not an error: sd.cpp has no /slots route, and a backend that
        doesn't report is simply left out of the capacity figure rather than
        counted as having none.
        """
        try:
            resp = await self._client.get(f"{base}{slots_path}")  # type: ignore[union-attr]
            slots = resp.json()
            if isinstance(slots, list) and slots:
                self._slots[base] = len(slots)
        except Exception:
            self._slots.pop(base, None)
