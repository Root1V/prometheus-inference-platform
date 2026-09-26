---
id: "001"
title: "Gateway Core — Request Routing and llama.cpp Proxy"
status: closed
current-agent: ""
pipeline-log: []
created: 2026-03-28
updated: 2026-03-28
---

# 001 — Gateway Core: Request Routing and llama.cpp Proxy

## Problem Statement

Client applications need a single, secured entry point to access SLM inference capabilities.
The llama.cpp HTTP server runs on bare-metal and must never be directly exposed to client apps.
Without a gateway, there is no authentication, no usage tracking, and no control plane.

## Goals

- [ ] Expose an OpenAI-compatible `/v1/chat/completions` endpoint for clients
- [ ] Forward validated requests to llama.cpp (bare-metal) transparently
- [ ] Attach a unique `request_id` to every request for traceability
- [ ] Return a `/health` endpoint for liveness probes
- [ ] Return proper RFC 9457 Problem Details on all errors
- [ ] Stream responses via Server-Sent Events when `stream: true`

## Non-Goals

- Authentication and authorization (covered in `002-jwt-auth.md`)
- Rate limiting (covered in `003-rate-limiting.md`)
- Usage metering and billing (covered in `004-usage-metering.md`)
- Model management / model switching (future spec)

## Proposed Solution

A Python FastAPI application running in Docker. It acts as a reverse proxy between
clients and the llama.cpp HTTP server. The gateway applies middleware in order and
forwards the request, streaming the response back.

### Request Flow

```
Client → [request-id middleware] → [router] → [llama-proxy] → llama.cpp
                                                     ↑
                                              sanitise payload
                                              validate content-type
```

### Key Design Decisions

| Decision | Rationale |
|----------|-----------|
| FastAPI + httpx async client | Native async, streaming support, OpenAPI docs auto-generated |
| Preserve OpenAI API shape | Client apps can use the openai-python SDK without changes |
| `host.docker.internal` for llama.cpp | Standard Docker-to-host networking on Linux/Mac |
| Bind gateway to `0.0.0.0:8000` | Must be reachable from Docker network |
| Bind llama.cpp to `127.0.0.1` | llama.cpp must NEVER be exposed beyond localhost |

## API Contract

> `gateway/api/001-gateway-core.yaml` (to be generated with `/generate-openapi`)

Endpoints:
- `POST /v1/chat/completions` — proxied to llama.cpp
- `GET /health` — liveness check (unauthenticated)
- `GET /v1/models` — list available models (unauthenticated for discovery)

## Data Model

No persistence in this spec. State is per-request only.

```python
@dataclass
class ForwardedRequest:
    request_id: str       # UUID4, injected by middleware
    model: str            # from request body
    messages: list[dict]  # validated, sanitised
    stream: bool          # from request body, default False
    max_tokens: int | None
    temperature: float | None
```

## Security Considerations

- Sanitise `messages` to prevent prompt injection (strip `role: system` overrides from user input).
- Validate `model` against `runtime/models/registry.yaml` — reject unknown models.
- Do not forward unknown or extra fields from the client request to llama.cpp (allowlist approach).
- llama.cpp URL must come from environment variable `LLAMA_CPP_URL`, never from client input.
- `/health` is the only unauthenticated endpoint (auth comes in spec 002).

## Acceptance Criteria

- [x] **AC-1**: Given a valid `POST /v1/chat/completions` request, when forwarded to llama.cpp, then the response is returned with the same structure (non-streaming).
- [x] **AC-2**: Given `stream: true` in the request, when llama.cpp responds with SSE chunks, then the gateway streams them to the client and closes with `data: [DONE]`.
- [x] **AC-3**: Given any request, when processed, then the response always includes an `X-Request-ID` header with a unique UUID.
- [x] **AC-4**: Given a `GET /health` request, when the gateway is running, then it returns `{"status": "ok"}` with HTTP 200.
- [x] **AC-5**: Given an unknown `model` value in the request body, when validated against the registry, then the gateway returns HTTP 400 with a `ProblemDetail` response.
- [x] **AC-6**: Given a request with a `role: system` message injected inside the `messages` array from user-controlled data, when sanitised, then the injected system message is stripped before forwarding.
- [x] **AC-7**: Given llama.cpp is unreachable, when the proxy attempts to forward, then the gateway returns HTTP 503 with a `ProblemDetail` response (not a raw connection error).

## Open Questions

- [x] Q1: `/v1/models` serves from `registry.yaml` (not proxied from llama.cpp — llama.cpp loads one model at a time).
- [x] Q2: Validate `max_tokens` against `context_length` from registry — return HTTP 400 if exceeded.

## References

- llama.cpp HTTP server API: https://github.com/ggerganov/llama.cpp/blob/master/examples/server/README.md
- OpenAI API reference: https://platform.openai.com/docs/api-reference/chat
- RFC 9457 Problem Details: https://www.rfc-editor.org/rfc/rfc9457
