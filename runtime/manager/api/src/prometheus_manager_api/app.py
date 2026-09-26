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
    configure_tracing,
    get_logger,
    instrument_fastapi,
)

from .control import router as control_router
from .discovery import router as discovery_router
from .fleet_routes import router as fleet_router
from .fleet_sweep import FleetSweep
from .routes import router

# Configure structlog when the API module is first loaded (idempotent — AC-24)
configure_logging(service="manager-api", component="api")
configure_tracing(service="manager-api")

logger = get_logger(__name__)


@asynccontextmanager
async def _lifespan(application: FastAPI) -> AsyncIterator[None]:
    """PRM-151: the coordinator sweeps the fleet's liveness while it runs.

    Started here rather than in cli.py because the task needs the running event
    loop that uvicorn owns. Only the coordinator has `app.state.fleet`, so only
    the coordinator sweeps — a plain node starts nothing.
    """
    fleet = getattr(application.state, "fleet", None)
    sweep: FleetSweep | None = None
    if fleet is not None:
        sweep = FleetSweep(fleet)
        application.state.fleet_sweep = sweep
        sweep.start()
        logger.info("fleet.sweep_started", interval_s=sweep._interval_s)
    try:
        yield
    finally:
        if sweep is not None:
            await sweep.stop()


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
