import structlog
from fastapi import Request
from fastapi.responses import JSONResponse

from ..telemetry import get_logger

logger = get_logger(__name__)

_BASE_URL = "https://prometheus.internal/errors"


def auth_error_response(
    request: Request,
    status_code: int,
    error_type: str,
    detail: str,
) -> JSONResponse:
    """Return an RFC 9457 Problem Details response for authentication failures.

    Implements: memory/specs/002-jwt-authentication-middleware.md — AC-2 through AC-10
    Implements: memory/specs/018-observability-telemetry.md — AC-27 (trace_id in error body)
    AC-11: the raw JWT token is never included — only request_id, error_type, and path.
    """
    request_id = getattr(getattr(request, "state", None), "request_id", "unknown")
    title = " ".join(word.capitalize() for word in error_type.split("-"))

    # AC-27: include trace_id for client-side log correlation
    trace_id = getattr(getattr(request, "state", None), "trace_id", None)
    if trace_id is None:
        trace_id = structlog.contextvars.get_contextvars().get("trace_id", "none")

    # AC-11: log only safe fields — never the Authorization header or raw token
    logger.warning(
        "auth.rejected",
        request_id=request_id,
        error_type=error_type,
        path=request.url.path,
        status=status_code,
    )

    return JSONResponse(
        status_code=status_code,
        media_type="application/problem+json",
        content={
            "type": f"{_BASE_URL}/{error_type}",
            "title": title,
            "status": status_code,
            "detail": detail,
            "instance": request.url.path,
            "request_id": request_id,
            "trace_id": trace_id,
        },
    )
