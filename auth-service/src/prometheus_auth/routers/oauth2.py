# See memory/specs/005-auth-service.md — /oauth2/token endpoint
# Implements: AC-1, AC-3, AC-4, AC-5, AC-6 through AC-9
# Implements: memory/specs/018-observability-telemetry.md — AC-2, AC-11
from typing import Annotated

import bcrypt
from fastapi import APIRouter, Depends, Form, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..crypto import issue_token
from ..db import OAuthClient, get_session_factory
from ..schemas import OAuth2Error, TokenResponse, invalid_scopes
from ..telemetry import get_logger, get_tracer

logger = get_logger(__name__)
_tracer = get_tracer("auth-service.oauth2")

router = APIRouter(tags=["oauth2"])


async def _get_db() -> AsyncSession:  # type: ignore[misc]
    async with get_session_factory()() as session:
        yield session


@router.post("/oauth2/token", response_model=TokenResponse)
async def token(
    request: Request,
    grant_type: Annotated[str, Form()],
    client_id: Annotated[str, Form()],
    client_secret: Annotated[str, Form()],
    scope: Annotated[str, Form()] = "",
    db: AsyncSession = Depends(_get_db),
) -> Response:
    """Issue an RS256 JWT via OAuth2 Client Credentials grant.

    Implements: memory/specs/005-auth-service.md — AC-1, AC-3, AC-4, AC-5
    Implements: memory/specs/022-opentelemetry-sdk-instrumentation.md — G-11, AC-15
    """
    from opentelemetry.trace import SpanKind, StatusCode

    with _tracer.start_as_current_span("token.issuance", kind=SpanKind.INTERNAL) as span:
        span.set_attribute("grant_type", grant_type)
        span.set_attribute("client_id", client_id)
        span.set_attribute("scope", scope or "")

        # Validate grant type
        if grant_type != "client_credentials":
            span.set_attribute("http.status_code", 400)
            return JSONResponse(
                status_code=400,
                content=OAuth2Error(
                    error="unsupported_grant_type",
                    error_description="Only client_credentials grant is supported.",
                ).model_dump(),
            )

        # Fetch client — AC-3: unknown client_id is treated as invalid_client
        result = await db.execute(select(OAuthClient).where(OAuthClient.client_id == client_id))
        client = result.scalar_one_or_none()

        if client is None:
            logger.warning("oauth2.invalid_client", client_id=client_id, reason="unknown_client_id")
            span.set_attribute("http.status_code", 401)
            span.set_status(StatusCode.ERROR, "invalid_client")
            return _invalid_client()

        # AC-5: deactivated clients cannot obtain tokens
        if not client.is_active:
            logger.warning(
                "oauth2.unauthorized_client", client_id=client_id, reason="client_deactivated"
            )
            span.set_attribute("http.status_code", 401)
            span.set_status(StatusCode.ERROR, "unauthorized_client")
            return JSONResponse(
                status_code=401,
                content=OAuth2Error(
                    error="unauthorized_client",
                    error_description="This client has been deactivated.",
                ).model_dump(),
            )

        # AC-3: verify secret — constant-time bcrypt comparison
        if not bcrypt.checkpw(client_secret.encode(), client.client_secret_hash.encode()):
            logger.warning("oauth2.invalid_client", client_id=client_id, reason="bad_secret")
            span.set_attribute("http.status_code", 401)
            span.set_status(StatusCode.ERROR, "invalid_client")
            return _invalid_client()

        # AC-4: validate requested scopes against allowed scopes
        requested = set(scope.split()) if scope else set(client.scopes)
        allowed = set(client.scopes)
        invalid = invalid_scopes(requested)
        if invalid:
            span.set_attribute("http.status_code", 400)
            return JSONResponse(
                status_code=400,
                content=OAuth2Error(
                    error="invalid_scope",
                    error_description=f"Unknown scope(s): {', '.join(sorted(invalid))}",
                ).model_dump(),
            )
        not_allowed = requested - allowed
        if not_allowed:
            span.set_attribute("http.status_code", 400)
            return JSONResponse(
                status_code=400,
                content=OAuth2Error(
                    error="invalid_scope",
                    error_description=f"Scope(s) not permitted for this client: {', '.join(sorted(not_allowed))}",
                ).model_dump(),
            )

        # Effective scope: intersection of requested and allowed
        effective_scope = (
            " ".join(sorted(requested & allowed)) if requested else " ".join(sorted(allowed))
        )

        settings = request.app.state.settings
        private_key = request.app.state.private_key

        token_str, expires_in = issue_token(
            private_key=private_key,
            kid=settings.auth_active_kid,
            issuer=settings.auth_jwt_issuer,
            audience=settings.auth_jwt_audience,
            client_id=client.client_id,
            client_name=client.client_name,
            role=client.role.value if hasattr(client.role, "value") else client.role,
            scope=effective_scope,
            ttl_seconds=client.token_ttl_seconds,
        )

        # AC-11 (018): auth.token_issued — no JWT payload, no private key, no client_secret
        logger.info(
            "auth.token_issued",
            client_id=client_id,
            role=client.role,
            scope=effective_scope,
        )

        span.set_attribute("scope", effective_scope)
        span.set_attribute("http.status_code", 200)
        return JSONResponse(
            content=TokenResponse(
                access_token=token_str,
                token_type="bearer",
                expires_in=expires_in,
                scope=effective_scope,
            ).model_dump()
        )


def _invalid_client() -> JSONResponse:
    return JSONResponse(
        status_code=401,
        content=OAuth2Error(
            error="invalid_client",
            error_description="Invalid client credentials.",
        ).model_dump(),
    )
