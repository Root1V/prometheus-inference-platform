"""JWT authentication for the Manager REST API.

Validates RS256 JWTs issued by the Auth Service and enforces
the `backend-registry:read` scope.

Implements: memory/specs/008-llama-server-manager.md — AC-12, AC-13
"""

from __future__ import annotations

from typing import Annotated

import httpx
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from ..telemetry import get_logger

logger = get_logger(__name__)

_bearer = HTTPBearer(auto_error=False)

REQUIRED_SCOPE = "backend-registry:read"


class JwtAuthError(HTTPException):
    pass


async def _get_jwks(jwks_url: str, tls_verify: bool = True) -> dict:
    """Fetch JWKS from the Auth Service (cached in app state)."""
    async with httpx.AsyncClient(timeout=5.0, verify=tls_verify) as client:
        resp = await client.get(jwks_url)
        resp.raise_for_status()
        return resp.json()


async def require_backend_registry_read(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> dict:
    """FastAPI dependency: validate JWT and assert `backend-registry:read` scope.

    Implements: memory/specs/008-llama-server-manager.md — AC-12 (JWT validation)
                                                      AC-13 (scope enforcement)
    Returns the validated JWT claims dict.
    Raises HTTP 401 or HTTP 403 using RFC 9457 Problem Details on failure.
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
        from jose import jwt  # type: ignore[import-untyped]
        from jose.backends import RSAKey  # noqa: F401

        jwks = await _get_jwks(jwks_url, tls_verify=jwks_tls_verify)
        claims = jwt.decode(
            token,
            jwks,
            algorithms=["RS256"],
            options={"verify_aud": False},
        )
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

    scopes: list[str] = []
    scope_claim = claims.get("scope", "")
    if isinstance(scope_claim, str):
        scopes = scope_claim.split()
    elif isinstance(scope_claim, list):
        scopes = scope_claim

    if REQUIRED_SCOPE not in scopes:
        raise HTTPException(
            status_code=403,
            detail={
                "type": "https://prometheus.local/errors/forbidden",
                "title": "Forbidden",
                "status": 403,
                "detail": f"Scope '{REQUIRED_SCOPE}' is required.",
            },
        )

    return claims
