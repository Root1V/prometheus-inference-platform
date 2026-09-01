"""Gateway router — /v1/chat/completions proxy, /v1/models, /v1/backends, /v1/usage.

Implements: memory/specs/001-gateway-core.md — AC-1 through AC-7
Implements: memory/specs/006-multi-model-gateway.md — AC-1 through AC-15
Implements: memory/specs/007-rate-limiting-and-throughput.md — AC-6, AC-8, AC-10, AC-11, AC-12, AC-14, AC-15, AC-17, AC-20
Implements: memory/specs/018-observability-telemetry.md — AC-8, AC-10, AC-23, AC-27, AC-28, AC-29
"""

from __future__ import annotations

import json
import time
from datetime import date as _date
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

import httpx
import structlog
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from . import db, pricing
from .models.registry import ModelRegistry
from .models.schemas import ChatCompletionRequest, EmbeddingsRequest, ImageGenerationRequest
from .telemetry import get_logger, get_tracer, metrics_store

if TYPE_CHECKING:
    from .models.backends import BackendPool

logger = get_logger(__name__)
_tracer = get_tracer("gateway")

_BASE_URL = "https://prometheus.internal/errors"
# Token approximation ratio — 4 chars ≈ 1 token (AC-12)
_CHARS_PER_TOKEN = 4
# RM-09: rough per-image token cost for context-budget estimation (AC-12 predates
# vision content parts). Matches common VLM low/mid-resolution tile estimates —
# not exact, just enough to keep the existing context-exceeded guard meaningful.
_IMAGE_TOKEN_ESTIMATE = 512


def _problem(
    request: Request,
    status: int,
    error_type: str,
    title: str,
    detail: str,
    extra_headers: dict[str, str] | None = None,
) -> JSONResponse:
    """Return an RFC 9457 Problem Details response.

    Implements: memory/specs/001-gateway-core.md — error format requirement
    Implements: memory/specs/018-observability-telemetry.md — AC-27 (trace_id in error body)
    """
    request_id = getattr(getattr(request, "state", None), "request_id", "unknown")
    # AC-27: include trace_id in error body for client-side log correlation
    trace_id = getattr(getattr(request, "state", None), "trace_id", None)
    if trace_id is None:
        trace_id = structlog.contextvars.get_contextvars().get("trace_id", "none")
    return JSONResponse(
        status_code=status,
        content={
            "type": f"{_BASE_URL}/{error_type}",
            "title": title,
            "status": status,
            "detail": detail,
            "instance": str(request.url.path),
            "request_id": request_id,
            "trace_id": trace_id,
        },
        media_type="application/problem+json",
        headers=extra_headers or {},
    )


def _estimate_tokens(messages: list[dict[str, Any]]) -> int:
    """Estimate total tokens in a messages list using 4 chars ≈ 1 token.

    Implements: memory/specs/007-rate-limiting-and-throughput.md — AC-12
    RM-09: list-content messages (vision) add each image as a flat token
    estimate instead of stringifying the content-part dicts.
    """
    total_tokens = 0
    for m in messages:
        # RM-35: an assistant message that only calls a tool has content: None.
        content = m.get("content") or ""
        if isinstance(content, list):
            for part in content:
                if not isinstance(part, dict):
                    continue
                if part.get("type") == "image_url":
                    total_tokens += _IMAGE_TOKEN_ESTIMATE
                else:
                    total_tokens += len(str(part.get("text", ""))) // _CHARS_PER_TOKEN
        else:
            total_tokens += len(str(content)) // _CHARS_PER_TOKEN
    return max(1, total_tokens)


