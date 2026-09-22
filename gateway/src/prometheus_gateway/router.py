"""Gateway router — /v1/chat/completions proxy, /v1/models, /v1/backends, /v1/usage.

Implements: memory/specs/001-gateway-core.md — AC-1 through AC-7
Implements: memory/specs/006-multi-model-gateway.md — AC-1 through AC-15
Implements: memory/specs/007-rate-limiting-and-throughput.md — AC-6, AC-8, AC-10, AC-11, AC-12, AC-14, AC-15, AC-17, AC-20
Implements: memory/specs/018-observability-telemetry.md — AC-8, AC-10, AC-23, AC-27, AC-28, AC-29
"""

from __future__ import annotations

import asyncio
import csv
import io
import json
import time
from datetime import date as _date
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Literal, NamedTuple

import httpx
import structlog
from opentelemetry import context as otel_context
from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse

from . import db, idempotency, pricing
from .budget import (
    BudgetReservation,
    BudgetTracker,
    get_client_billing_settings_cached,
    parse_thresholds,
)
from .models.registry import ModelEntry, ModelRegistry, ModelResolution
from .models.schemas import (
    ChatCompletionRequest,
    EmbeddingsRequest,
    ImageGenerationRequest,
    RerankRequest,
    ignored_parameters,
)
from .notifications import send_budget_alert_email
from .telemetry import get_logger, get_tracer, metrics_store

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Sequence

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
# RM-60: cap a single CSV export to ~1 year of usage_events at a time.
_MAX_EXPORT_RANGE_DAYS = 366


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


# RM-70 (decision #2): a client can still target one replica, but through a
# header rather than by putting an instance id in `model`. Keeping `model` for
# models alone is what lets /v1/models list models, lets one grant cover a
# whole group, and lets a request be billed to the model whatever served it.
INSTANCE_HEADER = "X-Prometheus-Instance"


def _served_by_headers(entry: "ModelEntry") -> dict[str, str]:
    """Tell the caller which replica answered — RM-72.

    Without this, a client watching a load-balanced model has no way to tell
    whether balancing is happening at all, or which instance to look at when
    one of them misbehaves. Same idea as LiteLLM's x-litellm-model-id.
    """
    return {INSTANCE_HEADER: entry.label or entry.id, "X-Prometheus-Instance-Id": entry.id}


def _pin_to_instance(
    resolution: "ModelResolution", requested: str
) -> "ModelEntry | None | Literal[False]":
    """Resolve an X-Prometheus-Instance value against the group.

    Accepts either the instance id or its per-model label ("#2"), since the
    request already names the model and the label is unique within it.

    Returns the member, or False when the name doesn't belong to this group —
    the caller turns that into a 400. A pin is explicit: it must never quietly
    fall back to a different replica, because the reason to pin is to reach
    *that* one (reproducing a bug, comparing two engines, draining a node).
    """
    wanted = requested.strip()
    for member in resolution.members:
        if wanted in (member.id, member.label):
            return member
    return False


def _may_use(claims: Any, requested: str, resolution: "ModelResolution") -> bool:
    """Whether this token may use the model *requested* names — RM-70.

    A grant on the model's slug covers every alias it answers to, so naming a
    model doesn't strand clients that were granted it under an older spelling,
    and a client sending the new name doesn't need a second grant. The
    requested string is still accepted on its own, so a grant issued against an
    older name keeps working until it's reissued.

    Deny-by-default is unchanged: a token with no `model:*` scope at all has no
    model access, whatever it asks for.
    """
    return claims.has_model_scope(requested) or (
        bool(resolution.model_key) and claims.has_model_scope(resolution.model_key)
    )


class _GroupHealth(NamedTuple):
    usable: list["ModelEntry"]
    #: backend id -> why it can't take a request, for the 503 body.
    skipped: dict[str, str]
    #: Earliest moment any skipped replica might come back, for Retry-After.
    soonest_recovery_at: float | None


async def _healthy_members(
    pool: "BackendPool",
    request: Request,
    members: "Sequence[ModelEntry]",
) -> _GroupHealth:
    """Members that can take a request right now, in preference order — RM-69.

    The circuit breaker used to be consulted *after* the replica was chosen, so
    one tripped instance returned 503 for the whole model while its healthy
    siblings sat idle: adding a replica bought no fault tolerance at all. The
    check belongs here, across the group, before anything is picked.

    Returns the usable members plus, for the rest, why they were skipped — the
    503 has to be able to say what is actually wrong with each replica.

    A closed circuit is a cheap read, but taking an *open* one through
    `allow_request()` acquires a distributed probe lock held for the whole
    recovery timeout. Spending that on a replica we then don't use would delay
    its recovery just because a sibling happened to be healthy — so the probing
    path is only entered while nothing usable has been found yet.

    RM-72: candidates are ordered least-loaded first, so the replica already
    handling the fewest requests is preferred. Ties keep registry order, which
    leaves a single-instance model behaving exactly as it did before.
    """
    monitor = getattr(getattr(request.app, "state", None), "health_monitor", None)
    members = sorted(members, key=lambda m: pool.load_ratio(m.id))

    usable: list[ModelEntry] = []
    skipped: dict[str, str] = {}
    recoveries: list[float] = []
    for entry in members:
        if monitor is not None and entry.backend_url and monitor.unreachable(entry.backend_url):
            skipped[entry.id] = "unreachable"
            continue

        cb = pool.get_circuit_breaker(entry.id)
        if cb is None:
            usable.append(entry)
            continue

        try:
            state = await cb.get_state()
            if state.is_closed:
                usable.append(entry)
                continue
            if usable:
                # Healthy sibling already found — record it without spending
                # the probe that would otherwise be wasted.
                skipped[entry.id] = f"circuit {state.state}"
            elif await cb.allow_request():
                usable.append(entry)
                continue
            else:
                skipped[entry.id] = f"circuit {state.state}"
            if state.recovery_at:
                recoveries.append(state.recovery_at)
        except Exception as exc:
            # A breaker that can't be read must not make a healthy backend
            # unroutable — Redis being down is not the backend's fault.
            logger.warning("circuit_breaker.check_error", backend_id=entry.id, error=str(exc))
            usable.append(entry)

    return _GroupHealth(usable, skipped, min(recoveries) if recoveries else None)


# RM-87: work that must finish even though the client walked away.
#
# A streamed response is accounted for while the generator unwinds, and that
# unwinding happens inside the request task — which the server cancels the
# moment the connection drops. Every `await` in that path is therefore a place
# where billing, metering and idempotency silently stop happening, and a client
# that disconnects mid-generation is the case most worth billing, not least.
#
# Creating a task is synchronous, so it works even while a generator is being
# closed, and the task is not a child of the cancelled one. The set keeps a
# strong reference, because the loop only holds a weak one and an unreferenced
# task can be collected before it runs.
_detached: set["asyncio.Task[None]"] = set()


def _detach(coro: "Any", *, what: str) -> None:
    task = asyncio.ensure_future(coro)
    _detached.add(task)

    def _done(t: "asyncio.Task[None]") -> None:
        _detached.discard(t)
        exc = t.exception() if not t.cancelled() else None
        if exc is not None:
            logger.error("stream.finalisation_failed", what=what, error=str(exc))

    task.add_done_callback(_done)


# RM-95: OpenTelemetry's GenAI semantic conventions, emitted by hand.
#
# Argus asked for these by name and offered a package that produces them. We
# emit them ourselves for now: it is a handful of constants on a span we already
# create, against a dependency that currently ships as a loose pre-release wheel
# with no index. Their own words were that the table is the contract and hand
# emission is equally fine. When there is an index, the package is the better
# home — the conventions are still experimental and will move.
# PRM-135: `gen_ai.provider.name` carries the product's name, not our internal
# backend id. Only the ids that differ need an entry — `mlx`, `vllm` and
# `sglang` are already what the products are called. `hf_serve` is not: an
# underscore here would reach Argus's dashboards as a provider nobody can find,
# and split the series the day it is corrected.
_ENGINE_PROVIDERS = {
    "llama_cpp": "llama.cpp",
    "sd_cpp": "stable-diffusion.cpp",
    "hf_serve": "hf-serve",
}


# PRM-136: the modalities routed through the pass-through, and the path they
# are forwarded to. `/predict` is hf-serve's, and it is the only engine in
# BACKENDS that serves one of these today — when a second one arrives with a
# different path, this becomes a per-engine lookup rather than a constant.
_PASS_THROUGH_MODALITIES = frozenset({"classification"})
_PASS_THROUGH_PATH = "/predict"


def _trace_id_for(request: Request) -> str:
    """The id this request is logged under, wherever it was set."""
    trace_id = getattr(getattr(request, "state", None), "trace_id", None)
    if trace_id is None:
        trace_id = structlog.contextvars.get_contextvars().get("trace_id", "none")
    return str(trace_id)


def _provider_of(engine: str | None) -> str:
    """The `gen_ai.provider.name` for a backend engine.

    One function rather than the expression inline, because PRM-131 made the
    metrics need the same answer the spans already give — and a rule written
    out at two call sites is the shape that produced PRM-118 and PRM-130.
    """
    return _ENGINE_PROVIDERS.get(engine or "", engine or "unknown")


def _genai_request_attrs(operation: str, model: str, engine: str) -> dict[str, Any]:
    return {
        "gen_ai.operation.name": operation,
        "gen_ai.request.model": model,
        "gen_ai.provider.name": _provider_of(engine),
    }


def _genai_response_attrs(
    *,
    response_model: str | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    finish_reason: str | None = None,
    backend_id: str | None = None,
    ttft_ms: int | None = None,
    first_token_ms: int | None = None,
) -> dict[str, Any]:
    """What the answer turned out to be.

    `gen_ai.response.model` is not redundant with the request's: when a caller
    asks for one name and another is served, that difference is the first thing
    worth seeing. `finish_reason` carries our own termination reason, including
    `client_disconnected` — the distinction between failing and being abandoned,
    which are different problems with different fixes.
    """
    attrs: dict[str, Any] = {}
    if response_model is not None:
        attrs["gen_ai.response.model"] = response_model
    if input_tokens is not None:
        attrs["gen_ai.usage.input_tokens"] = input_tokens
    if output_tokens is not None:
        attrs["gen_ai.usage.output_tokens"] = output_tokens
    if finish_reason is not None:
        attrs["gen_ai.response.finish_reasons"] = [finish_reason]
    # Ours, not OpenTelemetry's: which replica answered, and how long the caller
    # waited to see anything. No standard attribute covers either.
    if backend_id is not None:
        attrs["argus.inference.backend_id"] = backend_id
    if ttft_ms is not None:
        attrs["argus.inference.ttft_ms"] = ttft_ms
    if first_token_ms is not None:
        attrs["argus.inference.first_token_ms"] = first_token_ms
    return attrs


async def _settle_stream(
    claim: "idempotency.Claim",
    emitted: list[str],
    *,
    clean: bool,
    request_id: str | None = None,
) -> None:
    """Store a finished stream, or hand its key back — RM-82.

    `stream_error` inside the generator is already surfaced to the client as an
    in-band error frame, so "clean" here means the generator itself wasn't torn
    down. A stream that ended in an error frame is still not a result worth
    replaying: the caller would receive the failure again and could never get
    past it.
    """
    body = "".join(emitted)
    if not clean or not body or '"error"' in body:
        await idempotency.release(claim)
        return
    await idempotency.complete(claim, 200, idempotency.wrap_stream(body), request_id)


async def _replay_stream(body: str) -> "AsyncIterator[str]":
    """Re-emit a stored SSE body — RM-82.

    One chunk: the client is parsing SSE frames, not timing them, and the
    original pacing carried no information worth reproducing.
    """
    yield body


