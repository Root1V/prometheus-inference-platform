import os
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request, Response

from .auth.middleware import JWTAuthMiddleware
from .config import Settings
from .models.backends import BackendPool
from .models.registry import ModelRegistry
from .rate_limit_middleware import RateLimitMiddleware
from .router import create_router
from .telemetry import (
    TraceIDMiddleware,
    configure_logging,
    configure_tracing,
    get_logger,
    metrics_store,
)

# Logger initialised after configure_logging() is called inside create_app()
logger = get_logger(__name__)


def create_app(
    settings: Settings | None = None,
    registry: ModelRegistry | None = None,
    redis_client: object | None = None,
) -> FastAPI:
    """Application factory — accepts injectable settings and registry for testing."""
    if settings is None:
        settings = Settings()  # type: ignore[call-arg]  # populated from env vars at runtime

    if registry is None:
        if settings.resolved_manager_nodes:
            # Manager is the single source of truth — start with an empty registry.
            # ManagerRegistrySync will populate it on startup via the manager REST API.
            # The static registry.yaml is ignored when MANAGER_URL/MANAGER_NODES is set.
            registry = ModelRegistry.__new__(ModelRegistry)
            registry._models = {}
        else:
            registry = ModelRegistry(settings.model_registry_path)

    # AC-1 (018): configure structured logging once per process
    # See memory/specs/018-observability-telemetry.md
    configure_logging(
        service="gateway",
        log_level=settings.log_level,
        log_file_path=settings.log_file_path,
        log_max_bytes=settings.log_max_bytes,
        log_backup_count=settings.log_backup_count,
    )
    # See memory/specs/022-opentelemetry-sdk-instrumentation.md — G-4, G-7, G-10
    configure_tracing(service="gateway", instrument_httpx=True)
    # Bind a startup trace_id so early logs (deprecation warnings, etc.) are traceable
    import structlog as _sl

    _sl.contextvars.bind_contextvars(trace_id=f"startup-{str(uuid.uuid4())[:8]}")

    # AC-10 (006): warn if deprecated LLAMA_CPP_URL is set in environment
    if os.environ.get("LLAMA_CPP_URL"):
        logger.warning(
            "llama_cpp_url.deprecated",
            detail="LLAMA_CPP_URL is deprecated and will be ignored. "
            "Configure backend_url per-model in runtime/models/registry.yaml instead.",
        )

    # AC-15 (006): shared connection pool — one client per backend URL
    # AC-16, AC-18 (007): pass Redis client so CB state is persisted/read on startup
    _redis_instance: object | None = redis_client

    if _redis_instance is None and settings.effective_rate_limit_redis_url:
        import redis.asyncio as aioredis

        _redis_instance = aioredis.from_url(
            settings.effective_rate_limit_redis_url,
            health_check_interval=15,  # AC-19: transparent reconnection after Redis restart
            socket_keepalive=True,
        )

    pool = BackendPool(
        redis_client=_redis_instance,
        failure_threshold=settings.circuit_breaker_failure_threshold,
        recovery_timeout=settings.circuit_breaker_recovery_timeout,
        success_threshold=settings.circuit_breaker_success_threshold,
        retry_max=settings.backend_retry_max,
        retry_backoff_base_ms=settings.backend_retry_backoff_base_ms,
    )

    @asynccontextmanager
    async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.backend_pool = pool

        # AC-7 (007): inject Redis client into JWKS module for cross-worker cache
        if _redis_instance is not None:
            from .auth.jwks import set_jwks_redis_client

            set_jwks_redis_client(_redis_instance)

        # AC-23 (008): if MANAGER_URL/MANAGER_NODES is set, start background registry sync
        # RM-08 phase 2: resolved_manager_nodes returns one or more (name, url) pairs.
        _manager_sync = None
        if settings.resolved_manager_nodes:
            from .models.manager_sync import ManagerRegistrySync

            _manager_sync = ManagerRegistrySync(
                nodes=settings.resolved_manager_nodes,
                registry=registry,
                poll_interval_s=settings.manager_poll_interval_s,
                manager_client_id=settings.manager_client_id,
                manager_client_secret=settings.manager_client_secret,
                auth_token_url=settings.auth_service_token_url,
                auth_tls_verify=settings.auth_service_tls_verify,
                manager_jwt=settings.manager_jwt,  # deprecated static fallback
            )
            await _manager_sync.start()

        yield

        if _manager_sync is not None:
            await _manager_sync.stop()
        await pool.aclose()

    app = FastAPI(
        title="Prometheus Gateway",
        version="0.2.0",
        lifespan=_lifespan,
        # Disable auto-generated docs endpoints (unauthenticated)
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    # Make pool accessible immediately (before lifespan) for tests that inspect state
    app.state.backend_pool = pool
    # Expose metrics_store on app.state for route access
    app.state.metrics_store = metrics_store

    # Middleware stack — innermost added first (see gateway.instructions.md)
    # Order: [request-id+trace-id] → [auth] → [rate-limit] → router
    # AC-1..AC-13 (007): rate limiting middleware sits after auth so claims are available
    # AC-6, AC-7 (018): trace_id middleware integrated into request_id_middleware below
    app.add_middleware(RateLimitMiddleware, settings=settings, redis_client=_redis_instance)
    app.add_middleware(JWTAuthMiddleware, settings=settings)
    app.add_middleware(TraceIDMiddleware, service="gateway")  # outermost: runs before auth

    @app.middleware("http")
    async def request_id_middleware(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """Inject X-Request-ID on every request. Runs after TraceIDMiddleware (AC-26)."""
        request.state.request_id = str(uuid.uuid4())
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        return response

    @app.get("/health")
    async def health() -> dict[str, str]:
        """Liveness probe — unauthenticated. See memory/specs/001-gateway-core.md AC-4."""
        return {"status": "ok"}

    @app.get("/metrics")
    async def get_metrics(request: Request) -> dict[str, Any]:
        """In-process operational metrics — unauthenticated.

        Implements: memory/specs/018-observability-telemetry.md — AC-19, AC-20, AC-21, AC-22.
        No authentication required (already exempted in JWTAuthMiddleware.EXEMPT_PATHS).
        No per-user data — only aggregate counters and named backend states (AC-21).
        """
        pool = getattr(request.app.state, "backend_pool", None)
        return await metrics_store.snapshot(pool)

    # Implements: memory/specs/001-gateway-core.md — AC-1, AC-2, AC-5, AC-6, AC-7
    # Implements: memory/specs/006-multi-model-gateway.md — AC-1 through AC-15
    app.include_router(create_router(registry, pool))

    # Implements: memory/specs/013-web-chat-ui-proxy.md — AC-1 (disabled by default)
    # Implements: memory/specs/014-login-page-ux-redesign.md — AC-2
    if settings.ui_enabled:
        if not settings.gateway_tls_cert_file:
            # AC-16: warn operators that Secure cookie flag requires HTTPS
            logger.warning(
                "ui.tls_not_configured",
                detail="UI_ENABLED=true but no TLS certificate configured. "
                "The 'Secure' cookie flag is always set — browsers will reject the session "
                "cookie over plain HTTP. Set GATEWAY_TLS_CERT_FILE and GATEWAY_TLS_KEY_FILE.",
            )
        from fastapi.staticfiles import StaticFiles

        from .ui.router import _UI_DIR, create_ui_router

        # Mount static files at /ui/static directly on the app so they are
        # resolved before the APIRouter catch-all (/{model_id}/{path:path}).
        # Cache-Control: no-cache — AC-Q1 in memory/specs/014.
        _static_dir = _UI_DIR / "static"
        if _static_dir.is_dir():
            app.mount(
                "/ui/static",
                StaticFiles(directory=str(_static_dir)),
                name="ui-static",
            )

        app.include_router(create_ui_router(settings, registry), prefix="/ui")
    # When ui_enabled=False, all /ui/* paths are handled by JWTAuthMiddleware's exempt logic
    # and will pass through to FastAPI's 404 handler — AC-1 satisfied.

    return app