def create_router(registry: ModelRegistry, pool: "BackendPool") -> APIRouter:
    """Factory — creates an APIRouter bound to the given registry and backend pool.

    Implements: memory/specs/006-multi-model-gateway.md — AC-13
    """

    router = APIRouter()

    # ── GET /v1/models ──────────────────────────────────────────────────────
    # Implements: memory/specs/006-multi-model-gateway.md — AC-1
    @router.get("/v1/models")
    async def list_models() -> dict[str, Any]:
        """List active models (those with a backend_url set). No auth required."""
        from opentelemetry.trace import SpanKind

        with _tracer.start_as_current_span("models.list", kind=SpanKind.INTERNAL) as span:
            models = registry.list_active_models()
            span.set_attribute("model_count", len(models))
            return {
                "object": "list",
                "data": [
                    {
                        "id": m.id,
                        "object": "model",
                        "owned_by": "prometheus",
                        "context_length": m.context_length,
                        "family": m.family,
                        "quantization": m.quantization,
                        "modality": m.modality,
                    }
                    for m in models
                ],
            }

    # ── GET /v1/models/mine ──────────────────────────────────────────────────
    # RM-45: unlike GET /v1/models above (public, lists the full catalog),
    # this requires a valid Bearer token and returns only the models the
    # caller's own model:<id> scopes grant — model access can be assigned or
    # changed after a client is created, so a client may want to check what
    # it currently has before making an inference request.
    @router.get("/v1/models/mine")
    async def list_my_models(request: Request) -> Any:
        """List only the models the caller's JWT authorizes it to use."""
        from opentelemetry.trace import SpanKind

        claims = getattr(getattr(request, "state", None), "claims", None)
        if claims is None:
            return _problem(
                request,
                401,
                "missing-credentials",
                "Unauthorized",
                "This endpoint requires a valid Bearer token.",
            )

        with _tracer.start_as_current_span("models.list_mine", kind=SpanKind.INTERNAL) as span:
            # RM-14: same admin:write carve-out used for the Playground's own
            # inference calls — admin:write already implies full model
            # management, so seeing every model here isn't a new privilege.
            is_admin_bypass = claims.has_scope("admin:write")
            authorized = [
                m
                for m in registry.list_active_models()
                if is_admin_bypass or claims.has_model_scope(m.id)
            ]
            span.set_attribute("model_count", len(authorized))
            return {
                "object": "list",
                "data": [
                    {
                        "id": m.id,
                        "object": "model",
                        "owned_by": "prometheus",
                        "context_length": m.context_length,
                        "family": m.family,
                        "quantization": m.quantization,
                        "modality": m.modality,
                    }
                    for m in authorized
                ],
            }

    # ── GET /v1/backends ────────────────────────────────────────────────────
    # Implements: memory/specs/006-multi-model-gateway.md — AC-14
    # Implements: memory/specs/007-rate-limiting-and-throughput.md — AC-10, AC-20
    @router.get("/v1/backends")
    async def list_backends(request: Request) -> Any:
        """Admin diagnostic endpoint — list all models with backend status + CB state.

        Requires admin:read scope.
        Implements: memory/specs/006-multi-model-gateway.md — AC-14
        Implements: memory/specs/007-rate-limiting-and-throughput.md — AC-10, AC-20
        Implements: memory/specs/022-opentelemetry-sdk-instrumentation.md — G-21
        """
        from opentelemetry.trace import SpanKind

        claims = getattr(getattr(request, "state", None), "claims", None)
        if claims is None or not claims.has_scope("admin:read"):
            return _problem(
                request,
                403,
                "forbidden",
                "Forbidden",
                "This endpoint requires admin:read scope.",
            )

        with _tracer.start_as_current_span("gateway.backends.list", kind=SpanKind.INTERNAL) as span:
            data = []
            for m in registry.list_models():
                entry: dict[str, Any] = {
                    "id": m.id,
                    "backend_url": m.backend_url,
                    "status": m.backend_status,
                    "modality": m.modality,
                }

                # AC-20: circuit breaker state per backend
                cb = pool.get_circuit_breaker(m.id) if m.backend_url else None
                if cb is not None:
                    try:
                        cb_state = await cb.get_state()
                        entry["circuit_state"] = cb_state.state
                        entry["consecutive_failures"] = cb_state.consecutive_failures
                        entry["circuit_opened_at"] = (
                            datetime.fromtimestamp(cb_state.opened_at, tz=timezone.utc).isoformat()
                            if cb_state.opened_at
                            else None
                        )
                        entry["circuit_recovery_at"] = (
                            datetime.fromtimestamp(
                                cb_state.recovery_at, tz=timezone.utc
                            ).isoformat()
                            if cb_state.recovery_at
                            else None
                        )
                        if cb_state.is_open:
                            entry["status"] = "circuit-open"
                        elif cb_state.is_half_open:
                            entry["status"] = "circuit-half-open"
                    except Exception:
                        entry["circuit_state"] = "unknown"
                        entry["consecutive_failures"] = 0
                        entry["circuit_opened_at"] = None
                        entry["circuit_recovery_at"] = None
                else:
                    entry["circuit_state"] = "closed"
                    entry["consecutive_failures"] = 0
                    entry["circuit_opened_at"] = None
                    entry["circuit_recovery_at"] = None

                # AC-10: requests_last_minute from Redis
                rl_redis = getattr(pool, "_redis", None)
                if rl_redis is not None:
                    try:
                        from .rate_limiter import RateLimiter

                        rl = RateLimiter(rl_redis)
                        entry["requests_last_minute"] = await rl.get_rpm_count(
                            m.id, "chat_completions"
                        )
                    except Exception:
                        entry["requests_last_minute"] = 0
                else:
                    entry["requests_last_minute"] = 0

                data.append(entry)

            span.set_attribute("http.status_code", 200)
            span.set_attribute("backend_count", len(data))
            return {"object": "list", "data": data}

    # ── GET /v1/usage ────────────────────────────────────────────────────────
    # Implements: docs/roadmap.md — RM-32 (persisted history + per-model breakdown)
    @router.get("/v1/usage")
    async def get_usage(request: Request, date: str | None = None) -> Any:
        """Return per-client token usage (with a per-model breakdown) for one UTC day.

        Requires admin:read scope. Defaults to today; pass ?date=YYYY-MM-DD for a
        past day. Implements: docs/roadmap.md — RM-32.
        Implements: memory/specs/022-opentelemetry-sdk-instrumentation.md — G-9
        """
        from opentelemetry.trace import SpanKind

        claims = getattr(getattr(request, "state", None), "claims", None)
        if claims is None or not claims.has_scope("admin:read"):
            return _problem(
                request, 403, "forbidden", "Forbidden", "This endpoint requires admin:read scope."
            )

        if date is None:
            target_day = datetime.now(tz=timezone.utc).date()
        else:
            try:
                target_day = _date.fromisoformat(date)
            except ValueError:
                return _problem(
                    request,
                    400,
                    "invalid-date",
                    "Invalid Date",
                    f"{date!r} is not a valid YYYY-MM-DD date.",
                )

        with _tracer.start_as_current_span("usage.query", kind=SpanKind.INTERNAL) as span:
            user_id = claims.user_id if claims else "unknown"
            span.set_attribute("user_id", user_id)

            try:
                rows = await db.query_usage_day(target_day)
            except Exception as exc:
                logger.error("usage.db_error", error=str(exc))
                span.set_attribute("http.status_code", 503)
                return _problem(
                    request,
                    503,
                    "usage-store-unavailable",
                    "Usage Store Unavailable",
                    "Unable to read usage data from the store.",
                )

            price_table = pricing.get_pricing_table()
            by_client: dict[str, dict[str, Any]] = {}
            for row in rows:
                entry = by_client.setdefault(
                    row.client_id,
                    {
                        "client_id": row.client_id,
                        "prompt_tokens": 0,
                        "completion_tokens": 0,
                        "total_tokens": 0,
                        "request_count": 0,
                        "estimated_cost_usd": None,
                        "by_model": [],
                    },
                )
                entry["prompt_tokens"] += row.prompt_tokens
                entry["completion_tokens"] += row.completion_tokens
                entry["total_tokens"] += row.prompt_tokens + row.completion_tokens
                entry["request_count"] += row.request_count
                model_cost = price_table.estimate_cost_usd(
                    row.model_id, row.prompt_tokens, row.completion_tokens
                )
                if model_cost is not None:
                    entry["estimated_cost_usd"] = (entry["estimated_cost_usd"] or 0.0) + model_cost
                entry["by_model"].append(
                    {
                        "model_id": row.model_id,
                        "prompt_tokens": row.prompt_tokens,
                        "completion_tokens": row.completion_tokens,
                        "total_tokens": row.prompt_tokens + row.completion_tokens,
                        "request_count": row.request_count,
                        "estimated_cost_usd": model_cost,
                    }
                )

            span.set_attribute("http.status_code", 200)
            return {
                "object": "list",
                "window": target_day.isoformat(),
                "data": list(by_client.values()),
            }

    # ── POST /v1/chat/completions ────────────────────────────────────────────
    @router.post("/v1/chat/completions")
    async def chat_completions(
        body: ChatCompletionRequest,
        request: Request,
    ) -> Any:
        """Proxy chat completions to the correct llama-server backend.

        Implements: memory/specs/001-gateway-core.md — AC-1, AC-2, AC-5, AC-6, AC-7
        Implements: memory/specs/006-multi-model-gateway.md — AC-2, AC-3, AC-4, AC-5, AC-6, AC-8
        Implements: memory/specs/022-opentelemetry-sdk-instrumentation.md — G-7, AC-8 to AC-11
        """
        from opentelemetry.trace import SpanKind, StatusCode

        claims = getattr(getattr(request, "state", None), "claims", None)
        request_id = getattr(getattr(request, "state", None), "request_id", "unknown")

        with _tracer.start_as_current_span("inference.request", kind=SpanKind.INTERNAL) as inf_span:
            inf_span.set_attribute("http.method", "POST")
            inf_span.set_attribute("http.route", "/v1/chat/completions")
            inf_span.set_attribute("model", body.model)
            inf_span.set_attribute("user_id", claims.user_id if claims else "unknown")
            inf_span.set_attribute("client_id", claims.client_id if claims else "unknown")

            # AC-5 (006): validate model exists in registry. Checked before the RM-07
            # scope checks below — GET /v1/models is public ("No auth required"), so
            # the model catalog isn't secret and there's nothing to protect by hiding
            # existence behind authorization.
            entry = registry.get(body.model)
            if entry is None:
                inf_span.set_attribute("http.status_code", 400)
                return _problem(
                    request,
                    400,
                    "unknown-model",
                    "Unknown Model",
                    f"Model {body.model!r} is not registered. "
                    f"Use GET /v1/models for the list of available models.",
                )

            # RM-07: inference:read/inference:stream were documented scopes but never
            # actually enforced here — any valid JWT could call any model.
            # See memory/wiki/auth-model.md.
            # RM-14: admin:write holders (the admin dashboard's own session, used by
            # the model playground) bypass both scope checks below — admin:write
            # already implies full model management control (admin:models), so
            # letting it also invoke any model for testing isn't a new privilege,
            # just an explicit, narrow carve-out. Everything else about the request
            # (usage/cost recording, rate limiting, circuit breaker) still applies
            # exactly as for a real client — this is the real endpoint, not a proxy.
            is_admin_bypass = claims is not None and claims.has_scope("admin:write")
            required_scope = "inference:stream" if body.stream else "inference:read"
            if claims is None or not (claims.has_scope(required_scope) or is_admin_bypass):
                inf_span.set_attribute("http.status_code", 403)
                return _problem(
                    request,
                    403,
                    "forbidden",
                    "Forbidden",
                    f"This endpoint requires {required_scope} scope.",
                )

            # RM-07: per-model grant, deny-by-default — a client with no model:*
            # scope at all has no model access, even with inference:read/stream.
            if not (claims.has_model_scope(body.model) or is_admin_bypass):
                inf_span.set_attribute("http.status_code", 403)
                return _problem(
                    request,
                    403,
                    "forbidden",
                    "Forbidden",
                    f"This client is not authorized to use model {body.model!r}. "
                    "Contact the platform operator to request access.",
                )

            # RM-09: reject image content parts against a non-vision model. Placed
            # with the other request-shape validation (400s), after the auth checks
            # above since it's about the request, not who's allowed to send it.
            has_image = any(
                isinstance(m.content, list) and any(part.type == "image_url" for part in m.content)
                for m in body.messages
            )
            if has_image and entry.modality != "vision":
                inf_span.set_attribute("http.status_code", 400)
                return _problem(
                    request,
                    400,
                    "modality-mismatch",
                    "Modality Mismatch",
                    f"Model {body.model!r} does not support image input "
                    f"(modality={entry.modality!r}). Use a vision-capable model.",
                )

            # AC-6 (007): enforce max_tokens ≤ context_length
            if body.max_tokens is not None and body.max_tokens > entry.context_length:
                inf_span.set_attribute("http.status_code", 400)
                return _problem(
                    request,
                    400,
                    "context-exceeded",
                    "Context Exceeded",
                    f"max_tokens={body.max_tokens} exceeds the context length "
                    f"({entry.context_length}) for model {body.model!r}.",
                )

            # AC-12 (007): validate estimated message tokens ≤ context_length
            raw_messages = [m.model_dump() for m in body.messages]
            estimated_input_tokens = _estimate_tokens(raw_messages)
            if estimated_input_tokens > entry.context_length:
                inf_span.set_attribute("http.status_code", 400)
                return _problem(
                    request,
                    400,
                    "context-exceeded",
                    "Context Exceeded",
                    f"Estimated message tokens ({estimated_input_tokens}) exceed the context length "
                    f"({entry.context_length}) for model {body.model!r}.",
                )
            if (
                body.max_tokens is not None
                and (estimated_input_tokens + body.max_tokens) > entry.context_length
            ):
                inf_span.set_attribute("http.status_code", 400)
                return _problem(
                    request,
                    400,
                    "context-exceeded",
                    "Context Exceeded",
                    f"Estimated total tokens ({estimated_input_tokens + body.max_tokens}) exceed "
                    f"the context length ({entry.context_length}) for model {body.model!r}.",
                )

            # AC-4 (006): model registered but no active backend
            if entry.backend_url is None:
                inf_span.set_attribute("http.status_code", 503)
                return _problem(
                    request,
                    503,
                    "model-not-loaded",
                    "Model Not Loaded",
                    f"Model {body.model!r} is registered but has no active backend. "
                    "Contact the platform operator.",
                )

            # AC-14 (007): circuit breaker check — fast-fail if circuit is OPEN
            cb = pool.get_circuit_breaker(entry.id)
            if cb is not None:
                try:
                    cb_allowed = await cb.allow_request()
                    if not cb_allowed:
                        cb_state = await cb.get_state()
                        retry_after = max(1, int((cb_state.recovery_at or 0) - time.time()))
                        recovery_iso = (
                            datetime.fromtimestamp(
                                cb_state.recovery_at, tz=timezone.utc
                            ).isoformat()
                            if cb_state.recovery_at
                            else None
                        )
                        inf_span.set_attribute("http.status_code", 503)
                        return _problem(
                            request,
                            503,
                            "backend-unavailable",
                            "Backend Unavailable",
                            f"Backend '{entry.id}' circuit is {cb_state.state}. "
                            f"Recovery expected at {recovery_iso}.",
                            extra_headers={"Retry-After": str(retry_after)},
                        )
                except Exception as exc:
                    logger.warning(
                        "circuit_breaker.check_error", backend_id=entry.id, error=str(exc)
                    )

            payload = body.to_llama_payload()

            target_url = f"{entry.backend_url.rstrip('/')}/v1/chat/completions"

            # AC-8 (006): log model and backend_url on every inference request
            # AC-8 (018): forward X-Trace-ID to backend (AC-8, AC-28)
            trace_id = getattr(getattr(request, "state", None), "trace_id", None)
            if trace_id is None:
                trace_id = structlog.contextvars.get_contextvars().get("trace_id", "none")

            logger.info(
                "llama.forwarding",
                model=body.model,
                backend_url=entry.backend_url,
                request_id=request_id,
            )

            # AC-15 (006): use shared pooled client
            client = pool.get(entry.backend_url)

            try:
                if body.stream:
                    # AC-7 (006): SSE streaming — retry NOT applied (AC-17c)
                    return await _stream_response(
                        request, client, target_url, payload, pool, entry.id, trace_id
                    )
                else:
                    await metrics_store.inc_requests_active()
                    backend_start = time.monotonic()
                    try:
                        # AC-17: retry logic inside pool.forward()
                        # AC-8 (018): forward X-Trace-ID header to backend
                        resp = await pool.forward(
                            entry.id,
                            client,
                            target_url,
                            payload,
                            extra_headers={"X-Trace-ID": trace_id},
                        )
                        backend_latency_ms = int((time.monotonic() - backend_start) * 1000)
                    finally:
                        await metrics_store.dec_requests_active()

                    usage_obj: dict[str, Any] = {}
                    try:
                        resp_body: Any = resp.json()
                        usage_obj = (
                            resp_body.get("usage", {}) if isinstance(resp_body, dict) else {}
                        )
                    except Exception:
                        resp_body = {}

                    prompt_tokens: int = usage_obj.get("prompt_tokens", 0)
                    completion_tokens: int = usage_obj.get("completion_tokens", 0)
                    total_tokens = prompt_tokens + completion_tokens
                    tps = (
                        (completion_tokens / (backend_latency_ms / 1000))
                        if backend_latency_ms > 0 and completion_tokens > 0
                        else 0.0
                    )

                    # AC-10 (018): inference.complete with spec-compliant field names
                    # AC-23 (018): Langfuse-ready field names (tokens_prompt, tokens_completion, etc.)
                    finish_reason: str = "unknown"
                    try:
                        choices = (
                            resp_body.get("choices", []) if isinstance(resp_body, dict) else []
                        )
                        if choices:
                            finish_reason = choices[0].get("finish_reason") or "unknown"
                    except Exception:
                        pass
                    log_fields: dict[str, Any] = {
                        "model": body.model,
                        "backend_id": entry.id,
                        "backend_url": entry.backend_url,
                        "request_id": request_id,
                        "tokens_prompt": prompt_tokens,
                        "tokens_completion": completion_tokens,
                        "tokens_total": total_tokens,
                        "latency_ms": backend_latency_ms,
                        "tokens_per_second": round(tps, 2),
                        "finish_reason": finish_reason,
                        "user_id": claims.user_id if claims else "unknown",
                        "client_id": claims.client_id if claims else "unknown",
                    }
                    # AC-29: optional prompt/response summary — opt-in only
                    settings = getattr(getattr(request.app, "state", None), "settings", None)
                    if settings is not None and getattr(
                        settings, "log_include_prompt_summary", False
                    ):
                        first_user = next(
                            (m for m in raw_messages if m.get("role") == "user"), None
                        )
                        if first_user:
                            log_fields["input"] = str(first_user.get("content", ""))[:200]
                        try:
                            if choices:
                                content = (choices[0].get("message") or {}).get("content", "")
                                log_fields["output"] = str(content)[:200]
                        except Exception:
                            pass
                    logger.info("inference.complete", **log_fields)

                    # Hook MetricsStore (AC-19, AC-20)
                    await metrics_store.record_inference(
                        prompt_tokens=prompt_tokens,
                        completion_tokens=completion_tokens,
                        latency_ms=backend_latency_ms,
                        backend_id=entry.id,
                    )

                    # RM-32: record persisted daily usage
                    await _record_usage(claims, entry.id, prompt_tokens, completion_tokens)

                    # Increment TPM counter with actual token usage
                    rl_redis = getattr(pool, "_redis", None)
                    if rl_redis is not None and claims and total_tokens > 0:
                        try:
                            from .rate_limiter import RateLimiter

                            rl = RateLimiter(rl_redis)
                            await rl.increment_tpm(
                                claims.client_id, "chat_completions", total_tokens
                            )
                            await rl.increment_tpm(claims.user_id, "chat_completions", total_tokens)
                        except Exception as exc:
                            logger.warning("tpm.increment_error", error=str(exc))

                    inf_span.set_attribute("http.status_code", resp.status_code)
                    return JSONResponse(
                        content=resp_body,
                        status_code=resp.status_code,
                        media_type="application/json",
                    )

            except (httpx.ConnectError, httpx.ConnectTimeout, httpx.RemoteProtocolError) as exc:
                logger.error(
                    "llama.unreachable",
                    model=body.model,
                    backend_url=entry.backend_url,
                    error=str(exc),
                )
                await metrics_store.record_inference(
                    prompt_tokens=0,
                    completion_tokens=0,
                    latency_ms=0,
                    backend_id=entry.id,
                    error=True,
                )
                inf_span.set_attribute("http.status_code", 503)
                inf_span.set_status(StatusCode.ERROR, str(exc))
                return _problem(
                    request,
                    503,
                    "backend-unavailable",
                    "Backend Unavailable",
                    "The inference backend is currently unreachable. Please try again later.",
                )
            except Exception as exc:
                # AC-17b: all retries exhausted → 502
                logger.error(
                    "llama.upstream_error",
                    model=body.model,
                    backend_url=entry.backend_url,
                    error=str(exc),
                )
                await metrics_store.record_inference(
                    prompt_tokens=0,
                    completion_tokens=0,
                    latency_ms=0,
                    backend_id=entry.id,
                    error=True,
                )
                inf_span.set_attribute("http.status_code", 502)
                inf_span.set_status(StatusCode.ERROR, str(exc))
                return _problem(
                    request,
                    502,
                    "upstream-error",
                    "Upstream Error",
                    "The inference backend returned an unrecoverable error after retries.",
                )

    # ── POST /v1/embeddings ─────────────────────────────────────────────────
    # Implements: docs/roadmap.md — RM-09 (VLM + embeddings)
    @router.post("/v1/embeddings")
    async def embeddings(body: EmbeddingsRequest, request: Request) -> Any:
        """Proxy embeddings requests to an embedding-capable backend.

        Mirrors the validation order used by /v1/chat/completions: unknown
        model (400) -> wrong modality (400) -> auth (403) -> backend
        availability (503) -> forward.
        """
        claims = getattr(getattr(request, "state", None), "claims", None)
        request_id = getattr(getattr(request, "state", None), "request_id", "unknown")

        entry = registry.get(body.model)
        if entry is None:
            return _problem(
                request,
                400,
                "unknown-model",
                "Unknown Model",
                f"Model {body.model!r} is not registered. "
                f"Use GET /v1/models for the list of available models.",
            )

        if entry.modality != "embedding":
            return _problem(
                request,
                400,
                "modality-mismatch",
                "Modality Mismatch",
                f"Model {body.model!r} is not an embedding model (modality={entry.modality!r}). "
                f"Use GET /v1/models to find an embedding-capable model.",
            )

        # RM-37: same admin:write carve-out as /v1/chat/completions (RM-14) — the
        # Playground's own embeddings calls run under the admin dashboard's
        # session, which has no inference:read/model:<id> grants of its own.
        is_admin_bypass = claims is not None and claims.has_scope("admin:write")
        if claims is None or not (claims.has_scope("inference:read") or is_admin_bypass):
            return _problem(
                request,
                403,
                "forbidden",
                "Forbidden",
                "This endpoint requires inference:read scope.",
            )

        if not (claims.has_model_scope(body.model) or is_admin_bypass):
            return _problem(
                request,
                403,
                "forbidden",
                "Forbidden",
                f"This client is not authorized to use model {body.model!r}. "
                "Contact the platform operator to request access.",
            )

        if entry.backend_url is None:
            return _problem(
                request,
                503,
                "model-not-loaded",
                "Model Not Loaded",
                f"Model {body.model!r} is registered but has no active backend. "
                "Contact the platform operator.",
            )

        cb = pool.get_circuit_breaker(entry.id)
        if cb is not None:
            try:
                if not await cb.allow_request():
                    cb_state = await cb.get_state()
                    retry_after = max(1, int((cb_state.recovery_at or 0) - time.time()))
                    return _problem(
                        request,
                        503,
                        "backend-unavailable",
                        "Backend Unavailable",
                        f"Backend '{entry.id}' circuit is {cb_state.state}.",
                        extra_headers={"Retry-After": str(retry_after)},
                    )
            except Exception as exc:
                logger.warning("circuit_breaker.check_error", backend_id=entry.id, error=str(exc))

        target_url = f"{entry.backend_url.rstrip('/')}/v1/embeddings"
        trace_id = getattr(getattr(request, "state", None), "trace_id", None)
        if trace_id is None:
            trace_id = structlog.contextvars.get_contextvars().get("trace_id", "none")

        logger.info(
            "embeddings.forwarding",
            model=body.model,
            backend_url=entry.backend_url,
            request_id=request_id,
        )

        client = pool.get(entry.backend_url)
        try:
            resp = await pool.forward(
                entry.id,
                client,
                target_url,
                body.to_llama_payload(),
                extra_headers={"X-Trace-ID": trace_id},
            )
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.RemoteProtocolError) as exc:
            logger.error(
                "embeddings.unreachable",
                model=body.model,
                backend_url=entry.backend_url,
                error=str(exc),
            )
            return _problem(
                request,
                503,
                "backend-unavailable",
                "Backend Unavailable",
                "The inference backend is currently unreachable. Please try again later.",
            )
        except Exception as exc:
            logger.error(
                "embeddings.upstream_error",
                model=body.model,
                backend_url=entry.backend_url,
                error=str(exc),
            )
            return _problem(
                request,
                502,
                "upstream-error",
                "Upstream Error",
                "The inference backend returned an unrecoverable error after retries.",
            )

        try:
            resp_body: Any = resp.json()
        except Exception:
            resp_body = {}
        return JSONResponse(
            content=resp_body, status_code=resp.status_code, media_type="application/json"
        )

    # ── POST /v1/images/generations ─────────────────────────────────────────
    # Implements: docs/roadmap.md — RM-38 (image generation)
    @router.post("/v1/images/generations")
    async def images_generations(body: ImageGenerationRequest, request: Request) -> Any:
        """Proxy image-generation requests to an image-capable backend.

        Mirrors /v1/embeddings exactly: buffered, no streaming, no usage/cost
        accounting (there's no token count to log for an image response).
        """
        claims = getattr(getattr(request, "state", None), "claims", None)
        request_id = getattr(getattr(request, "state", None), "request_id", "unknown")

        entry = registry.get(body.model)
        if entry is None:
            return _problem(
                request,
                400,
                "unknown-model",
                "Unknown Model",
                f"Model {body.model!r} is not registered. "
                f"Use GET /v1/models for the list of available models.",
            )

        if entry.modality != "image":
            return _problem(
                request,
                400,
                "modality-mismatch",
                "Modality Mismatch",
                f"Model {body.model!r} is not an image model (modality={entry.modality!r}). "
                f"Use GET /v1/models to find an image-capable model.",
            )

        is_admin_bypass = claims is not None and claims.has_scope("admin:write")
        if claims is None or not (claims.has_scope("inference:read") or is_admin_bypass):
            return _problem(
                request,
                403,
                "forbidden",
                "Forbidden",
                "This endpoint requires inference:read scope.",
            )

        if not (claims.has_model_scope(body.model) or is_admin_bypass):
            return _problem(
                request,
                403,
                "forbidden",
                "Forbidden",
                f"This client is not authorized to use model {body.model!r}. "
                "Contact the platform operator to request access.",
            )

        if entry.backend_url is None:
            return _problem(
                request,
                503,
                "model-not-loaded",
                "Model Not Loaded",
                f"Model {body.model!r} is registered but has no active backend. "
                "Contact the platform operator.",
            )

        cb = pool.get_circuit_breaker(entry.id)
        if cb is not None:
            try:
                if not await cb.allow_request():
                    cb_state = await cb.get_state()
                    retry_after = max(1, int((cb_state.recovery_at or 0) - time.time()))
                    return _problem(
                        request,
                        503,
                        "backend-unavailable",
                        "Backend Unavailable",
                        f"Backend '{entry.id}' circuit is {cb_state.state}.",
                        extra_headers={"Retry-After": str(retry_after)},
                    )
            except Exception as exc:
                logger.warning("circuit_breaker.check_error", backend_id=entry.id, error=str(exc))

        target_url = f"{entry.backend_url.rstrip('/')}/v1/images/generations"
        trace_id = getattr(getattr(request, "state", None), "trace_id", None)
        if trace_id is None:
            trace_id = structlog.contextvars.get_contextvars().get("trace_id", "none")

        logger.info(
            "images_generations.forwarding",
            model=body.model,
            backend_url=entry.backend_url,
            request_id=request_id,
        )

        client = pool.get(entry.backend_url)
        try:
            resp = await pool.forward(
                entry.id,
                client,
                target_url,
                body.to_backend_payload(),
                extra_headers={"X-Trace-ID": trace_id},
            )
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.RemoteProtocolError) as exc:
            logger.error(
                "images_generations.unreachable",
                model=body.model,
                backend_url=entry.backend_url,
                error=str(exc),
            )
            return _problem(
                request,
                503,
                "backend-unavailable",
                "Backend Unavailable",
                "The inference backend is currently unreachable. Please try again later.",
            )
        except Exception as exc:
            logger.error(
                "images_generations.upstream_error",
                model=body.model,
                backend_url=entry.backend_url,
                error=str(exc),
            )
            return _problem(
                request,
                502,
                "upstream-error",
                "Upstream Error",
                "The inference backend returned an unrecoverable error after retries.",
            )

        try:
            resp_body_images: Any = resp.json()
        except Exception:
            resp_body_images = {}
        return JSONResponse(
            content=resp_body_images, status_code=resp.status_code, media_type="application/json"
        )

    return router


