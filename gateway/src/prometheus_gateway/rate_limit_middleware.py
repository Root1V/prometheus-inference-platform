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
_ENDPOINT_SLUG_MAP: dict[str, str] = {
    "/v1/chat/completions": "chat_completions",
}


def _endpoint_slug(path: str) -> str:
    return _ENDPOINT_SLUG_MAP.get(path, _DEFAULT_ENDPOINT)


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
        {"/health", "/metrics", "/v1/models", "/v1/backends", "/v1/usage"}
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
                    reset_ts = str(rpm_state.reset_at).encode()
                    # Use tpm_state if available; fall back to configured limits (RPM blocked before TPM check)
                    tl = str(tpm_state.limit if tpm_state else tpm_limit).encode()
                    tr = str(tpm_state.remaining if tpm_state else tpm_limit).encode()
                    headers += [
                        (b"x-ratelimit-limit-requests", str(rpm_state.limit).encode()),
                        (
                            b"x-ratelimit-remaining-requests",
                            str(max(0, rpm_state.remaining)).encode(),
                        ),
                        (b"x-ratelimit-reset-requests", reset_ts),
                        (b"x-ratelimit-limit-tokens", tl),
                        (b"x-ratelimit-remaining-tokens", tr),
                        (b"x-ratelimit-reset-tokens", reset_ts),
                    ]
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
                reset_ts = str(rpm_state.reset_at).encode()
                headers += [
                    (b"x-ratelimit-limit-requests", str(rpm_state.limit).encode()),
                    (b"x-ratelimit-remaining-requests", str(rpm_state.remaining).encode()),
                    (b"x-ratelimit-reset-requests", reset_ts),
                    (b"x-ratelimit-limit-tokens", str(tpm_state.limit).encode()),
                    (b"x-ratelimit-remaining-tokens", str(tpm_state.remaining).encode()),
                    (b"x-ratelimit-reset-tokens", reset_ts),
                ]
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
            )

        # Check user_id RPM (AC-9) — separate from client_id
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
