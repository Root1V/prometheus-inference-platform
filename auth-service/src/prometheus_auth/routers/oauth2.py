# See memory/specs/005-auth-service.md — /oauth2/token endpoint
# Implements: AC-1, AC-3, AC-4, AC-5, AC-6 through AC-9
# Implements: memory/specs/018-observability-telemetry.md — AC-2, AC-11
from typing import Annotated, Any

import bcrypt
from fastapi import APIRouter, Depends, Form, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..crypto import issue_token
from ..db import Principal, get_session_factory
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
    client_id: Annotated[str, Form()] = "",
    client_secret: Annotated[str, Form()] = "",
    username: Annotated[str, Form()] = "",
    password: Annotated[str, Form()] = "",
    scope: Annotated[str, Form()] = "",
    db: AsyncSession = Depends(_get_db),
) -> Response:
    """Issue an RS256 JWT via OAuth2 Client Credentials or Password grant.

    Implements: memory/specs/005-auth-service.md — AC-1, AC-3, AC-4, AC-5
    Implements: memory/specs/022-opentelemetry-sdk-instrumentation.md — G-11, AC-15
    Implements: docs/roadmap.md — RM-11 (password grant for human principals)
    """
    from opentelemetry.trace import SpanKind, StatusCode

    with _tracer.start_as_current_span("token.issuance", kind=SpanKind.INTERNAL) as span:
        span.set_attribute("grant_type", grant_type)
        span.set_attribute("scope", scope or "")

        if grant_type == "client_credentials":
            span.set_attribute("client_id", client_id)
            result = await db.execute(select(Principal).where(Principal.client_id == client_id))
            principal = result.scalar_one_or_none()

            # AC-3: unknown client_id, or a password-only principal, is invalid_client
            if principal is None or principal.auth_method != "oauth2":
                logger.warning(
                    "oauth2.invalid_client", client_id=client_id, reason="unknown_client_id"
                )
                span.set_attribute("http.status_code", 401)
                span.set_status(StatusCode.ERROR, "invalid_client")
                return _invalid_client()

            # AC-3: verify secret — constant-time bcrypt comparison
            secret_hash = principal.client_secret_hash
            if secret_hash is None or not bcrypt.checkpw(
                client_secret.encode(), secret_hash.encode()
            ):
                logger.warning("oauth2.invalid_client", client_id=client_id, reason="bad_secret")
                span.set_attribute("http.status_code", 401)
                span.set_status(StatusCode.ERROR, "invalid_client")
                return _invalid_client()

        elif grant_type == "password":
            span.set_attribute("username", username)
            result = await db.execute(select(Principal).where(Principal.email == username))
            principal = result.scalar_one_or_none()

            # Unknown email, or an oauth2-only principal, is invalid_client
            if principal is None or principal.auth_method != "password":
                logger.warning("oauth2.invalid_client", username=username, reason="unknown_email")
                span.set_attribute("http.status_code", 401)
                span.set_status(StatusCode.ERROR, "invalid_client")
                return _invalid_client()

            password_hash = principal.password_hash
            if password_hash is None or not bcrypt.checkpw(
                password.encode(), password_hash.encode()
            ):
                logger.warning("oauth2.invalid_client", username=username, reason="bad_password")
                span.set_attribute("http.status_code", 401)
                span.set_status(StatusCode.ERROR, "invalid_client")
                return _invalid_client()

        else:
            span.set_attribute("http.status_code", 400)
            return JSONResponse(
                status_code=400,
                content=OAuth2Error(
                    error="unsupported_grant_type",
                    error_description="Only client_credentials and password grants are supported.",
                ).model_dump(),
            )

        # AC-5: deactivated principals cannot obtain tokens
        if not principal.is_active:
            logger.warning(
                "oauth2.unauthorized_client",
                client_id=principal.client_id,
                reason="client_deactivated",
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

        settings = request.app.state.settings
        private_key = request.app.state.private_key
        return _issue_for_principal(principal, scope, settings, private_key, span)


def _issue_for_principal(
    principal: Principal,
    scope: str,
    settings: Any,
    private_key: Any,
    span: Any,
) -> JSONResponse:
    # AC-4: validate requested scopes against allowed scopes
    requested = set(scope.split()) if scope else set(principal.scopes)
    allowed = set(principal.scopes)
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

    token_str, expires_in = issue_token(
        private_key=private_key,
        kid=settings.auth_active_kid,
        issuer=settings.auth_jwt_issuer,
        audience=settings.auth_jwt_audience,
        client_id=principal.client_id,
        client_name=principal.client_name,
        role=principal.role.value if hasattr(principal.role, "value") else principal.role,
        scope=effective_scope,
        ttl_seconds=principal.token_ttl_seconds,
    )

    # AC-11 (018): auth.token_issued — no JWT payload, no private key, no client_secret
    logger.info(
        "auth.token_issued",
        client_id=principal.client_id,
        role=principal.role,
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
