# See memory/specs/005-auth-service.md — JWKS + health endpoints
# Implements: AC-17, AC-21
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter(tags=["well-known"])


@router.get("/.well-known/jwks.json")
async def jwks(request: Request) -> JSONResponse:
    """Return the RS256 public key in JWK Set format.

    Implements: memory/specs/005-auth-service.md — AC-17
    Unauthenticated — per RFC 7517.
    """
    return JSONResponse(content=request.app.state.jwks_document)


@router.get("/health")
async def health() -> dict[str, Any]:
    """Liveness probe. Implements: memory/specs/005-auth-service.md — AC-21."""
    return {"status": "ok"}
