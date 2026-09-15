"""FastAPI application for the Prometheus Manager REST API.

Implements: memory/specs/008-llama-server-manager.md — AC-11, AC-12, AC-13
Implements: memory/specs/018-observability-telemetry.md — AC-3, AC-28 (TraceIDMiddleware)
Implements: memory/specs/020-shared-telemetry-package.md — AC-19 (component="api")
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from prometheus_manager_core.telemetry import (
    TraceIDMiddleware,
    configure_logging,
    configure_tracing,
    instrument_fastapi,
)

from .control import router as control_router
from .discovery import router as discovery_router
from .routes import router

# Configure structlog when the API module is first loaded (idempotent — AC-24)
configure_logging(service="manager-api", component="api")
configure_tracing(service="manager-api")

app = FastAPI(
    title="Prometheus Manager API",
    version="0.1.0",
    docs_url="/docs",
    redoc_url=None,
)

# AC-28 (018): trace_id middleware propagates X-Trace-ID from gateway
app.add_middleware(TraceIDMiddleware, service="manager-api")

app.include_router(router)
# RM-10: register/deregister/start/stop/restart — backend-registry:write
app.include_router(control_router)
# RM-48: search/download/manage models from Hugging Face
app.include_router(discovery_router)

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
