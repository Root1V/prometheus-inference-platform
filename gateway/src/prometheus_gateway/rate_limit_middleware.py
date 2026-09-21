"""Rate Limiting ASGI middleware.

Implements: memory/specs/007-rate-limiting-and-throughput.md — AC-1, AC-2, AC-3, AC-4, AC-5, AC-9, AC-13
Implements: memory/specs/018-observability-telemetry.md — AC-1 (structlog migration)
"""

from __future__ import annotations

import time
from typing import Any

from fastapi import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from .config import Settings
from .rate_limiter import RateLimiter
from .telemetry import get_logger

logger = get_logger(__name__)

_BASE_URL = "https://prometheus.internal/errors"

# Endpoint slug used in rate limit keys when no specific route is matched
_DEFAULT_ENDPOINT = "default"

# Map route path patterns to endpoint slugs for per-endpoint limiting (AC-13)
#
# PRM-129 / E-05: embeddings and rerank used to fall through to "default" and
# therefore shared one budget, while chat had its own. That is not a tuning
# choice anyone made — it is what happens when only one route is on this map.
# The Executive Assistant copilot spends 3 requests per suggestion, 2 of them on
# the shared pair, so nine executives asked for 63/min against a 60 budget and
# missed by 5%. One line each moves them to 32 of 60 in three separate budgets.
_ENDPOINT_SLUG_MAP: dict[str, str] = {
    "/v1/chat/completions": "chat_completions",
    "/v1/embeddings": "embeddings",
    "/v1/rerank": "rerank",
}

# RM-51 follow-up: every /admin/api/* route (dynamic path segments like
# {node}/{model_id}, so a prefix check rather than the exact-match map above)
# shares one "admin" budget, separate from inference traffic — see
# Settings.rate_limit_rpm_admin.
_ADMIN_API_PREFIX = "/admin/api/"

# PRM-136: the pass-through route's path carries the model, so the exact-match
# map above cannot see it — and PRM-129 is the record of what happens when a
# route is not on that map: it silently shares `default` with everything else
# and nobody notices until a pilot misses its capacity by 5%.
_PREDICT_PREFIX = "/v1/models/"
_PREDICT_SUFFIX = "/predict"


def _endpoint_slug(path: str) -> str:
    if path.startswith(_ADMIN_API_PREFIX):
        return "admin"
    if path.startswith(_PREDICT_PREFIX) and path.endswith(_PREDICT_SUFFIX):
        return "predict"
    return _ENDPOINT_SLUG_MAP.get(path, _DEFAULT_ENDPOINT)


def _rl_headers(
    slug: str, rpm_state: Any, tpm_state: Any, tpm_limit: int
) -> list[tuple[bytes, bytes]]:
    """The X-RateLimit-* set, built once — PRM-129 follow-up.

    There were two copies of this list, one for the pass-through path and one
    for the 429, and `X-RateLimit-Scope` went into the first only. Axonium
    found it by exhausting a budget: the header was missing from the one
    response that needs it most, because in a 200 the scope is a convenience
    and in a 429 it decides whether you back off one endpoint or all three.

    Adding the line to the second copy would have fixed this instance and left
    the next header to go the same way. One list.
    """
    reset_ts = str(rpm_state.reset_at).encode()
    # RPM can block before the TPM check runs, so fall back to the configured
    # ceiling rather than reporting nothing.
    tl = str(tpm_state.limit if tpm_state else tpm_limit).encode()
    tr = str(tpm_state.remaining if tpm_state else tpm_limit).encode()
    return [
        (b"x-ratelimit-scope", slug.encode()),
        (b"x-ratelimit-limit-requests", str(rpm_state.limit).encode()),
        (b"x-ratelimit-remaining-requests", str(max(0, rpm_state.remaining)).encode()),
        (b"x-ratelimit-reset-requests", reset_ts),
        (b"x-ratelimit-limit-tokens", tl),
        (b"x-ratelimit-remaining-tokens", tr),
        (b"x-ratelimit-reset-tokens", reset_ts),
    ]


def _rl_problem(
    request: Request,
    status: int,
    error_type: str,
    title: str,
    detail: str,
    retry_after: int | None = None,
    extra: dict[str, Any] | None = None,
) -> JSONResponse:
    """RFC 9457 Problem Details response for rate limit errors."""
    request_id = getattr(getattr(request, "state", None), "request_id", "unknown")
    body: dict[str, Any] = {
        "type": f"{_BASE_URL}/{error_type}",
        "title": title,
        "status": status,
        "detail": detail,
        "instance": str(request.url.path),
        "request_id": request_id,
        # PRM-129 follow-up: this envelope used to omit trace_id, and the guide
        # documented the omission rather than closing it. Axonium cited it as
        # the pattern behind the missing scope header — "the rate-limit
        # middleware writes its own envelope, and what it writes is not what
        # the others write" — so fixing one field and leaving the other would
        # have kept the pattern and lost the point.
        "trace_id": getattr(getattr(request, "state", None), "trace_id", "none"),
    }
    if retry_after is not None:
        body["retry_after"] = retry_after
    if extra:
        body.update(extra)
    headers: dict[str, str] = {}
    if retry_after is not None:
        headers["Retry-After"] = str(retry_after)
    return JSONResponse(
        status_code=status,
        content=body,
        media_type="application/problem+json",
        headers=headers,
    )


