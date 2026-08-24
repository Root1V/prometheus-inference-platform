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
)

from .routes import router

# Configure structlog when the API module is first loaded (idempotent — AC-24)
configure_logging(service="manager", component="api")
configure_tracing(service="manager")

app = FastAPI(
    title="Prometheus Manager API",
    version="0.1.0",
    docs_url="/docs",
    redoc_url=None,
)

# AC-28 (018): trace_id middleware propagates X-Trace-ID from gateway
app.add_middleware(TraceIDMiddleware, service="manager")

app.include_router(router)


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
