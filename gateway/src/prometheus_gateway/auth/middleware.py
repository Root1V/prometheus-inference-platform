import re
from datetime import datetime, timezone
from typing import Any

from fastapi import Request
from jose import JWTError, jwt
from jose.exceptions import ExpiredSignatureError
from starlette.types import ASGIApp, Receive, Scope, Send

from .claims import Claims
from .errors import auth_error_response
from .jwks import fetch_jwks_keys, invalidate_jwks_cache
from ..config import Settings
from ..telemetry import get_logger, metrics_store

logger = get_logger(__name__)

# Implements: memory/specs/002-jwt-authentication-middleware.md — AC-7
EXEMPT_PATHS: frozenset[str] = frozenset({"/health", "/metrics", "/v1/models"})
# Implements: memory/specs/013-web-chat-ui-proxy.md — all /ui/* paths use cookie auth, not Bearer
_EXEMPT_PREFIXES: tuple[str, ...] = ("/ui",)

_BEARER_RE = re.compile(r"^Bearer\s+(\S+)$", re.IGNORECASE)
_ALLOWED_ALGORITHMS = ["RS256"]


class _InvalidTokenError(Exception):
    pass


class _TokenExpiredError(Exception):
    pass


class _TokenRevokedError(Exception):
    pass


class JWTAuthMiddleware:
    """Pure ASGI JWT authentication middleware — SSE-safe (does not buffer responses).

    Implements: memory/specs/002-jwt-authentication-middleware.md — AC-1 through AC-12
    See: .github/instructions/auth.instructions.md
    """

    def __init__(
        self,
        app: ASGIApp,
        settings: Settings,
        redis_client: Any = None,  # injectable for testing; created lazily otherwise
    ) -> None:
        self.app = app
        self.settings = settings
        self._static_public_key: str | None = None

        # [MEDIUM fix] Eager Redis init — eliminates race condition from lazy creation.
        # If a redis_client is injected (tests), use it directly.
        if redis_client is not None:
            self._redis: Any = redis_client
        elif settings.jwt_revocation_redis_url:
            import redis.asyncio as aioredis

            self._redis = aioredis.from_url(settings.jwt_revocation_redis_url)
        else:
            self._redis = None

        if settings.jwt_public_key_file:
            with open(settings.jwt_public_key_file) as fh:
                self._static_public_key = fh.read().strip()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # Ensure request.state is available for downstream middleware
        if "state" not in scope:
            scope["state"] = {}

        request = Request(scope)

        # AC-7: exempt paths pass through without auth
        if request.url.path in EXEMPT_PATHS or request.url.path.startswith(_EXEMPT_PREFIXES):
            await self.app(scope, receive, send)
            return

        # AC-10: reject tokens passed as query parameters
        if "token" in request.query_params:
            response = auth_error_response(
                request,
                401,
                "missing-credentials",
                "Token must be provided via the Authorization header, not a query parameter.",
            )
            await response(scope, receive, send)
            return

        # AC-2: require a well-formed Authorization: Bearer <token> header
        auth_header = request.headers.get("Authorization", "")
        match = _BEARER_RE.match(auth_header)
        if not match:
            response = auth_error_response(
                request,
                401,
                "missing-credentials",
                "Missing or malformed Authorization header. "
                "Expected format: Authorization: Bearer <token>",
            )
            await response(scope, receive, send)
            return

        raw_token = match.group(1)
        # AC-11: raw_token is NEVER passed to logger — only validated claims are logged

        # AC-9 (022): auth.validate INTERNAL span; child of current HTTP span from
        # TraceIDMiddleware. Captures validation outcome without exposing token value.
        from prometheus_telemetry import get_tracer
        from opentelemetry.trace import SpanKind, StatusCode

        _auth_tracer = get_tracer("gateway.auth")
        with _auth_tracer.start_as_current_span(
            "auth.validate", kind=SpanKind.INTERNAL
        ) as auth_span:
            try:
                claims = await self._validate_token(raw_token)
            except _TokenExpiredError as exc:
                await metrics_store.inc_jwt_failed()
                auth_span.set_attribute("validation.result", "fail")
                auth_span.set_status(StatusCode.ERROR, "token-expired")
                response = auth_error_response(request, 401, "token-expired", str(exc))
                await response(scope, receive, send)
                return
            except _TokenRevokedError as exc:
                await metrics_store.inc_jwt_failed()
                auth_span.set_attribute("validation.result", "fail")
                auth_span.set_status(StatusCode.ERROR, "token-revoked")
                response = auth_error_response(request, 401, "token-revoked", str(exc))
                await response(scope, receive, send)
                return
            except _InvalidTokenError as exc:
                await metrics_store.inc_jwt_failed()
                auth_span.set_attribute("validation.result", "fail")
                auth_span.set_status(StatusCode.ERROR, "invalid-token")
                response = auth_error_response(request, 401, "invalid-token", str(exc))
                await response(scope, receive, send)
                return

            auth_span.set_attribute("jwt.issuer", claims.issuer)
            auth_span.set_attribute("jwt.subject", claims.user_id)
            auth_span.set_attribute("validation.result", "ok")

        # Attach verified claims to request state for downstream use
        # AC-1: request.state.claims is available after this point
        # Starlette 1.0+: scope["state"] is a plain dict; request.state provides attr access
        scope["state"]["claims"] = claims
        logger.info(
            "auth.ok",
            user_id=claims.user_id,
            client_id=claims.client_id,
        )
        # AC (018): count successful JWT validation
        await metrics_store.inc_jwt_ok()
        await self.app(scope, receive, send)

    async def _validate_token(self, raw_token: str) -> Claims:
        # AC-9: algorithm pinning — inspect header BEFORE attempting decode
        try:
            unverified_header = jwt.get_unverified_header(raw_token)
        except JWTError as exc:
            # [LOW fix] Static message — do not reflect internal JWTError details to caller.
            raise _InvalidTokenError("Token could not be parsed.") from exc

        if unverified_header.get("alg") not in _ALLOWED_ALGORITHMS:
            # [LOW fix] Static message — do not reflect the attacker-supplied algorithm name.
            raise _InvalidTokenError("Token uses an unsupported signing algorithm.")

        public_keys = await self._resolve_public_keys()
        payload = await self._decode_with_keys(raw_token, public_keys)

        if payload is None:
            # AC-12: on failure with JWKS, invalidate cache and retry once.
            # [HIGH fix] invalidate_jwks_cache() is rate-limited — returns False if
            # a forced refresh occurred within the last 60 s, preventing DoS amplification.
            if self.settings.jwt_jwks_url and invalidate_jwks_cache():
                public_keys = await self._resolve_public_keys()
                payload = await self._decode_with_keys(raw_token, public_keys)

            if payload is None:
                raise _InvalidTokenError("Token signature validation failed.")

        # AC-8: revocation check via Redis — jti-level revocation (spec 002)
        jti = payload.get("jti")
        if jti:
            await self._check_revocation(jti)

        # spec 005 — AC-16: client-level revocation (auth-service writes this on DELETE /admin/clients/{id})
        sub = payload.get("sub", "")
        if sub:
            await self._check_client_revocation(sub)

        # [MEDIUM fix] Validate sub is a non-empty string.
        # An empty sub would collapse all such tokens to the same identity downstream.
        sub = payload.get("sub", "")
        if not sub:
            raise _InvalidTokenError("Token missing or empty 'sub' claim.")

        # [MEDIUM fix] Guard against missing iat (optional per RFC 7519) to avoid KeyError → 500.
        iat_raw = payload.get("iat")
        issued_at = (
            datetime.fromtimestamp(iat_raw, tz=timezone.utc)
            if iat_raw is not None
            else datetime.now(tz=timezone.utc)
        )

        return Claims(
            user_id=sub,
            client_id=payload.get("azp") or sub,  # fall back to sub when azp absent
            scope=payload.get("scope", ""),
            expires_at=datetime.fromtimestamp(payload["exp"], tz=timezone.utc),
            issued_at=issued_at,
            issuer=payload["iss"],
            jti=jti,
        )

    async def _decode_with_keys(
        self, raw_token: str, public_keys: list[Any]
    ) -> dict[str, Any] | None:
        """Try each key in turn; return the decoded payload or None on failure."""
        for key in public_keys:
            try:
                return jwt.decode(
                    raw_token,
                    key,
                    algorithms=_ALLOWED_ALGORITHMS,
                    audience=self.settings.jwt_audience,
                    issuer=self.settings.jwt_issuer,
                    options={"leeway": self.settings.jwt_clock_skew_seconds},
                )
            except ExpiredSignatureError as exc:
                # Propagate immediately — other keys won't fix an expired token
                raise _TokenExpiredError("Token has expired.") from exc
            except JWTError:
                continue
        return None

    async def _resolve_public_keys(self) -> list[Any]:
        if self._static_public_key:
            return [self._static_public_key]
        # JWKS path — fetch (possibly cached) keys
        return await fetch_jwks_keys(
            self.settings.jwt_jwks_url,  # type: ignore[arg-type]
            tls_verify=self.settings.auth_service_tls_verify,
        )

    async def _check_revocation(self, jti: str) -> None:
        """Check Redis blocklist for a revoked token JTI.

        Implements: memory/specs/002-jwt-authentication-middleware.md — AC-8
        Fail-closed behaviour is controlled by settings.jwt_revocation_strict.
        """
        if self._redis is None:
            return  # Revocation disabled when no Redis is configured

        try:
            value = await self._redis.get(f"prometheus:revoked:{jti}")
            if value is not None:
                raise _TokenRevokedError("Token has been revoked.")
        except _TokenRevokedError:
            raise
        except Exception as exc:
            logger.error("revocation.redis_error", extra={"error": str(exc)})
            if self.settings.jwt_revocation_strict:
                raise _InvalidTokenError("Unable to verify token revocation status.") from exc
            # fail-open: log warning and allow the request through
            logger.warning("revocation.fail_open", extra={"jti": jti})

    async def _check_client_revocation(self, client_id: str) -> None:
        """Check Redis for a revoked client (all tokens for this client_id are invalid).

        Implements: memory/specs/005-auth-service.md — AC-16
        Written by auth-service on DELETE /admin/clients/{client_id}.
        Key: revoked:client:<client_id>  TTL = client's token_ttl_seconds
        """
        if self._redis is None:
            return

        try:
            value = await self._redis.get(f"revoked:client:{client_id}")
            if value is not None:
                raise _TokenRevokedError("Client credentials have been revoked.")
        except _TokenRevokedError:
            raise
        except Exception as exc:
            logger.error("revocation.client_redis_error", extra={"error": str(exc)})
            if self.settings.jwt_revocation_strict:
                raise _InvalidTokenError("Unable to verify client revocation status.") from exc
            logger.warning("revocation.client_fail_open", extra={"client_id": client_id})