async def _record_usage(
    claims: Any, model_id: str, prompt_tokens: int, completion_tokens: int
) -> None:
    """Write persisted per-day, per-client, per-model token counters.

    Implements: docs/roadmap.md — RM-32 (replaces the old Redis daily-TTL counters).
    """
    if claims is None:
        return
    if prompt_tokens + completion_tokens == 0:
        return
    try:
        await db.record_usage(claims.client_id, model_id, prompt_tokens, completion_tokens)
    except Exception as exc:
        logger.warning("usage.db_write_error", error=str(exc))


async def _stream_response(
    request: Request,
    client: httpx.AsyncClient,
    url: str,
    payload: dict[str, Any],
    pool: "BackendPool",
    backend_id: str,
    trace_id: str = "none",
) -> StreamingResponse:
    """Forward a streaming request using a pooled client.

    Implements: memory/specs/001-gateway-core.md — AC-2
    Implements: memory/specs/006-multi-model-gateway.md — AC-7
    Implements: memory/specs/007-rate-limiting-and-throughput.md — AC-8b, AC-17c
    Implements: memory/specs/018-observability-telemetry.md — AC-8 (X-Trace-ID forwarded)
    Flushes each chunk immediately. Closes with 'data: [DONE]' per OpenAI convention.
    Retry is NOT applied (AC-17c: response headers already sent).
    """
    request_id = getattr(getattr(request, "state", None), "request_id", "unknown")
    claims = getattr(getattr(request, "state", None), "claims", None)
    backend_start = time.monotonic()
    cb = pool.get_circuit_breaker(backend_id)

    async def event_generator() -> Any:
        prompt_tokens = 0
        completion_tokens = 0
        stream_error: Exception | None = None
        try:
            # AC-8 (018): forward X-Trace-ID to backend for streaming requests
            async with client.stream(
                "POST",
                url,
                json=payload,
                timeout=120.0,
                headers={"X-Trace-ID": trace_id},
            ) as resp:
                async for line in resp.aiter_lines():
                    if line:
                        if line.startswith("data:") and "[DONE]" not in line:
                            try:
                                chunk = json.loads(line[5:].strip())
                                usage = chunk.get("usage") or {}
                                if usage:
                                    prompt_tokens = usage.get("prompt_tokens", 0)
                                    completion_tokens = usage.get("completion_tokens", 0)
                            except Exception:
                                pass
                        yield f"{line}\n\n"
            if cb:
                await cb.record_success()
        except Exception as exc:
            stream_error = exc
            logger.error(
                "llama.stream_error",
                backend_id=backend_id,
                error=str(exc),
                request_id=request_id,
            )
            if cb:
                await cb.record_failure()
            yield 'data: {"error": "stream interrupted"}\n\n'
        finally:
            yield "data: [DONE]\n\n"
            backend_latency_ms = int((time.monotonic() - backend_start) * 1000)
            tps = (
                (completion_tokens / (backend_latency_ms / 1000))
                if backend_latency_ms > 0 and completion_tokens > 0
                else 0.0
            )
            # AC-8b, AC-10 (018): metering after stream completes with spec field names
            logger.info(
                "inference.complete" if not stream_error else "inference.stream_error",
                backend_id=backend_id,
                tokens_prompt=prompt_tokens,
                tokens_completion=completion_tokens,
                tokens_total=prompt_tokens + completion_tokens,
                latency_ms=backend_latency_ms,
                tokens_per_second=round(tps, 2),
                client_id=claims.client_id if claims else "unknown",
                user_id=claims.user_id if claims else "unknown",
                span_id=None,
            )
            await metrics_store.record_inference(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                latency_ms=backend_latency_ms,
                backend_id=backend_id,
                error=stream_error is not None,
            )
            # RM-32: persisted daily usage for streaming
            await _record_usage(claims, backend_id, prompt_tokens, completion_tokens)

    logger.info("llama.forwarding_stream", backend_id=backend_id, request_id=request_id)
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
