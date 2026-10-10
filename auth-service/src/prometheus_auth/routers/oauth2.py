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
from ..login_throttle import LoginThrottle, client_address
from ..schemas import OAuth2Error, TokenResponse, invalid_scopes
from ..telemetry import get_logger, get_tracer

logger = get_logger(__name__)
_tracer = get_tracer("auth-service.oauth2")

router = APIRouter(tags=["oauth2"])

# PRM-250: a real cost-12 hash of a value no credential equals — the same cost
# `admin.py` stores every password and secret with. Hardcoded rather than
# generated at import, so the dummy path cannot drift from the real one and
# startup does not pay 160ms for it.
_DUMMY_HASH = b"$2b$12$hzURTyfGjqcrWaNq23mtE.4mBOU7BgzQlch10D6tILsrxu/fPO2mC"


def _credential_matches(supplied: str, stored: str | None) -> bool:
    """Compare, paying the bcrypt cost even when there is nothing to compare to.

    Measured against the running deployment before this existed: a wrong
    password for a real account answered in 179ms, and for an address with no
    account in 4.5ms, because the handler returned before bcrypt ran. The
    refusal was worded identically either way, so the message gave nothing
    away and the clock gave away everything — every valid operator address was
    discoverable at roughly a hundred guesses a second.

    The dummy comparison has to sit on the path that actually runs. The
    published advisories for this bug class describe a dummy branch that the
    caller's own early return skipped, leaving the hole open.
    """
    if stored is None:
        bcrypt.checkpw(supplied.encode(), _DUMMY_HASH)
        return False
    return bcrypt.checkpw(supplied.encode(), stored.encode())


def _throttled(
    request: Request, grant_type: str, client_id: str, username: str
) -> list[tuple[LoginThrottle, str]]:
    """The (throttle, key) pairs this attempt spends from.

    Two of them, because the two attacks look different: one account with many
    guesses, and many accounts with one guess each.
    """
    identity = client_id if grant_type == "client_credentials" else username
    address = client_address(request, request.app.state.trusted_proxies)
    return [
        (request.app.state.login_throttle, f"id:{identity.lower()}"),
        (request.app.state.login_throttle_by_address, f"ip:{address}"),
    ]


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

        # PRM-250: refuse before looking at the credential, so a caller out of
        # budget costs this service nothing.
        throttles = _throttled(request, grant_type, client_id, username)
        identity = client_id if grant_type == "client_credentials" else username
        address = client_address(request, request.app.state.trusted_proxies)
        # PRM-251: the caller is told nothing that distinguishes these cases.
        # The operator is told everything — which attempt, from where, and why
        # — because "a run of failures against one address" is a pattern only
        # the logs and traces can show, and refusing quietly is not the same as
        # refusing silently.
        span.set_attribute("auth.identity", identity)
        span.set_attribute("auth.source_address", address)

        waits = [w for t, k in throttles if (w := t.retry_after(k)) is not None]
        if waits:
            span.set_attribute("http.status_code", 429)
            span.set_attribute("auth.failure_reason", "too_many_attempts")
            span.set_status(StatusCode.ERROR, "too_many_attempts")
            logger.warning(
                "oauth2.too_many_attempts",
                grant_type=grant_type,
                identity=identity,
                source_address=address,
                retry_after=max(waits),
            )
            return _too_many_attempts(max(waits))

        def record_failure(reason: str) -> None:
            span.set_attribute("auth.failure_reason", reason)
            for throttle, key in throttles:
                throttle.record_failure(key)

        if grant_type == "client_credentials":
            span.set_attribute("client_id", client_id)
            result = await db.execute(select(Principal).where(Principal.client_id == client_id))
            principal = result.scalar_one_or_none()

            # AC-3: unknown client_id, or a password-only principal, is invalid_client
            if principal is None or principal.auth_method != "oauth2":
                _credential_matches(client_secret, None)
                logger.warning(
                    "oauth2.invalid_client",
                    client_id=client_id,
                    reason="unknown_client_id",
                    source_address=address,
                )
                span.set_attribute("http.status_code", 401)
                span.set_status(StatusCode.ERROR, "invalid_client")
                record_failure("unknown_client_id")
                return _invalid_client()

            # AC-3: verify secret — constant-time bcrypt comparison
            if not _credential_matches(client_secret, principal.client_secret_hash):
                logger.warning(
                    "oauth2.invalid_client",
                    client_id=client_id,
                    reason="bad_secret",
                    source_address=address,
                )
                span.set_attribute("http.status_code", 401)
                span.set_status(StatusCode.ERROR, "invalid_client")
                record_failure("bad_secret")
                return _invalid_client()

        elif grant_type == "password":
            span.set_attribute("username", username)
            result = await db.execute(select(Principal).where(Principal.email == username))
            principal = result.scalar_one_or_none()

            # Unknown email, or an oauth2-only principal, is invalid_client
            if principal is None or principal.auth_method != "password":
                _credential_matches(password, None)
                logger.warning(
                    "oauth2.invalid_client",
                    username=username,
                    reason="unknown_email",
                    source_address=address,
                )
                span.set_attribute("http.status_code", 401)
                span.set_status(StatusCode.ERROR, "invalid_client")
                record_failure("unknown_email")
                return _invalid_client()

            if not _credential_matches(password, principal.password_hash):
                logger.warning(
                    "oauth2.invalid_client",
                    username=username,
                    reason="bad_password",
                    source_address=address,
                )
                span.set_attribute("http.status_code", 401)
                span.set_status(StatusCode.ERROR, "invalid_client")
                record_failure("bad_password")
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

        # A sign-in that lands clears the budget: four typos and then the right
        # password has cost the platform nothing worth counting.
        for throttle, key in throttles:
            throttle.clear(key)

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


def _too_many_attempts(retry_after: int) -> JSONResponse:
    """Deliberately says nothing about which account, or whether one exists."""
    return JSONResponse(
        status_code=429,
        headers={"Retry-After": str(retry_after)},
        content=OAuth2Error(
            error="invalid_client",
            error_description=(
                f"Too many failed sign-in attempts. Try again in {retry_after} seconds."
            ),
        ).model_dump(),
    )


def _invalid_client() -> JSONResponse:
    return JSONResponse(
        status_code=401,
        content=OAuth2Error(
            error="invalid_client",
            error_description="Invalid client credentials.",
        ).model_dump(),
    )
