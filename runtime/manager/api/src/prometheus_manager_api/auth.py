"""JWT authentication for the Manager REST API.

Validates RS256 JWTs issued by the Auth Service and enforces
the `backend-registry:read` / `backend-registry:write` scopes.

Implements: memory/specs/008-llama-server-manager.md — AC-12, AC-13
Implements: memory/roadmap.md — RM-10 (backend-registry:write)
"""

from __future__ import annotations

from typing import Annotated, Any

import httpx
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from prometheus_manager_core.telemetry import get_logger

logger = get_logger(__name__)

_bearer = HTTPBearer(auto_error=False)

REQUIRED_SCOPE = "backend-registry:read"
REQUIRED_SCOPE_WRITE = "backend-registry:write"


class JwtAuthError(HTTPException):
    pass


async def _get_jwks(jwks_url: str, tls_verify: bool = True) -> dict[str, Any]:
    """Fetch JWKS from the Auth Service (cached in app state)."""
    async with httpx.AsyncClient(timeout=5.0, verify=tls_verify) as client:
        resp = await client.get(jwks_url)
        resp.raise_for_status()
        result: dict[str, Any] = resp.json()
        return result


async def _validate_token(
    request: Request, credentials: HTTPAuthorizationCredentials | None
) -> dict[str, Any]:
    """Validate the bearer JWT and return its claims — no scope check.

    Implements: memory/specs/008-llama-server-manager.md — AC-12 (JWT validation)
    Raises HTTP 401 using RFC 9457 Problem Details on failure.
    """
    if credentials is None:
        raise HTTPException(
            status_code=401,
            detail={
                "type": "https://prometheus.local/errors/unauthorized",
                "title": "Unauthorized",
                "status": 401,
                "detail": "Bearer token required.",
            },
        )

    token = credentials.credentials
    jwks_url: str = request.app.state.jwks_url  # set during startup
    jwks_tls_verify: bool = getattr(request.app.state, "jwks_tls_verify", True)

    try:
        from jose import jwt
        from jose.backends import RSAKey  # noqa: F401

        jwks = await _get_jwks(jwks_url, tls_verify=jwks_tls_verify)
        claims: dict[str, Any] = jwt.decode(
            token,
            jwks,
            algorithms=["RS256"],
            options={"verify_aud": False},
        )
        return claims
    except Exception as exc:
        logger.warning("auth.invalid_token", extra={"error": str(exc)})
        raise HTTPException(
            status_code=401,
            detail={
                "type": "https://prometheus.local/errors/unauthorized",
                "title": "Unauthorized",
                "status": 401,
                "detail": "Invalid or expired token.",
            },
        ) from exc


def _require_scope(claims: dict[str, Any], required: str) -> None:
    scopes: list[str] = []
    scope_claim = claims.get("scope", "")
    if isinstance(scope_claim, str):
        scopes = scope_claim.split()
    elif isinstance(scope_claim, list):
        scopes = scope_claim

    if required not in scopes:
        raise HTTPException(
            status_code=403,
            detail={
                "type": "https://prometheus.local/errors/forbidden",
                "title": "Forbidden",
                "status": 403,
                "detail": f"Scope '{required}' is required.",
            },
        )


async def require_backend_registry_read(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> dict[str, Any]:
    """FastAPI dependency: validate JWT and assert `backend-registry:read` scope.

    Implements: memory/specs/008-llama-server-manager.md — AC-12, AC-13
    Returns the validated JWT claims dict.
    """
    claims = await _validate_token(request, credentials)
    _require_scope(claims, REQUIRED_SCOPE)
    return claims


async def require_backend_registry_write(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> dict[str, Any]:
    """FastAPI dependency: validate JWT and assert `backend-registry:write` scope.

    Implements: memory/roadmap.md — RM-10 (register/unregister/start/stop/restart via REST)
    Returns the validated JWT claims dict.
    """
    claims = await _validate_token(request, credentials)
    _require_scope(claims, REQUIRED_SCOPE_WRITE)
    return claims
