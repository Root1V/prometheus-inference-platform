"""FastAPI application for the Prometheus Manager REST API.

Implements: memory/specs/008-llama-server-manager.md — AC-11, AC-12, AC-13
Implements: memory/specs/018-observability-telemetry.md — AC-3, AC-28 (TraceIDMiddleware)
Implements: memory/specs/020-shared-telemetry-package.md — AC-19 (component="api")
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from prometheus_manager_core.telemetry import (
    TraceIDMiddleware,
    configure_logging,
    configure_logs,
    configure_tracing,
    get_logger,
    instrument_fastapi,
)

from .control import router as control_router
from .discovery import router as discovery_router
from .fleet_routes import router as fleet_router
from .fleet_sweep import FleetSweep
from .heartbeat import NodeHeartbeat
from .routes import router

# Configure structlog when the API module is first loaded (idempotent — AC-24)
configure_logging(service="manager-api", component="api")
configure_tracing(service="manager-api")
# PRM-171: logs too, same gate. Fleet membership changes are administrative
# actions and their lines belong where they can be searched.
configure_logs(service="manager-api")

logger = get_logger(__name__)


@asynccontextmanager
async def _lifespan(application: FastAPI) -> AsyncIterator[None]:
    """The two fleet background tasks — PRM-151's sweep and PRM-152's heartbeat.

    Started here rather than in cli.py because both need the running event loop
    that uvicorn owns.

    **Every node reports itself; only the coordinator sweeps.** The sweep is the
    fallback from before nodes could authenticate (PRM-152) and is now behind
    `[fleet] sweep`, on by default. Turning it off is the end state rather than an
    option: while a probe also stamps `last_seen_at`, "the node is down" and "I
    could not reach it" remain indistinguishable, which is the whole reason the
    heartbeat exists.
    """
    fleet = getattr(application.state, "fleet", None)
    config = getattr(application.state, "config", None)
    fleet_cfg = getattr(config, "fleet", None)

    sweep: FleetSweep | None = None
    if fleet is not None and (fleet_cfg is None or fleet_cfg.sweep):
        sweep = FleetSweep(fleet)
        application.state.fleet_sweep = sweep
        sweep.start()
        logger.info("fleet.sweep_started", interval_s=sweep._interval_s)

    heartbeat: NodeHeartbeat | None = None
    if fleet_cfg is not None:
        # `fleet` is passed only when this node is the coordinator, which stamps
        # its own row in process — no HTTP to itself and no credential to hold.
        candidate = NodeHeartbeat(fleet_cfg, fleet=fleet)
        if absent := candidate.missing():
            logger.warning(
                "fleet.heartbeat_disabled",
                missing=absent,
                detail=(
                    "this node will not report its own liveness, so the coordinator's "
                    "sweep is the only thing keeping it routable. See PRM-152."
                ),
            )
        else:
            heartbeat = candidate
            application.state.fleet_heartbeat = heartbeat
            heartbeat.start()
            logger.info(
                "fleet.heartbeat_started",
                node_id=fleet_cfg.node_id,
                interval_s=fleet_cfg.heartbeat_interval_s,
                local=heartbeat.local,
            )
    try:
        yield
    finally:
        if sweep is not None:
            await sweep.stop()
        if heartbeat is not None:
            await heartbeat.stop()


app = FastAPI(
    title="Prometheus Manager API",
    version="0.1.0",
    docs_url="/docs",
    redoc_url=None,
    lifespan=_lifespan,
)

# AC-28 (018): trace_id middleware propagates X-Trace-ID from gateway
app.add_middleware(TraceIDMiddleware, service="manager-api")

app.include_router(router)
# RM-10: register/deregister/start/stop/restart — backend-registry:write
app.include_router(control_router)
# RM-48: search/download/manage models from Hugging Face
app.include_router(discovery_router)
# PRM-134: the fleet's node list. Mounted on every manager-api, but only the one
# configured as coordinator holds a registry — the rest answer 409 saying so,
# which is more useful than a 404 an operator cannot tell from a typo.
app.include_router(fleet_router)

# Argus A-19: after the routers, so the SERVER span the ASGI instrumentation
# opens can resolve http.route, and outside TraceIDMiddleware so that
# middleware reads the id from it rather than opening a bare second span.
instrument_fastapi(app)


@app.exception_handler(404)
async def not_found_handler(request, exc):  # type: ignore[no-untyped-def]
    return JSONResponse(
        status_code=404,
        content={
            "type": "https://prometheus.local/errors/not-found",
            "title": "Not Found",
            "status": 404,
        },
    )