async def _begin_idempotent(
    request: Request, claims: Any, path: str, payload: Any, model_key: str | None = None
) -> Response | None:
    """Honour an Idempotency-Key header — RM-78.

    A returned response means stop: either the stored result, replayed, or a
    409 explaining why the key can't be honoured. None means proceed — the
    claim is left on `request.state` for the middleware in main.py to settle
    once the response exists.

    Settled centrally rather than at each return because these handlers have a
    dozen exit paths between here and a result, and a new one would silently
    leave the key held for the whole window — blocking exactly the retry it was
    meant to protect.
    """
    key = request.headers.get(idempotency.HEADER)
    if not key or claims is None:
        return None
    outcome = await idempotency.begin(claims.client_id, key, path, payload, model_key=model_key)
    if isinstance(outcome, idempotency.Replay):
        # Deliberately no budget reserve, no usage row, no metrics: replaying
        # is not a second use of the model, which is the entire point.
        # PRM-100: name the generation that was actually billed. This response
        # carries its own request id, and looking that up finds nothing —
        # correctly, since a replay records no usage. Without this the caller
        # holds the only id it has and no way to reach the row explaining what
        # it paid for.
        replay_headers = {"Idempotent-Replay": "true"}
        if outcome.original_request_id:
            replay_headers["X-Idempotent-Replay-Of"] = outcome.original_request_id
        sse = idempotency.unwrap_stream(outcome.body)
        if sse is not None:
            return StreamingResponse(
                _replay_stream(sse),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "X-Accel-Buffering": "no",
                    **replay_headers,
                },
            )
        return JSONResponse(
            content=outcome.body,
            status_code=outcome.status_code,
            media_type="application/json",
            headers=replay_headers,
        )
    if isinstance(outcome, idempotency.Refusal):
        # RM-80: one type per reason. The four need opposite handling — only
        # "still running" resolves by waiting — and a client told to branch on
        # the type can't be asked to match on prose instead, since rewording a
        # message would then break it in silence. A malformed key is a 400: it
        # never conflicted with anything, and calling it a conflict would tell
        # a client it had repeated a request when its key simply didn't fit.
        status = 400 if outcome.kind == idempotency.INVALID_KEY else 409
        title = (
            "Invalid Idempotency Key"
            if outcome.kind == idempotency.INVALID_KEY
            else "Idempotency Conflict"
        )
        headers = (
            {"Retry-After": str(outcome.retry_after_seconds)}
            if outcome.retry_after_seconds is not None
            else None
        )
        return _problem(request, status, outcome.kind, title, outcome.detail, extra_headers=headers)
    request.state.idempotency_claim = outcome
    return None


def _advertised_context_length(resolution: "ModelResolution") -> int | None:
    """RM-77: null where a context window doesn't apply.

    Image generation has no token context, and the registry stores 0 for it —
    which reads as "a window of zero" rather than "no such concept". A client
    checking `prompt_tokens < context_length` before sending would reject every
    image request. null says which of the two it is; 0 needs prior knowledge.
    """
    return None if resolution.modality == "image" else resolution.context_length


def _candidates(members: "Sequence[ModelEntry]") -> list[tuple[str, str]]:
    """(backend_id, origin) pairs for BackendPool.forward_with_failover."""
    return [(m.id, m.backend_url) for m in members if m.backend_url]


def _served_by(
    members: "Sequence[ModelEntry]", served_id: str, fallback: "ModelEntry"
) -> "ModelEntry":
    """The member that actually answered, so metrics and the circuit breaker
    below land on it rather than on the replica we merely tried first.
    """
    for member in members:
        if member.id == served_id:
            return member
    return fallback


