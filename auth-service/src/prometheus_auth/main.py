# See memory/specs/005-auth-service.md — FastAPI application
# Implements: AC-18 (rate limiting), AC-19 (startup validation)
# Implements: memory/specs/018-observability-telemetry.md — AC-2, AC-9
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncGenerator

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from .config import Settings
from .crypto import build_jwks, load_private_key, load_public_key
from .db import create_tables, init_db_engine
from .routers.admin import router as admin_router
from .routers.admin_ui import router as admin_ui_router
from .routers.oauth2 import router as oauth2_router
from .routers.share import router as share_router
from .routers.well_known import router as well_known_router
from prometheus_telemetry import TraceIDMiddleware, configure_logging, configure_tracing, get_logger


def create_app(settings: Settings | None = None) -> FastAPI:
    if settings is None:
        settings = Settings()  # type: ignore[call-arg]

    # AC-2 (018): configure structlog once per process — replaces logging.basicConfig
    configure_logging(
        service="auth-service",
        log_level=settings.log_level,
        log_file_path=settings.log_file_path,
        log_max_bytes=settings.log_max_bytes,
        log_backup_count=settings.log_backup_count,
    )
    # See memory/specs/022-opentelemetry-sdk-instrumentation.md — G-4, G-11 to G-35
    configure_tracing(service="auth-service")
    _logger = get_logger(__name__)

    # AC-18: rate limiter keyed on remote IP
    limiter = Limiter(
        key_func=get_remote_address, default_limits=[f"{settings.auth_rate_limit_rpm}/minute"]
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None, Any]:
        # Initialise DB engine + create tables
        engine = init_db_engine(settings.auth_db_url)
        await create_tables(engine)

        # Load RSA keys — fail-fast if files are missing or unreadable
        private_key = load_private_key(settings.auth_private_key_file)
        public_key = load_public_key(settings.auth_public_key_file)

        # Pre-build JWKS document (static, served from memory)
        jwks_doc = build_jwks(settings.auth_active_kid, public_key)

        # Store on app.state for access in route handlers
        app.state.settings = settings
        app.state.private_key = private_key
        app.state.public_key = public_key
        app.state.jwks_document = jwks_doc
        app.state.limiter = limiter

        # AC-2 (018): structured JSON startup event (replaces logging.basicConfig + basicConfig.info)
        _logger.info("auth_service.started", kid=settings.auth_active_kid)

        yield

        await engine.dispose()

    app = FastAPI(
        title="Prometheus Auth Service",
        description="OAuth2 Authorization Server for the Prometheus AI Gateway",
        version="0.1.0",
        openapi_url="/openapi.json",
        docs_url="/docs",
        redoc_url=None,
        lifespan=lifespan,
    )

    # Rate limiter integration
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)  # type: ignore[arg-type]

    # AC-9 (018): trace_id middleware — outermost so all events carry trace_id
    app.add_middleware(TraceIDMiddleware, service="auth-service")

    # Attach rate limit to the token endpoint specifically (AC-18)
    @app.middleware("http")
    async def _attach_limiter(request: Request, call_next: Any) -> Any:
        request.state.view_rate_limit = None
        return await call_next(request)

    app.include_router(well_known_router)
    app.include_router(oauth2_router)
    app.include_router(admin_router)

    # memory/specs/016-credential-share-link.md — public one-time credential delivery
    app.include_router(share_router)

    # memory/specs/015-auth-service-dashboard.md — admin web UI + static assets
    # Mount static BEFORE the UI router so the catch-all path param doesn't intercept CSS.
    _static_dir = Path(__file__).parent / "static"
    app.mount(
        "/admin/ui/static",
        StaticFiles(directory=str(_static_dir)),
        name="admin-static",
    )
    app.include_router(admin_ui_router)

    return app