class RateLimitMiddleware:
    """Pure ASGI rate limiting middleware.

    Must be placed AFTER JWTAuthMiddleware so that request.state.claims is available.
    Applies sliding-window RPM and TPM limits per client_id and user_id.

    Implements: memory/specs/007-rate-limiting-and-throughput.md — AC-1 through AC-13
    """

    def __init__(
        self,
        app: ASGIApp,
        settings: Settings,
        redis_client: Any = None,
    ) -> None:
        self.app = app
        self.settings = settings

        if redis_client is not None:
            self._redis: Any = redis_client
        elif settings.effective_rate_limit_redis_url:
            import redis.asyncio as aioredis

            self._redis = aioredis.from_url(
                settings.effective_rate_limit_redis_url,
                health_check_interval=15,  # AC-19: transparent reconnection
                socket_keepalive=True,
            )
        else:
            self._redis = None

        if self._redis is not None:
            self._limiter: RateLimiter | None = RateLimiter(self._redis)
        else:
            self._limiter = None

    # Paths that bypass rate limiting (no claims needed either)
    _EXEMPT_PATHS: frozenset[str] = frozenset(
        # PRM-96: /oauth2/token is exempt because the limiter keys on claims and
        # this is the request that produces them. auth-service applies its own
        # limits to token issuance, which is where that belongs.
        {"/health", "/metrics", "/v1/models", "/v1/backends", "/v1/usage", "/oauth2/token"}
    )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope)

        if request.url.path in self._EXEMPT_PATHS:
            await self.app(scope, receive, send)
            return

        # Claims must be present — JWTAuthMiddleware runs before this
        claims = getattr(getattr(request, "state", None), "claims", None)
        if claims is None:
            # No claims → JWT middleware will have already rejected the request;
            # this is a safety guard only (should not be reached in normal flow)
            await self.app(scope, receive, send)
            return

        # Resolve per-endpoint limits (AC-13)
        slug = _endpoint_slug(request.url.path)
        rpm_limit, tpm_limit = self._resolve_limits(slug)

        # ── No Redis ─────────────────────────────────────────────────────────
        if self._limiter is None:
            if self.settings.rate_limit_strict:
                response = _rl_problem(
                    request,
                    503,
                    "rate-limiting-unavailable",
                    "Rate Limiting Unavailable",
                    "Rate limiting is not configured. Contact the platform operator.",
                )
                await response(scope, receive, send)
                return
            # fail-open (AC-4b)
            logger.warning("rate_limit.redis_not_configured_fail_open")
            await self.app(scope, receive, send)
            return

        # ── Redis check ───────────────────────────────────────────────────────
        rl_response: JSONResponse | None
        try:
            rl_response = await self._check_limits(request, claims, slug, rpm_limit, tpm_limit)
        except Exception as exc:
            # AC-4: Redis error handling
            logger.error("rate_limit.redis_error", error=str(exc))
            if self.settings.rate_limit_strict:
                error_response = _rl_problem(
                    request,
                    503,
                    "rate-limiting-unavailable",
                    "Rate Limiting Unavailable",
                    "Rate limiting store is temporarily unavailable.",
                )
                await error_response(scope, receive, send)
                return
            logger.warning("rate_limit.fail_open", error=str(exc))
            await self.app(scope, receive, send)
            return

        if rl_response is not None:
            # AC-5: inject RL headers even on 429/503 error responses
            rpm_state = getattr(request.state, "_rl_rpm_state", None)
            tpm_state = getattr(request.state, "_rl_tpm_state", None)

            async def send_429_with_headers(message: Any) -> None:
                if message["type"] == "http.response.start" and rpm_state:
                    headers = list(message.get("headers", []))
                    headers += _rl_headers(slug, rpm_state, tpm_state, tpm_limit)
                    message = {**message, "headers": headers}
                await send(message)

            await rl_response(scope, receive, send_429_with_headers)
            return

        # ── Pass through — attach RL headers on the way out ───────────────────
        # We wrap the send callable to inject headers into the first response message
        rpm_state = getattr(request.state, "_rl_rpm_state", None)
        tpm_state = getattr(request.state, "_rl_tpm_state", None)

        async def send_with_rl_headers(message: Any) -> None:
            if message["type"] == "http.response.start" and rpm_state and tpm_state:
                headers = list(message.get("headers", []))
                headers += _rl_headers(slug, rpm_state, tpm_state, tpm_limit)
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_rl_headers)

    def _resolve_limits(self, endpoint_slug: str) -> tuple[int, int]:
        """Return (rpm_limit, tpm_limit) for the given endpoint, applying per-endpoint overrides.

        Implements: memory/specs/007-rate-limiting-and-throughput.md — AC-13
        """
        rpm = self.settings.rate_limit_rpm
        tpm = self.settings.rate_limit_tpm

        if endpoint_slug == "chat_completions":
            if self.settings.rate_limit_rpm_chat_completions is not None:
                rpm = self.settings.rate_limit_rpm_chat_completions
            if self.settings.rate_limit_tpm_chat_completions is not None:
                tpm = self.settings.rate_limit_tpm_chat_completions
        elif endpoint_slug == "admin":
            if self.settings.rate_limit_rpm_admin is not None:
                rpm = self.settings.rate_limit_rpm_admin
            if self.settings.rate_limit_tpm_admin is not None:
                tpm = self.settings.rate_limit_tpm_admin

        return rpm, tpm

    async def _check_limits(
        self,
        request: Request,
        claims: Any,
        slug: str,
        rpm_limit: int,
        tpm_limit: int,
    ) -> JSONResponse | None:
        """Check RPM limits for both client_id and user_id.

        Returns a JSONResponse (429/503) to short-circuit, or None to allow.

        Implements: memory/specs/007-rate-limiting-and-throughput.md — AC-1, AC-9
        """
        assert self._limiter is not None

        # Check client_id RPM (AC-1)
        client_rpm = await self._limiter.check_and_increment_rpm(claims.client_id, slug, rpm_limit)
        # Store on state for header injection (AC-5)
        request.state._rl_rpm_state = client_rpm

        if not client_rpm.allowed:
            retry_after = max(1, client_rpm.reset_at - int(time.time()))
            logger.warning(
                "rate_limit.rpm_exceeded",
                client_id=claims.client_id,
                endpoint=slug,
                limit=rpm_limit,
            )
            return _rl_problem(
                request,
                429,
                "rate-limit-exceeded-requests",
                "Rate Limit Exceeded",
                f"Client '{claims.client_id}' has exceeded the request rate limit of "
                f"{rpm_limit} RPM for endpoint '{slug}'. Reset in {retry_after} seconds.",
                retry_after=retry_after,
                extra={"scope": slug},
            )

        # Check user_id RPM (AC-9) — separate from client_id.
        #
        # PRM-128: unless they are the same string, which is the normal case for
        # a client_credentials token: there is no human behind it, so the JWT's
        # `sub` and `azp` are both the client. Both checks then increment the
        # *same* Redis key and every request costs two — a client advertised 60
        # RPM was cut off at 30, and the header said 60 the whole way down.
        #
        # The per-user limit exists so one user of a multi-user client cannot
        # eat the client's whole budget. With no distinct user there is nobody
        # to protect from anyone, and charging twice buys nothing.
        # Skipped, not returned from: everything below this block still has to
        # run. Returning early here dropped the TPM pre-flight — and with it
        # `_rl_tpm_state`, which is where the X-RateLimit-* headers come from,
        # so the fix for a wrong limit briefly removed the way to see any limit
        # at all. Caught by re-measuring against the deployment; the suite was
        # green because no test on this path asserts the headers.
        if claims.user_id and claims.user_id != claims.client_id:
            user_rpm = await self._limiter.check_and_increment_rpm(claims.user_id, slug, rpm_limit)
            if not user_rpm.allowed:
                retry_after = max(1, user_rpm.reset_at - int(time.time()))
                logger.warning(
                    "rate_limit.user_rpm_exceeded",
                    user_id=claims.user_id,
                    endpoint=slug,
                    limit=rpm_limit,
                )
                return _rl_problem(
                    request,
                    429,
                    "rate-limit-exceeded-requests",
                    "Rate Limit Exceeded",
                    f"User '{claims.user_id}' has exceeded the request rate limit of "
                    f"{rpm_limit} RPM for endpoint '{slug}'. Reset in {retry_after} seconds.",
                    retry_after=retry_after,
                    extra={"scope": slug},
                )

        # TPM pre-flight check — read the request body max_tokens hint if present
        # The actual body is parsed by the router; here we do a lightweight check
        # based on max_tokens from query or a default sentinel
        # Full pre-check happens in the router; this only guards the counter read
        tpm_state = await self._limiter.check_tpm_budget(
            claims.client_id,
            slug,
            tpm_limit,
            0,  # 0 tokens for the gate check only
        )
        request.state._rl_tpm_state = tpm_state

        return None
