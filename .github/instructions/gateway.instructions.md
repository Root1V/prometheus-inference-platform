---
description: "Use when building the Prometheus API Gateway: middleware, routing, request forwarding to llama.cpp, rate limiting, consumption metering, SSE streaming, or any gateway-layer code."
applyTo: "gateway/**"
---

# Gateway — Development Guidelines

## Responsibility

The Gateway is the **only** component authorised to call llama.cpp.
Its responsibilities in order:
1. Authenticate the caller (JWT validation)
2. Authorise the request (check scopes/permissions)
3. Apply rate limits (per user + per client)
4. Forward the request to llama.cpp
5. Stream or buffer the response
6. Record consumption (tokens in/out, latency, model)

## Middleware Stack Order (must be preserved)

```
request → [tracing/request-id] → [auth] → [authz] → [rate-limit] → [quota-check] → [router] → [llama-proxy] → [metering] → response
```

Never skip or reorder these layers.

## Forwarding to llama.cpp

```python
# Always use the internal network URL from config, never hardcode
LLAMA_BASE_URL = settings.llama_cpp_url  # e.g. http://localhost:8080

# Validate and sanitise the payload before forwarding
# Strip any system-role message injected by the caller
def sanitise_messages(messages: list[dict]) -> list[dict]:
    # See memory/specs/002-prompt-injection-defense.md
    ...
```

## API Response Format

All errors follow RFC 9457 Problem Details:
```json
{
  "type": "https://prometheus.internal/errors/rate-limit-exceeded",
  "title": "Rate Limit Exceeded",
  "status": 429,
  "detail": "You have exceeded 100 requests per minute.",
  "instance": "/v1/chat/completions",
  "request_id": "uuid-here"
}
```

## Streaming (SSE)

- Use Server-Sent Events for streaming completions (matches OpenAI API convention).
- Flush each chunk immediately — do not buffer.
- Close the SSE stream with `data: [DONE]`.
- Meter tokens from the `usage` field in the final chunk.

## Consumption Metering

Every inference call MUST emit a metering record:
```python
@dataclass
class MeteringRecord:
    request_id: str
    user_id: str
    client_id: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    latency_ms: int
    status: str  # "success" | "error" | "rate_limited"
    timestamp: datetime
```

Write to the metering store **after** the response is sent — never block the response.

## Environment Variables

| Variable | Description |
|----------|-------------|
| `LLAMA_CPP_URL` | llama.cpp HTTP server base URL |
| `JWT_PUBLIC_KEY` | RS256 public key for token verification |
| `JWT_ISSUER` | Expected issuer claim |
| `RATE_LIMIT_RPM` | Requests per minute per user |
| `RATE_LIMIT_TPM` | Tokens per minute per user |
| `METERING_BACKEND` | `postgres` \| `redis` \| `file` |

## OpenAPI Contract

Gateway API contracts live in `gateway/api/`. Every public endpoint must have:
- A path entry in the OpenAPI YAML
- Security scheme reference (`BearerAuth`)
- Response schemas for 200, 400, 401, 403, 429, 500, 503