def _no_replica_available(
    request: Request,
    model_name: str,
    health: _GroupHealth,
) -> JSONResponse:
    """503 for a group where every replica is out — RM-69.

    Names each replica and why, because "backend unavailable" on a model with
    three replicas tells an operator nothing about which one to go look at.
    """
    detail = ", ".join(f"{bid} ({why})" for bid, why in sorted(health.skipped.items()))
    headers = {}
    if health.soonest_recovery_at:
        headers["Retry-After"] = str(max(1, int(health.soonest_recovery_at - time.time())))
        recovery_iso = datetime.fromtimestamp(
            health.soonest_recovery_at, tz=timezone.utc
        ).isoformat()
        detail += f". Earliest recovery at {recovery_iso}"
    return _problem(
        request,
        503,
        "backend-unavailable",
        "Backend Unavailable",
        f"No replica of model {model_name!r} can take a request: {detail}.",
        extra_headers=headers,
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

    # ── POST /oauth2/token ──────────────────────────────────────────────────
    # PRM-96: an SDK should only ever need one host. Until now it needed two —
    # the gateway for inference and the auth-service for its token — which
    # meant exposing the auth-service to every client that wanted to call us.
    #
    # The gateway proxies token issuance instead of issuing tokens itself:
    # one issuer, one signing key, and no second copy of the client/scope/TTL
    # rules to drift out of step with the first. Same path as upstream, so an
    # SDK migrating off the direct auth-service URL changes only the host.
    @router.post("/oauth2/token")
    async def token(request: Request) -> Response:
        """Proxy OAuth2 token issuance to the auth-service, verbatim.

        Implements: docs/roadmap.md — PRM-96
        """
        settings = getattr(getattr(request.app, "state", None), "settings", None)
        token_url = getattr(settings, "auth_service_token_url", None)
        if not token_url:
            return _problem(
                request,
                503,
                "not-configured",
                "Not Configured",
                "AUTH_SERVICE_TOKEN_URL is not set on the gateway — token issuance "
                "is unavailable through this host.",
            )

        body = await request.body()
        content_type = request.headers.get("content-type", "application/x-www-form-urlencoded")
        try:
            async with httpx.AsyncClient(
                timeout=10.0,
                verify=getattr(settings, "auth_service_tls_verify", True),
            ) as client:
                upstream = await client.post(
                    token_url,
                    content=body,
                    headers={"content-type": content_type},
                )
        except httpx.HTTPError as exc:
            logger.warning("oauth2.proxy_unreachable", error=str(exc))
            return _problem(
                request,
                503,
                "upstream-unavailable",
                "Upstream Unavailable",
                f"The auth-service is currently unreachable: {exc}",
            )

        # Returned as-is, errors included. An OAuth2 client expects
        # {"error": "invalid_client"} per RFC 6749 §5.2 — translating that into
        # problem+json would make the gateway a worse token endpoint than the
        # one it replaces. (The dashboard's /admin/api/auth/login normalizes
        # instead, because its caller is our own SPA, not an OAuth2 client.)
        return Response(
            content=upstream.content,
            status_code=upstream.status_code,
            media_type=upstream.headers.get("content-type", "application/json"),
        )

    # ── GET /share/{token} ──────────────────────────────────────────────────
    # PRM-102: the one-time credential view. Creating and revoking a share link
    # already went through the gateway; opening one did not, which left
    # auth-service as an address an operator had to hand to a person. Worse, the
    # link auth-service builds comes from the base URL of the request that asked
    # for it — and that request arrives from the gateway, so the operator was
    # being shown an internal hostname the recipient could not resolve anyway.
    #
    # Fronting it here fixes both: the link now points at the gateway, which is
    # the address everyone already has, and auth-service stops needing a
    # published port.
    @router.get("/share/{token}")
    async def share_view(token: str, request: Request) -> Response:
        """Proxy the one-time credential page from auth-service.

        Implements: docs/roadmap.md — PRM-102
        """
        settings = getattr(getattr(request.app, "state", None), "settings", None)
        share_url = getattr(settings, "auth_service_share_url", None)
        if not share_url:
            return _problem(
                request,
                503,
                "not-configured",
                "Not Configured",
                "AUTH_SERVICE_SHARE_URL is not set on the gateway — credential "
                "share links cannot be opened through this host.",
            )

        # auth-service stamps used_by_ip/used_by_ua on the row, which is the
        # audit trail for a secret being read. Proxying would record the
        # gateway every time, so the real client is forwarded — *overwritten*,
        # never appended to, so a visitor cannot forge whose read it was.
        client_ip = request.client.host if request.client else "unknown"
        headers = {
            "X-Forwarded-For": client_ip,
            "User-Agent": request.headers.get("user-agent", ""),
        }
        try:
            async with httpx.AsyncClient(
                timeout=10.0,
                verify=getattr(settings, "auth_service_tls_verify", True),
            ) as client:
                upstream = await client.get(f"{share_url}/{token}", headers=headers)
        except httpx.HTTPError as exc:
            logger.warning("share.proxy_unreachable", error=str(exc))
            return _problem(
                request,
                503,
                "upstream-unavailable",
                "Upstream Unavailable",
                f"The auth-service is currently unreachable: {exc}",
            )

        # The page carries Cache-Control: no-store, X-Robots-Tag: noindex and
        # Referrer-Policy: no-referrer. Those are the whole point of the
        # response — a secret rendered in a browser — so pass the headers
        # through rather than rebuilding a set that could fall behind.
        skip = {"content-length", "content-encoding", "transfer-encoding", "connection"}
        return Response(
            content=upstream.content,
            status_code=upstream.status_code,
            headers={k: v for k, v in upstream.headers.items() if k.lower() not in skip},
        )

    # ── GET /v1/models ──────────────────────────────────────────────────────
    # Implements: memory/specs/006-multi-model-gateway.md — AC-1
    @router.get("/v1/models")
    async def list_models() -> dict[str, Any]:
        """List active models (those with a backend_url set). No auth required."""
        from opentelemetry.trace import SpanKind

        with _tracer.start_as_current_span("models.list", kind=SpanKind.INTERNAL) as span:
            # RM-57: every routable name — instance ids as before, plus the
            # catalog name that load-balances across replicas. `served_by` is
            # how a client tells the two apart.
            models = registry.list_served_names()
            span.set_attribute("model_count", len(models))
            return {
                "object": "list",
                "data": [
                    {
                        "id": r.name,
                        "object": "model",
                        "owned_by": "prometheus",
                        "context_length": _advertised_context_length(r),
                        "family": r.members[0].family,
                        "quantization": r.members[0].quantization,
                        "modality": r.modality,
                        "served_by": len(r.members),
                    }
                    for r in models
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
            # RM-57: filter on the routable name, so a client granted the
            # catalog name sees it here even though no single instance is
            # called that.
            authorized = [
                r
                for r in registry.list_served_names()
                if is_admin_bypass or claims.has_model_scope(r.name)
            ]
            span.set_attribute("model_count", len(authorized))
            return {
                "object": "list",
                "data": [
                    {
                        "id": r.name,
                        "object": "model",
                        "owned_by": "prometheus",
                        "context_length": _advertised_context_length(r),
                        "family": r.members[0].family,
                        "quantization": r.members[0].quantization,
                        "modality": r.modality,
                        "served_by": len(r.members),
                    }
                    for r in authorized
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

            def _accumulate(entry: dict[str, Any], key: str, value: float | None) -> None:
                if value is not None:
                    entry[key] = (entry[key] or 0.0) + value

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
                        # RM-60 follow-up: cost broken into what's paid for
                        # input tokens vs. inference (completion) tokens vs.
                        # images, alongside the existing combined total.
                        "prompt_cost_usd": None,
                        "completion_cost_usd": None,
                        "image_cost_usd": None,
                        "by_model": [],
                    },
                )
                entry["prompt_tokens"] += row.prompt_tokens
                entry["completion_tokens"] += row.completion_tokens
                entry["total_tokens"] += row.prompt_tokens + row.completion_tokens
                entry["request_count"] += row.request_count
                # RM-60: read the cost stored at write time — never recompute
                # against the *current* pricing table, or a price change +
                # restart would silently re-price every past day.
                model_cost = row.cost_usd
                _accumulate(entry, "estimated_cost_usd", model_cost)
                _accumulate(entry, "prompt_cost_usd", row.prompt_cost_usd)
                _accumulate(entry, "completion_cost_usd", row.completion_cost_usd)
                _accumulate(entry, "image_cost_usd", row.image_cost_usd)
                entry["by_model"].append(
                    {
                        "model_id": row.model_id,
                        "prompt_tokens": row.prompt_tokens,
                        "completion_tokens": row.completion_tokens,
                        "total_tokens": row.prompt_tokens + row.completion_tokens,
                        "request_count": row.request_count,
                        "estimated_cost_usd": model_cost,
                        "prompt_cost_usd": row.prompt_cost_usd,
                        "completion_cost_usd": row.completion_cost_usd,
                        "image_cost_usd": row.image_cost_usd,
                    }
                )

            span.set_attribute("http.status_code", 200)
            return {
                "object": "list",
                "window": target_day.isoformat(),
                "data": list(by_client.values()),
            }

    # ── GET /v1/usage/export ─────────────────────────────────────────────────
    # Implements: docs/roadmap.md — RM-60 (CSV export over an arbitrary date range)
    @router.get("/v1/usage/export")
    async def export_usage(
        request: Request, start: str, end: str, client_id: str | None = None
    ) -> Any:
        """CSV export of raw usage_events over [start, end] (inclusive UTC days).

        One row per request (not pre-aggregated) so a client can verify the
        exact rate applied to each request, plus a final TOTAL reconciliation
        row. Requires admin:read scope.
        """
        claims = getattr(getattr(request, "state", None), "claims", None)
        if claims is None or not claims.has_scope("admin:read"):
            return _problem(
                request, 403, "forbidden", "Forbidden", "This endpoint requires admin:read scope."
            )

        try:
            start_day = _date.fromisoformat(start)
            end_day = _date.fromisoformat(end)
        except ValueError:
            return _problem(
                request,
                400,
                "invalid-date",
                "Invalid Date",
                "start/end must be valid YYYY-MM-DD dates.",
            )
        if end_day < start_day:
            return _problem(
                request, 400, "invalid-range", "Invalid Range", "end must not be before start."
            )
        if (end_day - start_day).days > _MAX_EXPORT_RANGE_DAYS:
            return _problem(
                request,
                400,
                "range-too-large",
                "Range Too Large",
                f"Date range exceeds the {_MAX_EXPORT_RANGE_DAYS}-day maximum for a single export.",
            )

        try:
            events = await db.query_usage_events_range(start_day, end_day, client_id)
        except Exception as exc:
            logger.error("usage.export_db_error", error=str(exc))
            return _problem(
                request,
                503,
                "usage-store-unavailable",
                "Usage Store Unavailable",
                "Unable to read usage data from the store.",
            )

        generated_at = datetime.now(tz=timezone.utc).isoformat()
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(
            [
                "generated_at",
                "period_start",
                "period_end",
                "client_id",
                "recorded_at",
                "model_id",
                "request_kind",
                "prompt_tokens",
                "completion_tokens",
                "image_count",
                "prompt_price_per_1m",
                "completion_price_per_1m",
                "image_price_each",
                "cost_usd",
                "interrupted",
                # RM-88: `interrupted` stays where it is, in the position SDK
                # clients already parse. The reason is appended, so a consumer
                # reading by index is unaffected and one reading by name gains
                # the answer to "interrupted how?".
                "termination_reason",
                # PRM-100: appended, like every column before them. New columns
                # go at the end so a consumer reading by position is never
                # shifted — a rule we committed to in writing and had documented
                # nowhere until Axonium noticed. It is in the integration guide
                # now, with the column list.
                "request_id",
                "cached_prompt_tokens",
                # PRM-113: appended, by the same rule. `model_id` is now the
                # catalog id, which never changes — it used to be the slug, so
                # naming a model split its history in two. This is the name the
                # model answered to when the row was written, which is what an
                # invoice should show.
                "model_slug",
            ]
        )
        total_prompt = total_completion = total_images = total_cached = 0
        total_cost = 0.0
        any_cost = False
        for ev in events:
            writer.writerow(
                [
                    generated_at,
                    start_day.isoformat(),
                    end_day.isoformat(),
                    ev.client_id,
                    ev.recorded_at.isoformat(),
                    ev.model_id,
                    ev.request_kind,
                    ev.prompt_tokens,
                    ev.completion_tokens,
                    ev.image_count,
                    ev.prompt_price_per_1m,
                    ev.completion_price_per_1m,
                    ev.image_price_each,
                    f"{ev.cost_usd:.6f}" if ev.cost_usd is not None else "",
                    "true" if ev.interrupted else "false",
                    ev.termination_reason,
                    ev.request_id or "",
                    ev.cached_prompt_tokens,
                    ev.model_slug or ev.model_id,
                ]
            )
            total_prompt += ev.prompt_tokens
            total_completion += ev.completion_tokens
            total_images += ev.image_count
            total_cached += ev.cached_prompt_tokens
            if ev.cost_usd is not None:
                total_cost += ev.cost_usd
                any_cost = True
        writer.writerow(
            [
                generated_at,
                start_day.isoformat(),
                end_day.isoformat(),
                client_id or "ALL",
                "",
                "TOTAL",
                "",
                total_prompt,
                total_completion,
                total_images,
                "",
                "",
                "",
                f"{total_cost:.6f}" if any_cost else "",
                # RM-83/RM-88: blank on the total row — how one request ended is
                # a property of that request, and summing it would invent a
                # meaning it does not have. Same for a request id.
                "",
                "",
                "",
                # PRM-100: cached tokens do total, being a count.
                total_cached,
                # PRM-113: blank, like the other per-request labels. A total
                # spans whatever names the model went by.
                "",
            ]
        )
        filename = f"usage-{start_day.isoformat()}-to-{end_day.isoformat()}.csv"
        return Response(
            content=buf.getvalue(),
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    # Declared after /v1/usage/export on purpose: FastAPI matches in
    # declaration order, so registering this parameterised path first makes
    # it swallow "export" as a request id. Caught by that endpoint's own
    # tests, which is the only reason the order is written down here.
    @router.get("/v1/usage/{request_id}")
    async def get_own_usage(request: Request, request_id: str) -> Any:
        """One caller's usage row for one of its own requests — PRM-100.

        Requested by the SDK team, whose argument was ours to have made: we added
        `termination_reason` so a charge for a half-delivered answer could be
        explained, and then put both usage endpoints behind `admin:read`. The
        only party able to look was the one that does not need to. Granting a
        client `admin:read` so it can see its own row would let it see
        everyone's, so this reads exactly one row and only its owner's.

        **404 rather than 403** for a request belonging to someone else, on
        their suggestion: a 403 would confirm that the id exists.

        The token counts mirror the inference response field for field,
        including `prompt_tokens_details.cached_tokens`. An aggregate cannot be
        reconciled against what the caller received once caching is involved,
        and reconciling is the only thing this endpoint is for.
        """
        claims = getattr(getattr(request, "state", None), "claims", None)
        if claims is None:
            return _problem(
                request, 401, "unauthorized", "Unauthorized", "Authentication required."
            )

        event = await db.get_usage_event_for_client(claims.client_id, request_id)
        if event is None:
            return _problem(
                request,
                404,
                "not-found",
                "Not Found",
                f"No usage record for request {request_id!r}.",
            )

        return {
            "request_id": event.request_id,
            # PRM-115: the name the caller used, not the catalog id PRM-113 put
            # in `model_id`. Reconciling means comparing this against the
            # `model` the inference response returned, and the catalog id is
            # neither that value nor one `GET /v1/models` advertises — so a
            # caller could not resolve it to anything. `model_slug` is stored
            # at write time, so a row keeps the name in force when it was
            # billed even after the model is renamed.
            "model": event.model_slug or event.model_id,
            "request_kind": event.request_kind,
            "usage": {
                "prompt_tokens": event.prompt_tokens,
                "completion_tokens": event.completion_tokens,
                "total_tokens": event.prompt_tokens + event.completion_tokens,
                "prompt_tokens_details": {"cached_tokens": event.cached_prompt_tokens},
            },
            "image_count": event.image_count,
            "interrupted": event.interrupted,
            "termination_reason": event.termination_reason,
            "cost_usd": event.cost_usd,
            "instance_id": event.instance_id,
            "created_at": event.recorded_at.isoformat(),
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

        if (params_err := _parameter_check(request, body)) is not None:
            return params_err
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
            # RM-57: `body.model` may name a single instance (as before) or a
            # catalog model served by several replicas. Validation below runs
            # against the group; which replica serves it is decided after.
            resolution = registry.resolve(body.model)
            if resolution is None:
                inf_span.set_attribute("http.status_code", 400)
                return _problem(
                    request,
                    400,
                    "unknown-model",
                    "Unknown Model",
                    f"Model {body.model!r} is not registered. "
                    f"Use GET /v1/models for the list of available models.",
                )
            if resolution.mismatch is not None:
                inf_span.set_attribute("http.status_code", 400)
                return _problem(
                    request,
                    400,
                    "inconsistent-model-group",
                    "Inconsistent Model Group",
                    resolution.mismatch,
                )

            # RM-07: inference:read/inference:stream were documented scopes but never
            # actually enforced here — any valid JWT could call any model.
            # See docs/roadmap.md RM-07.
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
            if not (_may_use(claims, body.model, resolution) or is_admin_bypass):
                inf_span.set_attribute("http.status_code", 403)
                return _problem(
                    request,
                    403,
                    "forbidden",
                    "Forbidden",
                    f"This client is not authorized to use model {body.model!r}. "
                    "Contact the platform operator to request access.",
                )

            # RM-66: reject any model whose modality isn't chat-capable at all —
            # previously only images-into-a-non-vision-model were rejected (below);
            # an embedding or image-generation model passed straight through to the
            # backend and produced garbage output (confirmed live: an embedding
            # model returned repeating-token junk with a 200), silently billing the
            # caller for it instead of a clear error. /v1/embeddings and
            # /v1/images/generations already reject the wrong modality
            # unconditionally (router.py's embeddings/images handlers) — this was
            # the one direction missing.
            if resolution.modality not in ("text", "vision"):
                inf_span.set_attribute("http.status_code", 400)
                return _problem(
                    request,
                    400,
                    "modality-mismatch",
                    "Modality Mismatch",
                    f"Model {body.model!r} does not support chat completions "
                    f"(modality={resolution.modality!r}). Use /v1/embeddings or "
                    f"/v1/images/generations for that modality instead.",
                )

            # RM-09: reject image content parts against a non-vision model. Placed
            # with the other request-shape validation (400s), after the auth checks
            # above since it's about the request, not who's allowed to send it.
            has_image = any(
                isinstance(m.content, list) and any(part.type == "image_url" for part in m.content)
                for m in body.messages
            )
            if has_image and resolution.modality != "vision":
                inf_span.set_attribute("http.status_code", 400)
                return _problem(
                    request,
                    400,
                    "modality-mismatch",
                    "Modality Mismatch",
                    f"Model {body.model!r} does not support image input "
                    f"(modality={resolution.modality!r}). Use a vision-capable model.",
                )

            # AC-6 (007): enforce max_tokens ≤ context_length
            if body.max_tokens is not None and body.max_tokens > resolution.context_length:
                inf_span.set_attribute("http.status_code", 400)
                return _problem(
                    request,
                    400,
                    "context-exceeded",
                    "Context Exceeded",
                    f"max_tokens={body.max_tokens} exceeds the context length "
                    f"({resolution.context_length}) for model {body.model!r}.",
                )

            # AC-12 (007): validate estimated message tokens ≤ context_length
            raw_messages = [m.model_dump() for m in body.messages]
            estimated_input_tokens = _estimate_tokens(raw_messages)
            if estimated_input_tokens > resolution.context_length:
                inf_span.set_attribute("http.status_code", 400)
                return _problem(
                    request,
                    400,
                    "context-exceeded",
                    "Context Exceeded",
                    f"Estimated message tokens ({estimated_input_tokens}) exceed the context length "
                    f"({resolution.context_length}) for model {body.model!r}.",
                )
            if (
                body.max_tokens is not None
                and (estimated_input_tokens + body.max_tokens) > resolution.context_length
            ):
                inf_span.set_attribute("http.status_code", 400)
                return _problem(
                    request,
                    400,
                    "context-exceeded",
                    "Context Exceeded",
                    f"Estimated total tokens ({estimated_input_tokens + body.max_tokens}) exceed "
                    f"the context length ({resolution.context_length}) for model {body.model!r}.",
                )

            # AC-4 (006): model registered but no active backend. RM-57: with
            # replicas this means *none* of them is up, not just "the one".
            if not resolution.members:
                inf_span.set_attribute("http.status_code", 503)
                return _problem(
                    request,
                    503,
                    "model-not-loaded",
                    "Model Not Loaded",
                    f"Model {body.model!r} is registered but has no active backend. "
                    "Contact the platform operator.",
                )

            # RM-78/RM-82: before the budget reserve, because replaying must
            # not reserve, bill or meter.
            replay = await _begin_idempotent(
                request, claims, "/v1/chat/completions", body.model_dump(), resolution.model_key
            )
            if replay is not None:
                return replay

            # RM-69: AC-14 (007)'s circuit-breaker check, now across the whole
            # group rather than on a replica already chosen — a single tripped
            # instance used to 503 the model while its siblings sat idle.
            health = await _healthy_members(pool, request, resolution.members)
            if not health.usable:
                inf_span.set_attribute("http.status_code", 503)
                return _no_replica_available(request, body.model, health)

            pinned = request.headers.get(INSTANCE_HEADER)
            if pinned:
                target = _pin_to_instance(resolution, pinned)
                if target is False:
                    inf_span.set_attribute("http.status_code", 400)
                    return _problem(
                        request,
                        400,
                        "unknown-instance",
                        "Unknown Instance",
                        f"No instance {pinned!r} serves model {body.model!r}. "
                        f"Drop the {INSTANCE_HEADER} header to let the gateway choose.",
                    )
                if target not in health.usable:
                    inf_span.set_attribute("http.status_code", 503)
                    return _no_replica_available(request, body.model, health)
                health = health._replace(usable=[target])

            entry = health.usable[0]
            # PRM-104: the GenAI attributes used to be set here, on the INTERNAL
            # span — but only for a non-streaming answer, because a streamed one
            # outlives this span and carries its own. That left the same data
            # arriving under two span kinds and two names depending on whether
            # the caller asked for a stream, so a consumer's client-side RED
            # metrics saw half the traffic. Both paths now emit one CLIENT span
            # named `chat <model>`; this one stays INTERNAL and describes the
            # gateway's own work, which is what it actually is.
            # resolve() only ever returns members that have a backend_url;
            # binding it states that invariant for the type checker.
            assert entry.backend_url is not None
            backend_url = entry.backend_url

            # RM-60: hard spend-cap reserve, before forwarding. Completion-token
            # cost isn't known until generation finishes, so this reserves a
            # conservative worst-case estimate (max_tokens, or the remaining
            # context budget) and settles with the real cost once the response
            # completes (non-streaming: below; streaming: _stream_response()).
            budget_redis = getattr(pool, "_redis", None)
            reservation: BudgetReservation | None = None
            alert_thresholds_percent: list[int] = []
            if claims is not None and budget_redis is not None:
                billing_settings = await get_client_billing_settings_cached(claims.client_id)
                cap_usd = billing_settings.monthly_spend_cap_usd if billing_settings else None
                if cap_usd is not None:
                    app_settings = getattr(getattr(request.app, "state", None), "settings", None)
                    default_thresholds = getattr(
                        app_settings, "budget_alert_thresholds_percent_default", "50,80,100"
                    )
                    alert_thresholds_percent = parse_thresholds(
                        billing_settings.alert_thresholds_percent if billing_settings else None,
                        default_thresholds,
                    )
                    worst_case_completion = (
                        body.max_tokens
                        if body.max_tokens is not None
                        else max(0, entry.context_length - estimated_input_tokens)
                    )
                    # PRM-118: by BOTH names. The reserve runs before a replica
                    # is chosen, but the whole group belongs to one catalog
                    # model, so its id is known here — and that is what
                    # `model_price_config` is keyed on. Passing only the public
                    # name found no price for any renamed model, so `est_cost`
                    # was None, no reservation was made, and the spend cap
                    # never engaged. Measured on this deployment:
                    # qwen3-embedding and qwen3-vl-8b were both uncapped.
                    est_cost = pricing.get_pricing_table().estimate_cost_usd(
                        resolution.model_catalog_id or resolution.model_key,
                        estimated_input_tokens,
                        worst_case_completion,
                        model_slug=resolution.model_key,
                    )
                    if est_cost is not None:
                        reservation = await BudgetTracker(budget_redis).reserve(
                            claims.client_id,
                            est_cost,
                            cap_usd=cap_usd,
                            alert_thresholds_percent=alert_thresholds_percent,
                        )
                        await _dispatch_threshold_alerts(
                            request,
                            claims.client_id,
                            cap_usd,
                            reservation.total_spend_usd,
                            reservation.crossed_thresholds,
                        )
                        if not reservation.allowed:
                            inf_span.set_attribute("http.status_code", 402)
                            return _problem(
                                request,
                                402,
                                "spend-cap-exceeded",
                                "Spend Cap Exceeded",
                                f"Client '{claims.client_id}' has reached its monthly spend cap "
                                f"of ${cap_usd:.2f}. Current spend: ${reservation.total_spend_usd:.2f}. "
                                "Contact the platform operator to raise the cap.",
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
            client = pool.get(backend_url)

            try:
                if body.stream:
                    # RM-82: the middleware settles a claim when call_next
                    # returns, which for a stream is before the generator has
                    # emitted anything — it would hand the key back while the
                    # response was still being produced. Take it off the
                    # request so the generator settles it instead, once there
                    # is actually something to store.
                    stream_claim = getattr(request.state, "idempotency_claim", None)
                    request.state.idempotency_claim = None

                    # AC-7 (006): SSE streaming — retry NOT applied (AC-17c)
                    return await _stream_response(
                        request,
                        client,
                        target_url,
                        payload,
                        pool,
                        entry.id,
                        trace_id,
                        served_name=resolution.model_key,
                        billing_id=_billing_id(entry),
                        served_by_headers=_served_by_headers(entry),
                        budget_redis=budget_redis,
                        reservation=reservation,
                        alert_thresholds_percent=alert_thresholds_percent,
                        idempotency_claim=stream_claim,
                        # PRM-105: what the caller asked for; the response half
                        # carries what was actually served.
                        genai_request_attrs=_genai_request_attrs("chat", body.model, entry.backend),
                        engine=entry.backend,
                    )
                else:
                    await metrics_store.inc_requests_active()
                    backend_start = time.monotonic()
                    # PRM-104: monotonic cannot be handed to a span, which wants
                    # an epoch. Both are taken so the latency we report and the
                    # span's duration come from the same call.
                    backend_start_ns = time.time_ns()
                    try:
                        # AC-17: retry logic inside pool.forward()
                        # AC-8 (018): forward X-Trace-ID header to backend
                        # RM-69: and failover to the next healthy replica if
                        # this one is gone rather than merely slow.
                        resp, served_id = await pool.forward_with_failover(
                            _candidates(health.usable),
                            "/v1/chat/completions",
                            payload,
                            extra_headers={"X-Trace-ID": trace_id},
                        )
                        entry = _served_by(health.usable, served_id, entry)
                        backend_latency_ms = int((time.monotonic() - backend_start) * 1000)
                        backend_end_ns = time.time_ns()
                    finally:
                        await metrics_store.dec_requests_active()

                    usage_obj: dict[str, Any] = {}
                    # RM-46: llama.cpp-family backends include a `timings` object
                    # (confirmed live: predicted_per_token_ms is the real average
                    # inter-token latency for this request) — mlx/vllm/sglang
                    # don't, hence the None default rather than assuming it exists.
                    inter_token_ms: float | None = None
                    # RM-62: prefill (prompt-processing) throughput — the same
                    # `timings` object already reports this directly as
                    # `prompt_per_second`; fall back to prompt_n/prompt_ms if a
                    # backend only reports the raw pair.
                    prompt_tps: float | None = None
                    try:
                        resp_body: Any = resp.json()
                        usage_obj = (
                            resp_body.get("usage", {}) if isinstance(resp_body, dict) else {}
                        )
                        timings_obj = (
                            resp_body.get("timings", {}) if isinstance(resp_body, dict) else {}
                        )
                        inter_token_ms = timings_obj.get("predicted_per_token_ms")
                        prompt_tps = timings_obj.get("prompt_per_second")
                        if prompt_tps is None:
                            prompt_ms = timings_obj.get("prompt_ms")
                            prompt_n = timings_obj.get("prompt_n")
                            if prompt_ms and prompt_n:
                                prompt_tps = prompt_n / (prompt_ms / 1000)
                    except Exception:
                        resp_body = {}

                    prompt_tokens: int = usage_obj.get("prompt_tokens", 0)
                    completion_tokens: int = usage_obj.get("completion_tokens", 0)
                    # PRM-100: a subset of prompt_tokens, reported to the caller
                    # already and never stored until now.
                    cached_prompt_tokens: int = (usage_obj.get("prompt_tokens_details") or {}).get(
                        "cached_tokens", 0
                    )
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
                        model_id=resolution.model_key,
                        inter_token_ms=inter_token_ms,
                        tokens_per_second=round(tps, 2) if completion_tokens > 0 else None,
                        prompt_tokens_per_second=(
                            round(prompt_tps, 2) if prompt_tps is not None else None
                        ),
                    )

                    # PRM-104: one CLIENT span for the call to the model, the
                    # same shape the streamed path already emitted. Started and
                    # ended at the call's real bounds rather than wrapping the
                    # block, because the tokens it has to report are only known
                    # after the response is parsed.
                    # PRM-105: `body.model`, not the resolved name. Both halves
                    # used to come from the resolution, so the pair could never
                    # differ and the attribute Argus relies on to spot "asked for
                    # one model, served another" was empty by construction.
                    # Span name follows the convention, {operation} {request.model}
                    # — Argus asked us not to trade the standard for their
                    # cardinality, which they handle on their side.
                    genai_span = _tracer.start_span(
                        f"chat {body.model}",
                        kind=SpanKind.CLIENT,
                        start_time=backend_start_ns,
                    )
                    genai_span.set_attributes(
                        _genai_request_attrs("chat", body.model, entry.backend)
                    )
                    genai_span.set_attributes(
                        _genai_response_attrs(
                            response_model=resolution.model_key,
                            input_tokens=prompt_tokens,
                            output_tokens=completion_tokens,
                            finish_reason=db.TERMINATION_COMPLETE,
                            backend_id=entry.id,
                        )
                    )
                    genai_span.end(end_time=backend_end_ns)

                    # RM-32: record persisted daily usage
                    await _record_usage(
                        claims,
                        _billing_id(entry),
                        prompt_tokens,
                        completion_tokens,
                        model_slug=resolution.model_key,
                        instance_id=entry.id,
                        request_id=request_id,
                        cached_prompt_tokens=cached_prompt_tokens,
                        duration_s=backend_latency_ms / 1000,
                        engine=entry.backend,
                    )

                    # RM-60: settle the spend-cap reservation with the real cost
                    if reservation is not None and reservation.allowed and budget_redis is not None:
                        actual_cost = pricing.get_pricing_table().estimate_cost_usd(
                            _billing_id(entry),
                            prompt_tokens,
                            completion_tokens,
                            model_slug=resolution.model_key,
                        )
                        if actual_cost is not None:
                            newly_crossed = await BudgetTracker(budget_redis).settle(
                                claims.client_id,
                                reservation.reserved_usd,
                                actual_cost,
                                cap_usd=reservation.cap_usd,
                                alert_thresholds_percent=alert_thresholds_percent,
                            )
                            await _dispatch_threshold_alerts(
                                request,
                                claims.client_id,
                                reservation.cap_usd,
                                reservation.total_spend_usd,
                                newly_crossed,
                            )

                    # Increment TPM counter with actual token usage
                    rl_redis = getattr(pool, "_redis", None)
                    if rl_redis is not None and claims and total_tokens > 0:
                        try:
                            from .rate_limiter import RateLimiter

                            rl = RateLimiter(rl_redis)
                            await rl.increment_tpm(
                                claims.client_id, "chat_completions", total_tokens
                            )
                            # PRM-128: same identity, same key — a machine
                            # credential was spending its token budget twice.
                            if claims.user_id and claims.user_id != claims.client_id:
                                await rl.increment_tpm(
                                    claims.user_id, "chat_completions", total_tokens
                                )
                        except Exception as exc:
                            logger.warning("tpm.increment_error", error=str(exc))

                    # RM-77: see the streaming path — the body names the
                    # model, never the replica that served it.
                    if isinstance(resp_body, dict) and resp_body.get("model") is not None:
                        resp_body["model"] = resolution.model_key

                    # RM-78: the middleware settles the key from here — the
                    # handler has the body, and call_next hands middleware
                    # Starlette's streaming wrapper instead of this response.
                    request.state.idempotency_result = (resp.status_code, resp_body)

                    inf_span.set_attribute("http.status_code", resp.status_code)
                    return JSONResponse(
                        content=resp_body,
                        status_code=resp.status_code,
                        media_type="application/json",
                        headers=_served_by_headers(entry),
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
                    model_id=resolution.model_key,
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
                    model_id=resolution.model_key,
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
        if (params_err := _parameter_check(request, body)) is not None:
            return params_err
        claims = getattr(getattr(request, "state", None), "claims", None)
        request_id = getattr(getattr(request, "state", None), "request_id", "unknown")

        # RM-57: may name one instance or a catalog model with replicas.
        resolution = registry.resolve(body.model)
        if resolution is None:
            return _problem(
                request,
                400,
                "unknown-model",
                "Unknown Model",
                f"Model {body.model!r} is not registered. "
                f"Use GET /v1/models for the list of available models.",
            )
        if resolution.mismatch is not None:
            return _problem(
                request,
                400,
                "inconsistent-model-group",
                "Inconsistent Model Group",
                resolution.mismatch,
            )

        if resolution.modality != "embedding":
            return _problem(
                request,
                400,
                "modality-mismatch",
                "Modality Mismatch",
                f"Model {body.model!r} is not an embedding model (modality={resolution.modality!r}). "
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

        if not (_may_use(claims, body.model, resolution) or is_admin_bypass):
            return _problem(
                request,
                403,
                "forbidden",
                "Forbidden",
                f"This client is not authorized to use model {body.model!r}. "
                "Contact the platform operator to request access.",
            )

        # RM-57: no replica of this model is up (not just "the one").
        if not resolution.members:
            return _problem(
                request,
                503,
                "model-not-loaded",
                "Model Not Loaded",
                f"Model {body.model!r} is registered but has no active backend. "
                "Contact the platform operator.",
            )

        # RM-78: before the budget reserve — replaying must not reserve, bill
        # or meter, which is the whole point.
        replay = await _begin_idempotent(
            request, claims, "/v1/embeddings", body.model_dump(), resolution.model_key
        )
        if replay is not None:
            return replay

        # RM-69: circuit-breaker check across the group, before picking.
        health = await _healthy_members(pool, request, resolution.members)
        if not health.usable:
            return _no_replica_available(request, body.model, health)

        pinned = request.headers.get(INSTANCE_HEADER)
        if pinned:
            target = _pin_to_instance(resolution, pinned)
            if target is False:
                return _problem(
                    request,
                    400,
                    "unknown-instance",
                    "Unknown Instance",
                    f"No instance {pinned!r} serves model {body.model!r}. "
                    f"Drop the {INSTANCE_HEADER} header to let the gateway choose.",
                )
            if target not in health.usable:
                return _no_replica_available(request, body.model, health)
            health = health._replace(usable=[target])

        entry = health.usable[0]
        # resolve() only ever returns members that have a backend_url;
        # binding it states that invariant for the type checker.
        assert entry.backend_url is not None

        # RM-60: hard spend-cap reserve, before forwarding — mirrors chat
        # completions' hook below. Worst-case estimate priced as prompt-only
        # (embeddings have no completion tokens).
        budget_redis = getattr(pool, "_redis", None)
        reservation: BudgetReservation | None = None
        alert_thresholds_percent: list[int] = []
        if claims is not None and budget_redis is not None:
            billing_settings = await get_client_billing_settings_cached(claims.client_id)
            cap_usd = billing_settings.monthly_spend_cap_usd if billing_settings else None
            if cap_usd is not None:
                settings = getattr(getattr(request.app, "state", None), "settings", None)
                default_thresholds = getattr(
                    settings, "budget_alert_thresholds_percent_default", "50,80,100"
                )
                alert_thresholds_percent = parse_thresholds(
                    billing_settings.alert_thresholds_percent if billing_settings else None,
                    default_thresholds,
                )
                estimated_tokens = _estimate_text_tokens(body.input)
                est_cost = pricing.get_pricing_table().estimate_cost_usd(
                    resolution.model_catalog_id or resolution.model_key,  # PRM-118
                    estimated_tokens,
                    0,
                    model_slug=resolution.model_key,
                )
                if est_cost is not None:
                    reservation = await BudgetTracker(budget_redis).reserve(
                        claims.client_id,
                        est_cost,
                        cap_usd=cap_usd,
                        alert_thresholds_percent=alert_thresholds_percent,
                    )
                    await _dispatch_threshold_alerts(
                        request,
                        claims.client_id,
                        cap_usd,
                        reservation.total_spend_usd,
                        reservation.crossed_thresholds,
                    )
                    if not reservation.allowed:
                        return _problem(
                            request,
                            402,
                            "spend-cap-exceeded",
                            "Spend Cap Exceeded",
                            f"Client '{claims.client_id}' has reached its monthly spend cap of "
                            f"${cap_usd:.2f}. Current spend: ${reservation.total_spend_usd:.2f}. "
                            "Contact the platform operator to raise the cap.",
                        )

        trace_id = getattr(getattr(request, "state", None), "trace_id", None)
        if trace_id is None:
            trace_id = structlog.contextvars.get_contextvars().get("trace_id", "none")

        logger.info(
            "embeddings.forwarding",
            model=body.model,
            backend_url=entry.backend_url,
            request_id=request_id,
        )

        # RM-46 follow-up: this route never called record_inference() at all —
        # embedding models had zero entries in GET /metrics's backends map,
        # not just missing ttft/inter_token (neither concept applies to a
        # single-shot embedding call anyway — no streaming, no autoregressive
        # token generation).
        backend_start = time.monotonic()
        try:
            # RM-69: failover to the next healthy replica rather than 503ing
            # because the one we happened to pick first is gone.
            resp, served_id = await pool.forward_with_failover(
                _candidates(health.usable),
                "/v1/embeddings",
                body.to_llama_payload(),
                extra_headers={"X-Trace-ID": trace_id},
            )
            entry = _served_by(health.usable, served_id, entry)
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.RemoteProtocolError) as exc:
            logger.error(
                "embeddings.unreachable",
                model=body.model,
                backend_url=entry.backend_url,
                error=str(exc),
            )
            await metrics_store.record_inference(
                prompt_tokens=0,
                completion_tokens=0,
                latency_ms=int((time.monotonic() - backend_start) * 1000),
                backend_id=entry.id,
                model_id=resolution.model_key,
                error=True,
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
            await metrics_store.record_inference(
                prompt_tokens=0,
                completion_tokens=0,
                latency_ms=int((time.monotonic() - backend_start) * 1000),
                backend_id=entry.id,
                model_id=resolution.model_key,
                error=True,
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
        embeddings_usage = resp_body.get("usage", {}) if isinstance(resp_body, dict) else {}
        embeddings_prompt_tokens = embeddings_usage.get("prompt_tokens", 0)
        embeddings_latency_ms = int((time.monotonic() - backend_start) * 1000)

        # RM-60: embeddings never called _record_usage() at all — usage/cost
        # accounting was blind to this entire request type.
        await _record_usage(
            claims,
            _billing_id(entry),
            embeddings_prompt_tokens,
            0,
            request_kind="embedding",
            model_slug=resolution.model_key,
            instance_id=entry.id,
            request_id=getattr(getattr(request, "state", None), "request_id", None),
            duration_s=embeddings_latency_ms / 1000,
            engine=entry.backend,
        )
        if budget_redis is not None and reservation is not None and reservation.allowed:
            actual_cost = pricing.get_pricing_table().estimate_cost_usd(
                _billing_id(entry), embeddings_prompt_tokens, 0, model_slug=resolution.model_key
            )
            if actual_cost is not None:
                newly_crossed = await BudgetTracker(budget_redis).settle(
                    claims.client_id,
                    reservation.reserved_usd,
                    actual_cost,
                    cap_usd=reservation.cap_usd,
                    alert_thresholds_percent=alert_thresholds_percent,
                )
                await _dispatch_threshold_alerts(
                    request,
                    claims.client_id,
                    reservation.cap_usd,
                    reservation.total_spend_usd,
                    newly_crossed,
                )
        await metrics_store.record_inference(
            prompt_tokens=embeddings_prompt_tokens,
            completion_tokens=0,
            latency_ms=embeddings_latency_ms,
            backend_id=entry.id,
            model_id=resolution.model_key,
            # RM-46 follow-up: embeddings have no completion tokens (no text
            # is generated), so "tokens/sec" here means the input side —
            # confirmed via research as the throughput metric that actually
            # applies to embedding serving.
            tokens_per_second=(
                round(embeddings_prompt_tokens / (embeddings_latency_ms / 1000), 2)
                if embeddings_latency_ms > 0 and embeddings_prompt_tokens > 0
                else None
            ),
        )
        if isinstance(resp_body, dict) and resp_body.get("model") is not None:
            resp_body["model"] = resolution.model_key  # RM-77
        request.state.idempotency_result = (resp.status_code, resp_body)  # RM-78
        return JSONResponse(
            content=resp_body,
            status_code=resp.status_code,
            media_type="application/json",
            headers=_served_by_headers(entry),
        )

    # ── POST /v1/rerank ─────────────────────────────────────────────────────
    # Implements: docs/roadmap.md — PRM-106
    #
    # A reranker is a cross-encoder: it scores a query against each document
    # and returns them ordered. It is not text generation, and serving it as
    # though it were is how this arrived — as a bug report describing three
    # problems that were one. Without this endpoint a caller had to send a
    # scoring prompt to /v1/chat/completions, ask for logprobs to rebuild
    # P(yes)/(P(yes)+P(no)) by hand, prefill the chat template themselves, and
    # spend one request per document against a 60 RPM limit. The engine already
    # computes that score; it just was not reachable.
    @router.post("/v1/rerank")
    async def rerank(body: RerankRequest, request: Request) -> Any:
        """Score documents against a query on a rerank-capable backend."""
        if (params_err := _parameter_check(request, body)) is not None:
            return params_err
        claims = getattr(getattr(request, "state", None), "claims", None)
        request_id = getattr(getattr(request, "state", None), "request_id", "unknown")

        resolution = registry.resolve(body.model)
        if resolution is None:
            return _problem(
                request,
                400,
                "unknown-model",
                "Unknown Model",
                f"Model {body.model!r} is not registered. Use GET /v1/models to list them.",
            )
        if resolution.mismatch:
            return _problem(
                request,
                400,
                "inconsistent-model-group",
                "Inconsistent Model Group",
                resolution.mismatch,
            )

        if resolution.modality != "rerank":
            return _problem(
                request,
                400,
                "modality-mismatch",
                "Modality Mismatch",
                f"Model {body.model!r} is not a rerank model (modality={resolution.modality!r}). "
                f"Use GET /v1/models to find a rerank-capable model.",
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
        if not (_may_use(claims, body.model, resolution) or is_admin_bypass):
            return _problem(
                request,
                403,
                "forbidden",
                "Forbidden",
                f"This client is not authorized to use model {body.model!r}. "
                "Contact the platform operator to request access.",
            )

        if not body.documents:
            return _problem(
                request,
                400,
                "validation-error",
                "Validation Error",
                "documents must contain at least one document to score.",
            )

        if not resolution.members:
            return _problem(
                request,
                503,
                "model-not-loaded",
                "Model Not Loaded",
                f"Model {body.model!r} is registered but has no active backend. "
                "Contact the platform operator.",
            )

        replay = await _begin_idempotent(
            request, claims, "/v1/rerank", body.model_dump(), resolution.model_key
        )
        if replay is not None:
            return replay

        health = await _healthy_members(pool, request, resolution.members)
        if not health.usable:
            return _no_replica_available(request, body.model, health)

        pinned = request.headers.get(INSTANCE_HEADER)
        if pinned:
            target = _pin_to_instance(resolution, pinned)
            if target is False:
                return _problem(
                    request,
                    400,
                    "unknown-instance",
                    "Unknown Instance",
                    f"No instance {pinned!r} serves model {body.model!r}. "
                    f"Drop the {INSTANCE_HEADER} header to let the gateway choose.",
                )
            if target not in health.usable:
                return _no_replica_available(request, body.model, health)
            health = health._replace(usable=[target])

        entry = health.usable[0]
        assert entry.backend_url is not None

        # Spend cap: priced prompt-only, like embeddings. A reranker generates
        # no tokens — the query is re-encoded against every document, so the
        # estimate covers query plus all documents.
        budget_redis = getattr(pool, "_redis", None)
        reservation: BudgetReservation | None = None
        alert_thresholds_percent: list[int] = []
        if claims is not None and budget_redis is not None:
            billing_settings = await get_client_billing_settings_cached(claims.client_id)
            cap_usd = billing_settings.monthly_spend_cap_usd if billing_settings else None
            if cap_usd is not None:
                settings = getattr(getattr(request.app, "state", None), "settings", None)
                default_thresholds = getattr(
                    settings, "budget_alert_thresholds_percent_default", "50,80,100"
                )
                alert_thresholds_percent = parse_thresholds(
                    billing_settings.alert_thresholds_percent if billing_settings else None,
                    default_thresholds,
                )
                estimated_tokens = _estimate_text_tokens([body.query, *body.documents])
                est_cost = pricing.get_pricing_table().estimate_cost_usd(
                    resolution.model_catalog_id or resolution.model_key,  # PRM-118
                    estimated_tokens,
                    0,
                    model_slug=resolution.model_key,
                )
                if est_cost is not None:
                    reservation = await BudgetTracker(budget_redis).reserve(
                        claims.client_id,
                        est_cost,
                        cap_usd=cap_usd,
                        alert_thresholds_percent=alert_thresholds_percent,
                    )
                    await _dispatch_threshold_alerts(
                        request,
                        claims.client_id,
                        cap_usd,
                        reservation.total_spend_usd,
                        reservation.crossed_thresholds,
                    )
                    if not reservation.allowed:
                        return _problem(
                            request,
                            402,
                            "spend-cap-exceeded",
                            "Spend Cap Exceeded",
                            f"Client '{claims.client_id}' has reached its monthly spend cap of "
                            f"${cap_usd:.2f}. Current spend: ${reservation.total_spend_usd:.2f}. "
                            "Contact the platform operator to raise the cap.",
                        )

        trace_id = getattr(getattr(request, "state", None), "trace_id", None)
        if trace_id is None:
            trace_id = structlog.contextvars.get_contextvars().get("trace_id", "none")

        logger.info(
            "rerank.forwarding",
            model=body.model,
            backend_url=entry.backend_url,
            documents=len(body.documents),
            request_id=request_id,
        )

        backend_start = time.monotonic()
        try:
            resp, served_id = await pool.forward_with_failover(
                _candidates(health.usable),
                "/v1/rerank",
                body.to_llama_payload(),
                extra_headers={"X-Trace-ID": trace_id},
            )
            entry = _served_by(health.usable, served_id, entry)
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.RemoteProtocolError) as exc:
            logger.error(
                "rerank.unreachable",
                model=body.model,
                backend_url=entry.backend_url,
                error=str(exc),
            )
            await metrics_store.record_inference(
                prompt_tokens=0,
                completion_tokens=0,
                latency_ms=int((time.monotonic() - backend_start) * 1000),
                backend_id=entry.id,
                model_id=resolution.model_key,
                error=True,
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
                "rerank.upstream_error",
                model=body.model,
                backend_url=entry.backend_url,
                error=str(exc),
            )
            await metrics_store.record_inference(
                prompt_tokens=0,
                completion_tokens=0,
                latency_ms=int((time.monotonic() - backend_start) * 1000),
                backend_id=entry.id,
                model_id=resolution.model_key,
                error=True,
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
        rerank_usage = resp_body.get("usage", {}) if isinstance(resp_body, dict) else {}
        rerank_prompt_tokens = rerank_usage.get("prompt_tokens", 0)
        rerank_latency_ms = int((time.monotonic() - backend_start) * 1000)

        await _record_usage(
            claims,
            _billing_id(entry),
            rerank_prompt_tokens,
            0,
            request_kind="rerank",
            model_slug=resolution.model_key,
            instance_id=entry.id,
            request_id=getattr(getattr(request, "state", None), "request_id", None),
            duration_s=rerank_latency_ms / 1000,
            engine=entry.backend,
        )
        if budget_redis is not None and reservation is not None and reservation.allowed:
            actual_cost = pricing.get_pricing_table().estimate_cost_usd(
                _billing_id(entry), rerank_prompt_tokens, 0, model_slug=resolution.model_key
            )
            if actual_cost is not None:
                newly_crossed = await BudgetTracker(budget_redis).settle(
                    claims.client_id,
                    reservation.reserved_usd,
                    actual_cost,
                    cap_usd=reservation.cap_usd,
                    alert_thresholds_percent=alert_thresholds_percent,
                )
                await _dispatch_threshold_alerts(
                    request,
                    claims.client_id,
                    reservation.cap_usd,
                    reservation.total_spend_usd,
                    newly_crossed,
                )
        await metrics_store.record_inference(
            prompt_tokens=rerank_prompt_tokens,
            completion_tokens=0,
            latency_ms=rerank_latency_ms,
            backend_id=entry.id,
            model_id=resolution.model_key,
            tokens_per_second=(
                round(rerank_prompt_tokens / (rerank_latency_ms / 1000), 2)
                if rerank_latency_ms > 0 and rerank_prompt_tokens > 0
                else None
            ),
        )
        if isinstance(resp_body, dict) and resp_body.get("model") is not None:
            resp_body["model"] = resolution.model_key  # RM-77
        request.state.idempotency_result = (resp.status_code, resp_body)  # RM-78
        return JSONResponse(
            content=resp_body,
            status_code=resp.status_code,
            media_type="application/json",
            headers=_served_by_headers(entry),
        )

    # ── POST /v1/models/{model}/predict ─────────────────────────────────────
    # PRM-136: the pass-through. Every other route on this gateway is
    # OpenAI-shaped, because every task it serves has an OpenAI endpoint to be
    # shaped like. Classification does not, and neither does the class of
    # "System 1" decision models (Laya, Jev) that answer typed questions with
    # calibrated probabilities — there is no OpenAI request body for that, and
    # inventing one would be this platform deciding what an engine's API should
    # look like on the engine's behalf.
    #
    # So the body is forwarded verbatim and the answer comes back verbatim.
    # What is NOT passed through is everything that makes this a gateway: the
    # model still has to resolve, the caller still needs `inference:read` and a
    # `model:<slug>` grant, a dead replica is still skipped, the request is
    # still metered and still counts against a budget. The shape is the
    # backend's; the policy is ours.
    @router.post("/v1/models/{model}/predict")
    async def model_predict(model: str, request: Request) -> Any:
        """Forward a request to a model whose task has no OpenAI equivalent."""
        claims = getattr(getattr(request, "state", None), "claims", None)
        request_id = getattr(getattr(request, "state", None), "request_id", "unknown")

        resolution = registry.resolve(model)
        if resolution is None:
            return _problem(
                request,
                400,
                "unknown-model",
                "Unknown Model",
                f"Model {model!r} is not registered. Use GET /v1/models to list them.",
            )
        if resolution.mismatch:
            return _problem(
                request,
                400,
                "inconsistent-model-group",
                "Inconsistent Model Group",
                resolution.mismatch,
            )
        # The inverse of every other handler's modality check, and deliberately
        # so: a model that HAS an OpenAI endpoint must be sent there, or the
        # same model becomes reachable two ways with two different billing
        # paths and two different rate-limit buckets.
        if resolution.modality not in _PASS_THROUGH_MODALITIES:
            return _problem(
                request,
                400,
                "modality-mismatch",
                "Modality Mismatch",
                f"Model {model!r} has modality {resolution.modality!r}, which has its own "
                "endpoint — this route is only for tasks OpenAI has no shape for. "
                "See GET /v1/models.",
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
        if not (_may_use(claims, model, resolution) or is_admin_bypass):
            return _problem(
                request,
                403,
                "forbidden",
                "Forbidden",
                f"This client is not authorized to use model {model!r}. "
                "Contact the platform operator to request access.",
            )

        try:
            body = await request.json()
        except Exception:
            return _problem(
                request,
                400,
                "validation-error",
                "Validation Error",
                "Request body must be valid JSON.",
            )

        if not resolution.members:
            return _problem(
                request,
                503,
                "model-not-loaded",
                "Model Not Loaded",
                f"Model {model!r} is registered but has no active backend. "
                "Contact the platform operator.",
            )
        health = await _healthy_members(pool, request, resolution.members)
        if not health.usable:
            return _no_replica_available(request, model, health)
        entry = health.usable[0]
        assert entry.backend_url is not None

        trace_id = _trace_id_for(request)
        backend_start = time.monotonic()
        try:
            resp, served_id = await pool.forward_with_failover(
                _candidates(health.usable),
                _PASS_THROUGH_PATH,
                body,
                extra_headers={"X-Trace-ID": trace_id},
            )
            entry = _served_by(health.usable, served_id, entry)
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.RemoteProtocolError) as exc:
            logger.error("predict.unreachable", model=model, error=str(exc))
            return _problem(
                request,
                503,
                "backend-unavailable",
                "Backend Unavailable",
                "The inference backend is currently unreachable. Please try again later.",
            )
        except Exception as exc:
            logger.error("predict.upstream_error", model=model, error=str(exc))
            return _problem(
                request,
                502,
                "upstream-error",
                "Upstream Error",
                "The inference backend returned an unrecoverable error.",
            )

        latency_ms = int((time.monotonic() - backend_start) * 1000)
        try:
            resp_body: Any = resp.json()
        except Exception:
            resp_body = {}

        # The backend reports no usage for these tasks, so the input is
        # measured here. Estimated, and named as such in the row rather than
        # written as if it were counted.
        prompt_tokens = _estimate_text_tokens(json.dumps(body, ensure_ascii=False))
        await metrics_store.record_inference(
            prompt_tokens=prompt_tokens,
            completion_tokens=0,
            latency_ms=latency_ms,
            backend_id=entry.id,
            model_id=resolution.model_key,
        )
        await _record_usage(
            claims,
            _billing_id(entry),
            prompt_tokens,
            0,
            request_kind="predict",
            model_slug=resolution.model_key,
            instance_id=entry.id,
            request_id=request_id,
            duration_s=latency_ms / 1000,
            engine=entry.backend,
        )
        return JSONResponse(status_code=resp.status_code, content=resp_body)

    # ── POST /v1/images/generations ─────────────────────────────────────────
    # Implements: docs/roadmap.md — RM-38 (image generation)
    @router.post("/v1/images/generations")
    async def images_generations(body: ImageGenerationRequest, request: Request) -> Any:
        """Proxy image-generation requests to an image-capable backend.

        Mirrors /v1/embeddings: buffered, no streaming. Usage/cost is priced
        per generated image rather than by token count (RM-60).
        """
        if (params_err := _parameter_check(request, body)) is not None:
            return params_err
        claims = getattr(getattr(request, "state", None), "claims", None)
        request_id = getattr(getattr(request, "state", None), "request_id", "unknown")

        # RM-57: may name one instance or a catalog model with replicas.
        resolution = registry.resolve(body.model)
        if resolution is None:
            return _problem(
                request,
                400,
                "unknown-model",
                "Unknown Model",
                f"Model {body.model!r} is not registered. "
                f"Use GET /v1/models for the list of available models.",
            )
        if resolution.mismatch is not None:
            return _problem(
                request,
                400,
                "inconsistent-model-group",
                "Inconsistent Model Group",
                resolution.mismatch,
            )

        if resolution.modality != "image":
            return _problem(
                request,
                400,
                "modality-mismatch",
                "Modality Mismatch",
                f"Model {body.model!r} is not an image model (modality={resolution.modality!r}). "
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

        if not (_may_use(claims, body.model, resolution) or is_admin_bypass):
            return _problem(
                request,
                403,
                "forbidden",
                "Forbidden",
                f"This client is not authorized to use model {body.model!r}. "
                "Contact the platform operator to request access.",
            )

        # RM-57: no replica of this model is up (not just "the one").
        if not resolution.members:
            return _problem(
                request,
                503,
                "model-not-loaded",
                "Model Not Loaded",
                f"Model {body.model!r} is registered but has no active backend. "
                "Contact the platform operator.",
            )

        # RM-78: before the budget reserve — replaying must not reserve, bill
        # or meter, which is the whole point.
        replay = await _begin_idempotent(
            request, claims, "/v1/images/generations", body.model_dump(), resolution.model_key
        )
        if replay is not None:
            return replay

        # RM-69: circuit-breaker check across the group, before picking.
        health = await _healthy_members(pool, request, resolution.members)
        if not health.usable:
            return _no_replica_available(request, body.model, health)

        pinned = request.headers.get(INSTANCE_HEADER)
        if pinned:
            target = _pin_to_instance(resolution, pinned)
            if target is False:
                return _problem(
                    request,
                    400,
                    "unknown-instance",
                    "Unknown Instance",
                    f"No instance {pinned!r} serves model {body.model!r}. "
                    f"Drop the {INSTANCE_HEADER} header to let the gateway choose.",
                )
            if target not in health.usable:
                return _no_replica_available(request, body.model, health)
            health = health._replace(usable=[target])

        entry = health.usable[0]
        # resolve() only ever returns members that have a backend_url;
        # binding it states that invariant for the type checker.
        assert entry.backend_url is not None

        # RM-60: hard spend-cap reserve, before forwarding — worst-case
        # estimate priced per requested image (`n`, default 1).
        budget_redis = getattr(pool, "_redis", None)
        reservation: BudgetReservation | None = None
        alert_thresholds_percent: list[int] = []
        if claims is not None and budget_redis is not None:
            billing_settings = await get_client_billing_settings_cached(claims.client_id)
            cap_usd = billing_settings.monthly_spend_cap_usd if billing_settings else None
            if cap_usd is not None:
                settings = getattr(getattr(request.app, "state", None), "settings", None)
                default_thresholds = getattr(
                    settings, "budget_alert_thresholds_percent_default", "50,80,100"
                )
                alert_thresholds_percent = parse_thresholds(
                    billing_settings.alert_thresholds_percent if billing_settings else None,
                    default_thresholds,
                )
                est_cost = pricing.get_pricing_table().estimate_image_cost_usd(
                    # PRM-118: was `entry.id` — the *instance* id, a third
                    # spelling that is not what prices are keyed on either.
                    resolution.model_catalog_id or resolution.model_key,
                    body.n or 1,
                    model_slug=resolution.model_key,
                )
                if est_cost is not None:
                    reservation = await BudgetTracker(budget_redis).reserve(
                        claims.client_id,
                        est_cost,
                        cap_usd=cap_usd,
                        alert_thresholds_percent=alert_thresholds_percent,
                    )
                    await _dispatch_threshold_alerts(
                        request,
                        claims.client_id,
                        cap_usd,
                        reservation.total_spend_usd,
                        reservation.crossed_thresholds,
                    )
                    if not reservation.allowed:
                        return _problem(
                            request,
                            402,
                            "spend-cap-exceeded",
                            "Spend Cap Exceeded",
                            f"Client '{claims.client_id}' has reached its monthly spend cap of "
                            f"${cap_usd:.2f}. Current spend: ${reservation.total_spend_usd:.2f}. "
                            "Contact the platform operator to raise the cap.",
                        )

        trace_id = getattr(getattr(request, "state", None), "trace_id", None)
        if trace_id is None:
            trace_id = structlog.contextvars.get_contextvars().get("trace_id", "none")

        logger.info(
            "images_generations.forwarding",
            model=body.model,
            backend_url=entry.backend_url,
            request_id=request_id,
        )

        # RM-46 follow-up: same gap as /v1/embeddings — this route never
        # called record_inference() at all, so image models had zero entries
        # in GET /metrics's backends map. No token count and no streaming for
        # a single blocking image-generation call, so only latency applies.
        backend_start = time.monotonic()
        try:
            # RM-69: failover across replicas, same as the other two routes.
            resp, served_id = await pool.forward_with_failover(
                _candidates(health.usable),
                "/v1/images/generations",
                body.to_backend_payload(),
                extra_headers={"X-Trace-ID": trace_id},
            )
            entry = _served_by(health.usable, served_id, entry)
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.RemoteProtocolError) as exc:
            logger.error(
                "images_generations.unreachable",
                model=body.model,
                backend_url=entry.backend_url,
                error=str(exc),
            )
            await metrics_store.record_inference(
                prompt_tokens=0,
                completion_tokens=0,
                latency_ms=int((time.monotonic() - backend_start) * 1000),
                backend_id=entry.id,
                model_id=resolution.model_key,
                error=True,
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
            await metrics_store.record_inference(
                prompt_tokens=0,
                completion_tokens=0,
                latency_ms=int((time.monotonic() - backend_start) * 1000),
                backend_id=entry.id,
                model_id=resolution.model_key,
                error=True,
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
        images_latency_ms = int((time.monotonic() - backend_start) * 1000)
        num_images = (
            len(resp_body_images.get("data", [])) if isinstance(resp_body_images, dict) else 0
        ) or 1

        # RM-60: images/generations never called _record_usage() at all —
        # usage/cost accounting was blind to this entire request type.
        await _record_usage(
            claims,
            _billing_id(entry),
            0,
            0,
            request_kind="image",
            image_count=num_images,
            model_slug=resolution.model_key,
            instance_id=entry.id,
            request_id=getattr(getattr(request, "state", None), "request_id", None),
            duration_s=images_latency_ms / 1000,
            engine=entry.backend,
        )
        if budget_redis is not None and reservation is not None and reservation.allowed:
            actual_cost = pricing.get_pricing_table().estimate_image_cost_usd(
                _billing_id(entry), num_images, model_slug=resolution.model_key
            )
            if actual_cost is not None:
                newly_crossed = await BudgetTracker(budget_redis).settle(
                    claims.client_id,
                    reservation.reserved_usd,
                    actual_cost,
                    cap_usd=reservation.cap_usd,
                    alert_thresholds_percent=alert_thresholds_percent,
                )
                await _dispatch_threshold_alerts(
                    request,
                    claims.client_id,
                    reservation.cap_usd,
                    reservation.total_spend_usd,
                    newly_crossed,
                )
        await metrics_store.record_inference(
            prompt_tokens=0,
            completion_tokens=0,
            latency_ms=images_latency_ms,
            backend_id=entry.id,
            model_id=resolution.model_key,
            # RM-46 follow-up: images/second — confirmed via research
            # (Images Per Second is the standard throughput metric for
            # diffusion-model serving). num_images accounts for n > 1.
            images_per_second=(
                round(num_images / (images_latency_ms / 1000), 3) if images_latency_ms > 0 else None
            ),
        )
        if isinstance(resp_body_images, dict) and resp_body_images.get("model") is not None:
            resp_body_images["model"] = resolution.model_key  # RM-77
        request.state.idempotency_result = (resp.status_code, resp_body_images)  # RM-78
        return JSONResponse(
            content=resp_body_images,
            status_code=resp.status_code,
            media_type="application/json",
            headers=_served_by_headers(entry),
        )

    return router


# PRM-131: request_kind is ours (it keys the usage row and the price);
# gen_ai.operation.name is OpenTelemetry's. They are not the same vocabulary,
# so the translation is a table rather than the string passed straight through.
# `image_generation` is our own — the registry has no image operation.
_GENAI_OPERATIONS = {
    "chat": "chat",
    "embedding": "embeddings",
    "rerank": "rerank",
    "image": "image_generation",
}


def _emit_genai_metrics(
    *,
    request_kind: str,
    model: str,
    engine: str | None,
    prompt_tokens: int,
    completion_tokens: int,
    duration_s: float | None,
    ttft_s: float | None,
    cost_usd: float | None,
    backend_id: str | None,
) -> None:
    """Emit this request's four GenAI metrics through Argus's own instruments.

    Argus's package, not a hand-copy of it: the names, units, instrument kinds
    and meter scope are theirs, so a dashboard built on their conventions finds
    our series without a translation layer, and a rename reaches us as a
    dependency bump instead of a diff nobody remembers to write.

    Imported here rather than at module import time, and that is not style.
    `argus_semconv.metrics` creates its meter at import; OpenTelemetry's
    `_ProxyMeterProvider.get_meter()` accepts `attributes` and drops them, so a
    meter created before `configure_metrics()` runs loses its scope attributes
    permanently — including `argus.semconv.version`, which A-29 had just added.
    This module is imported while `main.py` is still building the app, before
    the provider exists; the first call to this function is not. Measured both
    ways before choosing (P-30).
    """
    from argus_semconv import metrics as genai

    operation = _GENAI_OPERATIONS.get(request_kind, request_kind)
    provider = _provider_of(engine)
    if duration_s is not None:
        genai.record_duration(
            operation=operation, provider=provider, model=model, seconds=duration_s
        )
    genai.record_tokens(
        operation=operation,
        provider=provider,
        model=model,
        input_tokens=prompt_tokens,
        output_tokens=completion_tokens,
    )
    if ttft_s is not None:
        genai.record_ttft(
            provider=provider,
            model=model,
            seconds=ttft_s,
            operation=operation,
            backend_id=backend_id,
        )
    # None is not zero — an unpriced model adds nothing to the counter rather
    # than adding a confident 0.0, the same rule PRM-119 put on the invoice.
    if cost_usd is not None:
        genai.record_cost(cost_usd=cost_usd, model=model)


async def _record_usage(
    claims: Any,
    model_id: str,
    prompt_tokens: int,
    completion_tokens: int,
    *,
    request_kind: str = "chat",
    image_count: int = 0,
    instance_id: str | None = None,
    termination_reason: str = db.TERMINATION_COMPLETE,
    request_id: str | None = None,
    cached_prompt_tokens: int = 0,
    model_slug: str | None = None,
    # PRM-131: what the GenAI metrics need and the usage row does not — how
    # long it took, how long until the caller saw anything, and which engine
    # answered. Optional because a caller that does not know them should still
    # bill; the metric is then simply not recorded, rather than recorded wrong.
    duration_s: float | None = None,
    ttft_s: float | None = None,
    engine: str | None = None,
) -> None:
    """Write an immutable usage_events row + persisted per-day rollup counters,
    and emit this request's GenAI metrics.

    Implements: docs/roadmap.md — RM-32 (replaces the old Redis daily-TTL counters).
    Implements: docs/roadmap.md — RM-60 (#1, #2, #3 — write-time cost, audit
    trail, and covers embeddings/images which previously never called this).
    Implements: docs/roadmap.md — PRM-131 (the four GenAI metrics).
    """
    if claims is None:
        return
    if request_kind == "image":
        if image_count == 0:
            return
    elif prompt_tokens + completion_tokens == 0:
        return
    cost_usd: float | None = None
    try:
        cost_usd = await db.record_usage(
            claims.client_id,
            model_id,
            prompt_tokens,
            completion_tokens,
            request_kind=request_kind,
            image_count=image_count,
            instance_id=instance_id,
            termination_reason=termination_reason,
            request_id=request_id,
            cached_prompt_tokens=cached_prompt_tokens,
            model_slug=model_slug,
        )
    except Exception as exc:
        logger.warning("usage.db_write_error", error=str(exc))
    _emit_genai_metrics(
        request_kind=request_kind,
        model=model_slug or model_id,
        engine=engine,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        duration_s=duration_s,
        ttft_s=ttft_s,
        cost_usd=cost_usd,
        backend_id=instance_id,
    )


def _parameter_check(request: Request, body: Any) -> Response | None:
    """PRM-127: record what we will not act on, and refuse if asked to.

    Ported from OpenRouter, which routes a request to a provider that cannot
    honour every parameter and lets it ignore the rest — the caller usually
    still wants the completion — unless `require_parameters` says otherwise.
    What OpenRouter gets for free is discoverability: its clients can look up
    each provider's supported parameters. Ours cannot, so the ignoring has to
    announce itself, and `X-Prometheus-Ignored-Parameters` is where.

    Returns a 400 only when the caller opted into strictness. Otherwise None,
    having left the names on request.state for the middleware to stamp.
    """
    ignored = ignored_parameters(body)
    if not ignored:
        return None
    request.state.ignored_parameters = ignored
    if not getattr(body, "require_parameters", False):
        logger.info("request.parameters_ignored", path=request.url.path, parameters=ignored)
        return None
    return _problem(
        request,
        400,
        "unknown-parameter",
        "Unknown Parameter",
        f"Unrecognized request argument supplied: {', '.join(ignored)}. This endpoint "
        "accepts an OpenAI-compatible subset — see the integration guide for the fields "
        "it takes. You asked to be told with require_parameters; without it these are "
        "ignored and named in X-Prometheus-Ignored-Parameters.",
    )


def _billing_id(entry: ModelEntry) -> str:
    """The identifier a usage row is keyed on — PRM-113.

    The catalog id, which never changes. Usage used to be keyed on the slug,
    and RM-70 lets an operator name a model once: doing so split its billing
    history into two buckets under two names, and made it miss its pricing.yaml
    entry so it silently billed nothing from that point on. Both were found in
    this deployment's own data.

    Falls back to the instance id for an entry with no catalog row, matching
    how the registry itself resolves it.
    """
    return entry.model_id or entry.id


def _estimate_text_tokens(text_input: str | list[str]) -> int:
    """Worst-case token estimate for an embeddings request's spend-cap reserve."""
    if isinstance(text_input, str):
        total_chars = len(text_input)
    else:
        total_chars = sum(len(s) for s in text_input)
    return max(1, total_chars // _CHARS_PER_TOKEN)


async def _dispatch_threshold_alerts(
    request: Request,
    client_id: str,
    cap_usd: float | None,
    spend_usd: float,
    newly_crossed: list[int],
) -> None:
    """Fire an email for each newly-crossed alert threshold. Blocking smtplib
    runs off the event loop via asyncio.to_thread; failures are logged, never
    raised (an alert-delivery failure must not fail the inference request).
    """
    if not newly_crossed or cap_usd is None:
        return
    settings = getattr(getattr(request.app, "state", None), "settings", None)
    if settings is None:
        return
    for threshold in newly_crossed:
        try:
            await asyncio.to_thread(
                send_budget_alert_email, settings, client_id, threshold, spend_usd, cap_usd
            )
        except Exception as exc:
            logger.warning("billing.alert_dispatch_error", error=str(exc))


async def _stream_response(
    request: Request,
    client: httpx.AsyncClient,
    url: str,
    payload: dict[str, Any],
    pool: "BackendPool",
    backend_id: str,
    trace_id: str = "none",
    *,
    # RM-57: what the client asked for. Usage and pricing are attributed to
    # this, while metrics and the circuit breaker stay on backend_id — the
    # replica that actually did the work.
    served_name: str | None = None,
    # PRM-113: the stable catalog id this request bills against. `served_name`
    # is the slug, which is what the answer is labelled with; they differ once
    # a model has been named.
    billing_id: str | None = None,
    budget_redis: Any = None,
    reservation: "BudgetReservation | None" = None,
    alert_thresholds_percent: list[int] | None = None,
    served_by_headers: dict[str, str] | None = None,
    idempotency_claim: "idempotency.Claim | None" = None,
    genai_request_attrs: dict[str, Any] | None = None,
    # PRM-131: the backend engine, for `gen_ai.provider.name` on the metrics.
    # Taken raw rather than read back out of `genai_request_attrs`, where it is
    # already mapped — one mapping, done in `_provider_of`, called once.
    engine: str | None = None,
) -> StreamingResponse:
    """Forward a streaming request using a pooled client.

    Implements: memory/specs/001-gateway-core.md — AC-2
    Implements: memory/specs/006-multi-model-gateway.md — AC-7
    Implements: memory/specs/007-rate-limiting-and-throughput.md — AC-8b, AC-17c
    Implements: memory/specs/018-observability-telemetry.md — AC-8 (X-Trace-ID forwarded)
    Flushes each chunk immediately. Closes with 'data: [DONE]' per OpenAI convention.
    Retry is NOT applied (AC-17c: response headers already sent).
    RM-60: budget_redis/reservation/alert_thresholds_percent settle the
    caller's spend-cap reservation once the real token counts are known.
    """
    request_id = getattr(getattr(request, "state", None), "request_id", "unknown")
    claims = getattr(getattr(request, "state", None), "claims", None)
    # RM-95: taken now, while the request's span is still current.
    _request_context = otel_context.get_current()
    backend_start = time.monotonic()
    cb = pool.get_circuit_breaker(backend_id)
    # Falls back to the backend id when called without a served name, so this
    # function still behaves as it did when used on its own.
    billed_name = served_name or backend_id

    # RM-72: claimed here, synchronously, rather than inside the generator —
    # the generator doesn't start until the response is consumed, by which time
    # other requests have already selected. A streamed response occupies its
    # backend for as long as it generates, which is exactly when least-loaded
    # routing matters most, so the claim spans the whole generator and is
    # released by it.
    pool.acquire(backend_id)

    async def event_generator() -> Any:
        # RM-82: buffered so a completed stream can be replayed to a retry
        # carrying the same key. Only a clean finish is stored — a stream that
        # broke has nothing complete to replay, and its key is handed back so
        # the retry proceeds, which is what a client wants after a break.
        emitted: list[str] = []
        clean = True
        settled = False
        released = False
        try:
            async for chunk in _stream_events():
                emitted.append(chunk)
                if chunk.startswith("data: [DONE]"):
                    # RM-87: the answer is complete the moment its terminal
                    # frame exists — not when the client gets round to reading
                    # it, and not when this generator is eventually torn down.
                    # Settling here is what makes a streamed replay work for a
                    # client that stops at `[DONE]`, which is every SDK.
                    if idempotency_claim is not None:
                        # Awaited, not detached: the client is still here — it
                        # has not been handed the terminal frame yet — and a
                        # retry arriving straight after must find a stored
                        # result rather than a claim still being written. If
                        # this await is cancelled anyway, `settled` stays False
                        # and the detached path in `finally` picks it up.
                        await _settle_stream(
                            idempotency_claim, emitted, clean=True, request_id=request_id
                        )
                        settled = True
                    # The backend finished generating and its connection is
                    # already closed; holding its slot until the client walks
                    # away made it look busier than it was to least-loaded
                    # routing.
                    pool.release(backend_id)
                    released = True
                yield chunk
        except BaseException:
            clean = False
            raise
        finally:
            if not released:
                pool.release(backend_id)
            if idempotency_claim is not None and not settled:
                # Detached for the same reason: this branch is reached when the
                # client dropped, which is exactly when the request task is
                # already being cancelled.
                _detach(
                    _settle_stream(
                        idempotency_claim, list(emitted), clean=clean, request_id=request_id
                    ),
                    what="stream-idempotency-abandoned",
                )

    async def _stream_events() -> Any:
        prompt_tokens = 0
        completion_tokens = 0
        # RM-83: counted as the stream runs, and used only if it breaks before
        # the frame carrying the real numbers.
        streamed_tokens = 0
        # RM-88: did the model finish, or were we cut off? Set below, once.
        upstream_complete = False
        # PRM-100: the cached half of the prompt, when the backend reports it.
        cached_prompt_tokens = 0
        stream_error: Exception | None = None
        # RM-46: time-to-first-token — set the first time a chunk carries real
        # delta.content, i.e. the token a streaming client actually sees first
        # (not the empty role-only opening chunk some backends send first).
        ttft_ms: int | None = None
        # PRM-105: ttft_ms is "time to first *visible* token" and stays that way
        # — it is a latency metric with history behind it. But a reasoning model
        # streams `reasoning_content` first: qwen3-0.6b sent 30 of those before
        # a single `content` chunk, so ttft_ms appeared on 13 of 247 spans and
        # only on the long ones. A histogram built on 5% of requests, with the
        # 5% chosen by how much the model reasons, is worse than none. This one
        # answers the other question — when did the model start working — and
        # is always there. Argus picked this over redefining ttft_ms.
        first_token_ms: int | None = None
        # See the non-streaming path's identical comment — same `timings`
        # object, present on the backend's final chunk for llama.cpp-family
        # backends only.
        inter_token_ms: float | None = None
        # RM-62: same prefill-throughput extraction as the non-streaming path.
        prompt_tps: float | None = None
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
                        if line.startswith("data:") and "[DONE]" in line:
                            # RM-87: the backend ends its stream with its own
                            # `[DONE]`, and forwarding it sent two terminal
                            # frames — ours plus theirs. A client is right to
                            # stop at the first, which arrived before anything
                            # had been accounted for. We emit the terminal frame
                            # ourselves, once, at the end.
                            continue
                        if line.startswith("data:"):
                            try:
                                chunk = json.loads(line[5:].strip())
                                usage = chunk.get("usage") or {}
                                if usage:
                                    prompt_tokens = usage.get("prompt_tokens", 0)
                                    completion_tokens = usage.get("completion_tokens", 0)
                                timings = chunk.get("timings") or {}
                                if timings:
                                    inter_token_ms = timings.get("predicted_per_token_ms")
                                    # llama.cpp's streaming chunks don't carry a
                                    # `usage` field at all (confirmed live —
                                    # only the final chunk's `timings` does),
                                    # so prompt_tokens/completion_tokens would
                                    # otherwise stay 0 for every llama.cpp
                                    # stream, breaking tokens/sec below. Same
                                    # cache_n+prompt_n / predicted_n mapping
                                    # already verified client-side for RM-36's
                                    # Playground token counts.
                                    if "predicted_n" in timings and "prompt_n" in timings:
                                        # PRM-100: `cache_n` is the cached half
                                        # of the prompt, which is exactly what
                                        # the non-streaming path reports as
                                        # prompt_tokens_details.cached_tokens.
                                        # Same figure, same subset relationship,
                                        # so the row means the same thing on
                                        # both paths.
                                        cached_prompt_tokens = timings.get("cache_n", 0)
                                        prompt_tokens = cached_prompt_tokens + timings["prompt_n"]
                                        completion_tokens = timings["predicted_n"]
                                    prompt_tps = timings.get("prompt_per_second")
                                    if prompt_tps is None:
                                        prompt_ms = timings.get("prompt_ms")
                                        prompt_n = timings.get("prompt_n")
                                        if prompt_ms and prompt_n:
                                            prompt_tps = prompt_n / (prompt_ms / 1000)
                                delta = (chunk.get("choices") or [{}])[0].get("delta") or {}
                                delta_content = delta.get("content")
                                if delta_content or delta.get("reasoning_content"):
                                    # RM-83: llama.cpp reports token counts only
                                    # on the final `timings` frame, which a
                                    # broken stream never reaches — so a
                                    # half-delivered answer was billed as zero
                                    # and left no usage row at all. One content
                                    # chunk is one token here, which is the best
                                    # count available when nothing better
                                    # arrives.
                                    # RM-87: a reasoning model streams its
                                    # thinking as `reasoning_content`, not
                                    # `content`. Counting only the latter meant
                                    # a stream abandoned while the model was
                                    # still reasoning tallied zero generated
                                    # tokens and was billed nothing — though
                                    # the GPU had been running the whole time.
                                    streamed_tokens += 1
                                    # TTFT stays on visible content on purpose:
                                    # it is a latency metric with history behind
                                    # it, and redefining it would silently move
                                    # every chart that uses it.
                                    if ttft_ms is None and delta_content:
                                        ttft_ms = int((time.monotonic() - backend_start) * 1000)
                                    if first_token_ms is None:
                                        first_token_ms = int(
                                            (time.monotonic() - backend_start) * 1000
                                        )
                                # RM-77: the backend names itself here —
                                # llama.cpp echoes its own --alias, which is
                                # the instance id. The body has to name the
                                # model, like every other surface: a client
                                # attributing cost by `response.model` would
                                # otherwise bill an identifier that isn't in
                                # the catalog, split across replica names
                                # nobody recognises. The replica is already in
                                # the X-Prometheus-Instance headers.
                                if billed_name and chunk.get("model") is not None:
                                    chunk["model"] = billed_name
                                    line = "data: " + json.dumps(chunk, separators=(",", ":"))
                            except Exception:
                                # Unparseable chunk — pass it through untouched
                                # rather than dropping a token the client needs.
                                pass
                        yield f"{line}\n\n"
            # RM-88: reached only if the backend's stream ran to its end. If the
            # caller hangs up mid-answer this generator is closed at a `yield`
            # above and we never get here — which is precisely how the two are
            # told apart, with no need to catch GeneratorExit to notice.
            upstream_complete = True
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
            # RM-87: everything that records what happened runs BEFORE the
            # terminal frame is handed over, never after. A `yield` inside this
            # block suspends the generator, and a client that stops reading at
            # `[DONE]` — which is what every OpenAI-shaped SDK does — never
            # resumes it. The generator is then closed later with GeneratorExit
            # raised at that yield, which abandons the rest of this block. That
            # is how streamed generations were metered, billed and settled only
            # for clients that happened to drain the body to EOF.
            # RM-88: three outcomes, named rather than flattened to a boolean.
            # An answer the caller walked away from is not the same event as one
            # we broke, and the usage row is where that distinction has to
            # survive — it is the only artefact left when a charge is queried.
            if stream_error is not None:
                termination_reason = db.TERMINATION_UPSTREAM_ERROR
            elif upstream_complete:
                termination_reason = db.TERMINATION_COMPLETE
            else:
                termination_reason = db.TERMINATION_CLIENT_DISCONNECTED
            if completion_tokens == 0 and streamed_tokens > 0:
                # RM-83: the stream stopped before the frame that carries the
                # counts. Bill what was actually sent to the caller rather than
                # nothing — those tokens left the building — with the prompt
                # estimated the way the context check already estimates it.
                completion_tokens = streamed_tokens
                prompt_tokens = _estimate_tokens(payload.get("messages") or [])
            backend_latency_ms = int((time.monotonic() - backend_start) * 1000)
            tps = (
                (completion_tokens / (backend_latency_ms / 1000))
                if backend_latency_ms > 0 and completion_tokens > 0
                else 0.0
            )

            async def _account_for_it() -> None:
                """Everything that records what this request did.

                RM-87: detached rather than awaited here. This runs while the
                generator unwinds, which for a disconnected client happens
                inside a task the server has already cancelled — so awaiting it
                meant the work stopped at the first `await` and the request was
                never billed, metered or settled. A client that walks away
                mid-generation is precisely the one RM-83 says we must still
                charge.
                """
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
                    model_id=billed_name,
                    error=stream_error is not None,
                    ttft_ms=ttft_ms,
                    inter_token_ms=inter_token_ms,
                    tokens_per_second=round(tps, 2) if completion_tokens > 0 else None,
                    prompt_tokens_per_second=round(prompt_tps, 2)
                    if prompt_tps is not None
                    else None,
                )
                # RM-32: persisted daily usage for streaming
                await _record_usage(
                    claims,
                    billing_id or billed_name,
                    prompt_tokens,
                    completion_tokens,
                    model_slug=billed_name,
                    instance_id=backend_id,
                    termination_reason=termination_reason,
                    request_id=request_id,
                    cached_prompt_tokens=cached_prompt_tokens,
                    duration_s=backend_latency_ms / 1000,
                    # PRM-131: `first_token_ms`, not `ttft_ms`. They are our
                    # two answers to the same question and A-24 measured the
                    # gap: 63.7% of spans carry the first token of any kind,
                    # 4.4% the first *visible* one — `qwen3-8b-q6` spent 24
                    # tokens reasoning and reported zero. `ttft_ms` is
                    # deliberately the visible one and stays that way; it has
                    # charts behind it. But the metric is
                    # `gen_ai.server.time_to_first_token`, whose spec says
                    # first token, and it has no history to move. Feeding it
                    # the visible one would ship a standard metric that is
                    # empty 19 times out of 20 and looks like an outage.
                    ttft_s=(first_token_ms / 1000) if first_token_ms is not None else None,
                    engine=engine,
                )

                # RM-60: settle the spend-cap reservation with the real cost
                if (
                    claims is not None
                    and reservation is not None
                    and reservation.allowed
                    and budget_redis is not None
                ):
                    actual_cost = pricing.get_pricing_table().estimate_cost_usd(
                        # PRM-118: the settle has to resolve the same price the
                        # reserve did, or it corrects a real reservation down
                        # to nothing.
                        billing_id or billed_name,
                        prompt_tokens,
                        completion_tokens,
                        model_slug=billed_name,
                    )
                    if actual_cost is not None:
                        newly_crossed = await BudgetTracker(budget_redis).settle(
                            claims.client_id,
                            reservation.reserved_usd,
                            actual_cost,
                            cap_usd=reservation.cap_usd,
                            alert_thresholds_percent=alert_thresholds_percent or [],
                        )
                        await _dispatch_threshold_alerts(
                            request,
                            claims.client_id,
                            reservation.cap_usd,
                            reservation.total_spend_usd,
                            newly_crossed,
                        )

            # RM-95: the handler's span ended when the response object was
            # returned, long before any of this was known, so the streamed
            # answer gets its own — parented to the request's context, which is
            # captured here rather than read inside the detached task where the
            # ambient context is gone.
            if genai_request_attrs is not None:
                from opentelemetry.trace import SpanKind

                genai_span = _tracer.start_span(
                    f"{genai_request_attrs['gen_ai.operation.name']} "
                    f"{genai_request_attrs['gen_ai.request.model']}",
                    context=_request_context,
                    kind=SpanKind.CLIENT,
                )
                genai_span.set_attributes(genai_request_attrs)
                genai_span.set_attributes(
                    _genai_response_attrs(
                        response_model=billed_name,
                        input_tokens=prompt_tokens,
                        output_tokens=completion_tokens,
                        finish_reason=termination_reason,
                        backend_id=backend_id,
                        ttft_ms=ttft_ms,
                        first_token_ms=first_token_ms,
                    )
                )
                genai_span.end()

            _detach(_account_for_it(), what="stream-usage")

            # RM-87: last, once the request is fully accounted for. Whether the
            # client reads this frame or has already walked away no longer
            # changes what we recorded.
            yield "data: [DONE]\n\n"

    logger.info("llama.forwarding_stream", backend_id=backend_id, request_id=request_id)
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            **(served_by_headers or {}),
        },
    )
