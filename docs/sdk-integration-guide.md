# Prometheus Gateway — SDK Integration Guide

**Revision**: 2026-09-18a · `10bf7fc`
<!-- Consumers vendor this file and diff it. The date and commit above are what to quote
     when asking whether a copy is current; they change whenever this document does. -->

Technical reference for the team building client SDKs (Python, Go, Rust) that wrap this
platform's inference API. It covers everything an SDK needs to encapsulate: authentication
and token refresh, TLS, the request/response contract for every client-facing endpoint, the
full error catalog, and the resilience behavior (retries, rate limits, circuit breaker) the
gateway already implements server-side — so the SDK complements it instead of fighting it.

Every fact below is sourced directly from the gateway/auth-service source and test suite, not
from documentation that could have drifted. File:line references point at the current
codebase for verification.

**Model names in the examples are real but not guaranteed.** They are taken from a live
deployment so the examples can be run as written, rather than failing with `unknown-model` the
first time somebody pastes one — which is what happened with the placeholder that used to be
here. A catalog still differs between deployments and changes over time: **`GET /v1/models` is
the source of truth**, and an SDK should never hardcode a model name it did not read from
there. Two things need confirming with the platform operator before
publishing an SDK against a specific deployment — flagged in §9.

---

## 1. Architecture at a glance

**The SDK needs one host: the gateway.** Everything a client calls — token issuance included
— is reachable there.

Behind it there are two services, and this matters for reading error messages and timeouts,
not for configuration:

- **gateway** — the inference API (`/v1/...`), and the token endpoint (`POST /oauth2/token`),
  which it forwards to the auth-service. Tokens are issued by the auth-service in both cases;
  the gateway never signs one itself.
- **auth-service** — issues the OAuth2 access tokens (JWT, RS256). **An SDK should not call it
  directly.** It is an internal service, and a deployment is free to keep it off any network
  the SDK can reach.

Inference endpoints live under the `/v1/` prefix; `/oauth2/token` deliberately does not, to
keep the path identical to the auth-service's own. There is no other API versioning mechanism
(no version header) — path prefix is the only version signal.

**Base URL**: not hardcoded anywhere in this codebase — confirm the real host/port for your
target deployment with the platform operator (see §9). Examples in this guide use
`https://gateway.example.internal` as a placeholder.

---

## 2. Authentication

### 2.1 Obtaining a token — `POST /oauth2/token`

**Call this on the gateway**, at the same path you would have used on the auth-service. The
gateway forwards the request verbatim and returns the response verbatim, error bodies
included — so an SDK that already talked to the auth-service changes only the host.

Only two grant types are supported: `client_credentials` (what an SDK/service
integration should use) and `password` (for human/email-login accounts — not relevant to an
SDK). This guide covers `client_credentials` only.

**The request must be form-encoded** (`Content-Type: application/x-www-form-urlencoded`), not
JSON — the endpoint is declared with FastAPI `Form(...)` params.

```
POST /oauth2/token
Content-Type: application/x-www-form-urlencoded

grant_type=client_credentials&client_id=<id>&client_secret=<secret>&scope=inference:read model:qwen3-0.6b
```

- `grant_type` — required, must be exactly `client_credentials`.
- `client_id`, `client_secret` — required, issued by the platform operator when your account
  is registered.
- `scope` — **optional**. Space-separated. If omitted, the token gets the account's full
  `allowed_scopes`. If supplied, the effective scope is the intersection of what you asked for
  and what your account is actually allowed — asking for a scope you don't have is a `400`,
  not a silent downgrade (see below). Requesting a narrower scope than your full grant (e.g.
  only `inference:read` + one specific `model:<id>`) is a reasonable way for an SDK to issue
  narrowly-scoped tokens for a specific purpose.

**Success response** (`200`):

```json
{
  "access_token": "<RS256 JWT>",
  "token_type": "bearer",
  "expires_in": 600,
  "scope": "inference:read model:qwen3-0.6b"
}
```

`scope` in the response is the actual effective (sorted) scope granted — always read this back
rather than assuming what you asked for was granted verbatim.

**Error response** (`400`/`401`), RFC 6749 §5.2 shape — note this is a *different* envelope
from the gateway's own error format described in §5:

```json
{ "error": "invalid_client", "error_description": "Invalid client credentials." }
```

Possible `error` values: `unsupported_grant_type` (400), `invalid_scope` (400 — either an
unrecognized scope string, or one your account isn't allowed to request), `invalid_client`
(401 — bad client_id/secret), `unauthorized_client` (401 — account deactivated).

**One exception to the verbatim rule**: if the gateway cannot reach the auth-service at all,
the failure is the gateway's, not an OAuth2 outcome, so it answers `503` in the gateway's own
problem+json envelope (§5) with `type` ending in `/upstream-unavailable` — or
`/not-configured` if that deployment has no token endpoint wired up. Parse `4xx` as OAuth2 and
`503` as a gateway problem document; a `503` is retryable, an `invalid_client` never is.

### 2.2 Using the token

Every gateway request needs:

```
Authorization: Bearer <access_token>
```

**Never pass the token as a query parameter** (`?token=...`) — the gateway explicitly rejects
that with `401 missing-credentials`, specifically to keep tokens out of server logs/proxy
logs/browser history. The header is the only accepted transport.

### 2.3 JWT structure (for introspection, not verification — the SDK never needs to verify
the signature itself)

RS256, these claims are minted:

```json
{
  "iss": "<auth-service issuer URL>",
  "sub": "<client_id>",
  "azp": "<client_id>",
  "aud": "prometheus-gateway",
  "iat": 1700000000,
  "exp": 1700000600,
  "jti": "<uuid4>",
  "scope": "inference:read model:qwen3-0.6b",
  "role": "app",
  "client_name": "my-integration"
}
```

`role` (`admin` | `cognitive` | `agent` | `app`) and `client_name` are minted into the token
and can be read by base64-decoding the JWT payload client-side (no signature check needed for
introspection purposes) — useful if the SDK wants to expose "what role/name is this token"
without a separate API call. **The gateway itself does not use `role` for authorization** —
only the `scope` claim matters for what a token can actually do.

### 2.4 Token TTL and refresh strategy

Default TTLs by role — **but these are only defaults**, an admin can override the TTL for any
individual account (bounded 60s–86400s), so **the SDK must not hardcode these and must always
read `expires_in` from the token response**:

| Role | Default TTL |
|---|---|
| `admin` | 10800s (3h) |
| `cognitive` | 3600s (1h) |
| `agent` | 600s (10min) |
| `app` | 300s (5min) |

**There is no refresh-token grant.** The only way to get a new token is to call
`POST /oauth2/token` again with `client_credentials`. The SDK should implement a
refresh-ahead pattern:

1. **The token response has no `issued_at` field, and the token itself isn't decoded just to
   read `iat`/`exp` for this** — don't anchor the expiry calculation to the SDK's own
   wall-clock reading at the moment it sent the request, since that's vulnerable to clock
   drift between the SDK's host and the auth-service's host. Instead, **use the HTTP `Date`
   response header** (confirmed present on every auth-service response — standard HTTP/1.1
   server behavior via uvicorn, not something the application explicitly sets, so it's
   reliably there) as the server-authoritative "now" reference: `expires_at = Date_header_value
   + expires_in`. This still doesn't eliminate clock skew *during* the single request
   round-trip, but that window is far smaller and bounded by network latency, not by however
   out-of-sync the two machines' clocks happen to be.
2. Before each request (or on a background timer), check if the token is within some buffer
   of expiry — e.g. 80% of its `expires_in` has elapsed, or fewer than 30 seconds remain,
   whichever is more conservative for short-TTL `app`-role tokens.
3. If within the buffer, request a fresh token *before* making the outbound call, not after a
   `401 token-expired` comes back — this avoids a failed-then-retried request pattern.
4. Guard the refresh with a mutex/lock so concurrent requests from multiple threads/goroutines
   don't all trigger simultaneous refreshes.
5. Still handle `401 token-expired` reactively as a fallback (refresh once, retry the request
   once) in case the proactive refresh was skipped or the clock drifted — but this should be
   the rare path, not the primary mechanism.

### 2.5 Scopes

Fixed scope strings: `inference:read`, `inference:stream`, `admin:read`, `admin:write`,
`admin:models`, `admin:usage`, `backend-registry:read`, `backend-registry:write`, `ui:chat`.

Per-model scope: `model:<model-id>` — e.g. `model:qwen3-0.6b`. Case-sensitive, must match
the model ID exactly.

**Deny-by-default, and this is the part SDK authors most often get wrong**: holding
`inference:read` (or `inference:stream`) grants **no model access by itself**. Every inference
call also needs the specific `model:<id>` scope for the model being called. A token with
`inference:read` but zero `model:*` scopes can authenticate successfully but will get `403` on
every actual inference call.

**Streaming needs a different scope than non-streaming**: `stream: true` requires
`inference:stream`; `stream: false` (or omitted) requires `inference:read`. Holding one does
not imply the other — an SDK that supports both streaming and non-streaming should surface
this clearly if a `403` comes back for one but not the other (a genuinely common integration
mistake, worth a specific error message in the SDK rather than a generic "forbidden").

`GET /v1/models/mine` (see §3.1) lets a token discover exactly which models it currently has
`model:<id>` scope for — useful for an SDK to call at startup and cache, rather than
discovering access model-by-model via failed requests.

### 2.6 TLS / certificates

- TLS termination at both services is optional and controlled by cert/key file pairs
  (`AUTH_TLS_CERT_FILE`/`KEY_FILE`, `GATEWAY_TLS_CERT_FILE`/`KEY_FILE` on the server side) — a
  given deployment may run plain HTTP (common for a private-network dev/staging setup) or HTTPS
  (expected for anything internet-facing).
- **There is no client-certificate/mTLS requirement anywhere in this platform.** Authentication
  is purely the Bearer JWT described above. Do not build mTLS support into the SDK unless a
  specific deployment asks for it as a separate reverse-proxy-level concern.
- For a deployment using a **self-signed dev certificate** (the repo's own dev-cert generation
  scripts explicitly warn these are never for production use), the SDK's HTTP client needs to
  trust that certificate explicitly — this is standard TLS client configuration, not a
  platform-specific mechanism:
  - Python: `httpx.Client(verify="/path/to/dev.crt")` or add it to a custom `ssl.SSLContext`.
  - Go: build a `x509.CertPool`, add the cert, set it as `RootCAs` on a custom
    `http.Transport`.
  - Rust: `reqwest::Certificate::from_pem(...)` added to the `ClientBuilder`.
- For a **production deployment**, expect a real CA-signed certificate — no special client
  configuration should be needed beyond the OS/language runtime's default trust store.
- Do not confuse this with `REQUESTS_CA_BUNDLE`/`SSL_CERT_FILE` — those are **server-side**
  environment variables that configure the gateway/auth-service's own *outbound* HTTP clients
  (e.g. behind a corporate TLS-inspecting proxy). They have nothing to do with how an external
  SDK client trusts the gateway's own certificate.

---

## 3. Core API endpoints

All request bodies are **allowlist schemas** — fields not explicitly documented below are
silently dropped and never forwarded to the backend model. Don't rely on passing through
provider-specific extras (e.g. `seed`, `presence_penalty`, `logit_bias`) — they won't reach the
model.

Response bodies for the three inference endpoints (chat/embeddings/images) are **passed
through from the backend verbatim** — the gateway does not reshape or validate them beyond
what's documented here. Treat fields not explicitly guaranteed by this doc (e.g. `id`,
`created`, `system_fingerprint`) as backend-dependent/optional, not a guaranteed contract.

### 3.1 `GET /v1/models` — public catalog

No authentication required. Returns every currently-deployed model:

```json
{
  "object": "list",
  "data": [
    {
      "id": "qwen3-0.6b",
      "object": "model",
      "owned_by": "prometheus",
      "context_length": 4096,
      "family": "qwen3",
      "quantization": "IQ4_NL",
      "modality": "text"
    }
  ]
}
```

`modality` is one of `text`, `vision`, `embedding`, `image` — matters for which endpoint a
given model can be called on (see the modality-mismatch error in §5.2).

### 3.2 `GET /v1/models/mine` — what *this token* can actually call

Requires a valid Bearer token (`401` if missing). Same item shape as above, but filtered to
only the models this token has `model:<id>` scope for. A token with `admin:write` sees the
full catalog regardless of individual model grants (an internal-tooling carve-out, not
something to expect for a normal client SDK integration). A token with `inference:read` but no
`model:*` grants gets an empty `data` array, not an error.

Recommended SDK pattern: call this once at client initialization (or on-demand), cache the
result, and use it to give a clear client-side error ("this token has no access to model X")
instead of always waiting for a `403` from the actual inference call.

### 3.3 `POST /v1/chat/completions`

**Request** (OpenAI-compatible subset):

```json
{
  "model": "qwen3-0.6b",
  "messages": [
    { "role": "user", "content": "Hello" }
  ],
  "stream": false,
  "max_tokens": 256,
  "temperature": 0.7,
  "top_p": 0.9,
  "stop": ["\n\n"],
  "tools": [
    {
      "type": "function",
      "function": {
        "name": "get_weather",
        "description": "Get the current weather for a city",
        "parameters": { "type": "object", "properties": { "city": { "type": "string" } }, "required": ["city"] }
      }
    }
  ],
  "tool_choice": "auto"
}
```

Fields: `model`, `messages` required; `stream` (default `false`); `max_tokens` (>0 if set);
`temperature` (0.0–2.0); `top_p` (0.0 < p ≤ 1.0); `stop` (string or list); `tools` /
`tool_choice` / `response_format` (forwarded as-is, no gateway-side validation of their
schemas).

**`response_format` — structured outputs (PRM-126).** Now supported, and constrained by the
engine rather than by prompting:

```json
{
  "model": "qwen3-0.6b",
  "messages": [{ "role": "user", "content": "Give me the capital of Peru" }],
  "response_format": {
    "type": "json_schema",
    "json_schema": {
      "name": "capital",
      "schema": {
        "type": "object",
        "properties": { "capital": { "type": "string" } },
        "required": ["capital"],
        "additionalProperties": false
      }
    }
  }
}
```

`{"type": "json_object"}` and `{"type": "text"}` work too. The content comes back as a JSON
**string** in `choices[0].message.content` — parse it; it is not a nested object.

**Anything else is a `400 unknown-parameter`, not a shrug.** This endpoint takes an
OpenAI-compatible *subset*, and until PRM-126 a field outside it was discarded in silence —
this guide said so in as many words, which made it a decision rather than an oversight, and the
decision was wrong: a client sending `response_format` got unconstrained prose and no way to
find out why. Unrecognised fields are now refused by name, the way OpenAI's own API refuses
them. Still outside the subset: `n`, `presence_penalty`, `frequency_penalty`, `logit_bias`,
`user`, `seed` — sending any of them is now an error you can see rather than a setting that
quietly did nothing.

`messages[].role` must be one of `system`, `user`, `assistant`, `tool`. `content` can be a
plain string, `null` (e.g. an assistant message that only carries `tool_calls`), or a list of
content parts for vision models:

```json
{
  "role": "user",
  "content": [
    { "type": "text", "text": "What's in this image?" },
    { "type": "image_url", "image_url": { "url": "data:image/png;base64,iVBORw0KG..." } }
  ]
}
```

**Important**: `image_url.url` must be a `data:` base64 URI. Remote `http(s)://` image URLs
are rejected outright (an explicit SSRF mitigation) — the SDK must download/encode images
client-side before sending, it cannot pass a link and let the gateway fetch it. Sending an
image content part to a model whose `modality` isn't `vision` returns `400 modality-mismatch`.

**Non-streaming response** — passed through from the backend:

```json
{
  "id": "chatcmpl-...",
  "object": "chat.completion",
  "model": "qwen3-0.6b",
  "choices": [
    {
      "index": 0,
      "message": { "role": "assistant", "content": "Hello! How can I help?" },
      "finish_reason": "stop"
    }
  ],
  "usage": { "prompt_tokens": 5, "completion_tokens": 9, "total_tokens": 14 }
}
```

Tool-call responses have `message.content: null` with `message.tool_calls` populated and
`finish_reason: "tool_calls"` — passed through untouched, no gateway-side interpretation.

llama.cpp-family backends also include a `timings` object alongside `usage` — **backend-
dependent, not guaranteed for every model** (MLX/vLLM/SGLang backends don't include it):

```json
"timings": {
  "prompt_n": 13, "prompt_ms": 34.25, "prompt_per_second": 379.5,
  "predicted_n": 20, "predicted_ms": 50.3, "predicted_per_second": 397.6,
  "predicted_per_token_ms": 2.52
}
```

**Streaming response** (`stream: true`) — `Content-Type: text/event-stream`. Each line is
forwarded from the backend essentially verbatim:

```
data: {"choices": [{"delta": {"content": "Hello"}}]}

data: {"choices": [{"delta": {"content": "!"}}]}

data: [DONE]

```

Notes an SDK must handle correctly:

- **The gateway always appends its own `data: [DONE]` at stream end**, regardless of whether
  the backend sent one — reliably treat `[DONE]` as the terminal sentinel.
- **No dedicated `usage` chunk for llama.cpp-family backends.** Token counts arrive only in
  the *final* chunk's `timings` object (`prompt_n` + `cache_n` → prompt tokens, `predicted_n`
  → completion tokens). Check `usage` defensively on every chunk (other backend types may
  include it), but don't build the SDK's token-accounting around expecting a `usage`-bearing
  chunk from llama.cpp models.
- **Mid-stream failures don't produce an HTTP error status** — by the time a backend fails
  mid-generation, the `200`/`text/event-stream` headers are already committed. Instead, the
  gateway emits an in-band error chunk followed by `data: [DONE]`, then closes the connection.
  **The SDK must parse this in-band shape and cannot rely on HTTP status alone to detect a
  failed stream.** As of this codebase version there is exactly one code path that emits an
  in-band error, and it always sends the literal `data: {"error": "stream interrupted"}` —
  there is no other in-band error message anywhere in the streaming code, and no evidence
  `error`'s value is ever anything other than that fixed string. That said, **this single
  fixed string is not a documented stable contract, just what the current implementation
  happens to send** — parse by checking for the presence of a top-level `error` key on any
  chunk (treat it as "the stream failed," full stop) rather than matching the specific string
  value, so the SDK doesn't silently stop detecting failures if the message text ever changes.
- Streaming requests are **never retried internally by the gateway** (response headers are
  already sent) — any retry-on-failure for a stream is entirely the SDK's responsibility, and
  since generation isn't idempotent, retrying a partially-completed stream means the client
  may be billed for/have consumed tokens from the failed attempt too.

### 3.4 `POST /v1/embeddings`

```json
{ "model": "embed-model", "input": ["first text", "second text"] }
```

`input` accepts a single string or a list of strings. **Not supported**: `dimensions`,
`encoding_format`.

```json
{
  "object": "list",
  "data": [{ "object": "embedding", "index": 0, "embedding": [0.1, 0.2, 0.3] }],
  "model": "embed-model",
  "usage": { "prompt_tokens": 2, "total_tokens": 2 }
}
```

No `completion_tokens` in embedding usage — there's no generation phase.

### 3.5 `POST /v1/images/generations`

```json
{ "model": "sd-turbo", "prompt": "a photo of a cat", "n": 1, "size": "512x512" }
```

Only `model`, `prompt`, `n`, `size` are forwarded — `cfg_scale`, `steps`, `negative_prompt`,
etc. are **not** supported by the gateway's schema even if the underlying backend accepts them.

```json
{
  "created": 1700000000,
  "data": [{ "b64_json": "iVBORw0KGgo..." }],
  "output_format": "png"
}
```

**Images come back as base64 (`b64_json`), not a URL.** The SDK is responsible for decoding
and persisting the image if the caller wants a file.

### 3.6 `POST /v1/rerank` — score documents against a query

For **rerank-capable models only** (`modality: "rerank"` in `GET /v1/models`). A reranker is a
cross-encoder: it scores a query against each document and returns them ordered. It does not
generate text, and a rerank model returns `400 modality-mismatch` from `/v1/chat/completions`.

```
POST /v1/rerank
Content-Type: application/json

{
  "model": "qwen3-reranker",
  "query": "how do I reorder search results by relevance?",
  "documents": [
    "A reranker is a cross-encoder that scores each query-document pair.",
    "Lima is the capital of Peru.",
    "Use a reranker after retrieval to reorder the top-k candidates."
  ],
  "top_n": 3
}
```

- `query`, `documents` — required. `documents` must be non-empty (`400 validation-error`).
- `top_n` — optional; omit to get every document back.

**Response** (`200`) — real values from a live deployment:

```json
{
  "model": "qwen3-reranker",
  "object": "list",
  "usage": { "prompt_tokens": 368, "total_tokens": 368 },
  "results": [
    { "index": 2, "relevance_score": 0.9918406009674072 },
    { "index": 0, "relevance_score": 0.03621646389365196 },
    { "index": 1, "relevance_score": 0.00006435815885197371 }
  ]
}
```

- **`results` is ordered by score, best first.** `index` refers to the position in *your*
  `documents` array, so a reordered result stays attributable to the input.
- `relevance_score` is a probability in `[0, 1]`, computed by the engine. **You do not need
  `logprobs` to obtain it** — an earlier integration reconstructed it from
  `P(yes)/(P(yes)+P(no))` because this endpoint did not exist yet.
- `usage.total_tokens` equals `prompt_tokens`: a reranker generates nothing, so there are no
  completion tokens. Billing is prompt-only.

**Three things worth knowing if you are migrating from a chat-based workaround:**

1. **No prompt template to prefill.** The engine applies the reranker's own template. Sending an
   assistant message with `<think>\n\n</think>` was a workaround for the chat endpoint and is
   neither needed nor honoured here.
2. **The whole document set is one request**, not one per document. This matters against the
   rate limit: scoring 50 candidates used to cost 50 of your 60 RPM budget, and now costs 1.
3. **`logprobs` and `top_logprobs` are not accepted** by this gateway on any endpoint. They were
   never silently dropped in a way that changed an answer — the request schema is an allowlist,
   so unknown fields simply do not reach the backend.

### 3.7 Headers

**Required**: `Authorization: Bearer <token>` on every endpoint except `GET /v1/models`.
`Content-Type: application/json` on every POST.

**Optional request headers**:

```
Idempotency-Key         — makes a retry a replay instead of a second generation
X-Prometheus-Instance   — pin the request to one replica of the model
```

**`Idempotency-Key`** is the header §6.2 tells you to weigh a retry against, and it is what
makes retrying a generation safe at all. Shape follows OpenAI and Anthropic — an opaque key you
generate, a 24h window, the stored result replayed — so an SDK written against either works here
unchanged. Max 255 characters.

- A replay is **not billed and does not use the model**; it carries `Idempotent-Replay: true`
  and `X-Idempotent-Replay-Of`, the `x-request-id` of the generation that *was* billed. Use that
  id with §3.8 — a replay's own id has no usage row, correctly.
- The key is scoped to your client and **fingerprinted against the path and payload**, so
  reusing one with different parameters is refused rather than answered with the earlier
  result. Reusing it for a different endpoint is the same refusal.
- Streaming works: the SSE body is stored and replayed.
- The four ways a key can be refused are four distinct `type` suffixes, in §5.2. Only one of
  them resolves by waiting, so branch on the suffix — never on the message.

**`X-Prometheus-Instance`** pins a request to one replica, by instance id or by its per-model
label (`#2`). It never silently falls back to another replica — the reason to pin is to reach
*that* one — so naming an instance that does not serve the model is a `400 unknown-instance`
rather than a quiet reassignment. Responses carry `X-Prometheus-Instance` and
`X-Prometheus-Instance-Id` saying which replica actually answered.

**Response headers worth reading** (all endpoints except `/health`, `/metrics`, `/v1/models`,
`/v1/backends`, `/v1/usage`):

```
X-Request-ID                       — fresh UUID per request, generated server-side
X-Trace-ID                         — for log correlation; see the exact adoption rule below
X-RateLimit-Limit-Requests
X-RateLimit-Remaining-Requests
X-RateLimit-Reset-Requests         — unix timestamp of the next window
X-RateLimit-Limit-Tokens
X-RateLimit-Remaining-Tokens
X-RateLimit-Reset-Tokens
X-Prometheus-Instance              — which replica answered (its per-model label, e.g. "#2")
X-Prometheus-Instance-Id           — the same replica's instance id
Idempotent-Replay                  — "true" only on a replayed response
X-Idempotent-Replay-Of             — on a replay: the request id that was actually billed
```

**`X-Trace-ID` adoption rule, confirmed precisely (two deployment modes exist)**:
- In deployments with a real tracing backend configured ("OTEL mode"), a client-supplied
  `X-Trace-ID` is **never read at all** — the gateway always starts a fresh trace and returns
  its own ID, specifically to prevent a client from forging trace context.
- In deployments without a tracing backend configured ("legacy mode"), a client-supplied
  `X-Trace-ID` **is** adopted, but only if it's a syntactically valid UUID4 — anything else is
  replaced with a freshly generated one.
- **`traceparent` (the W3C standard trace-context header) is never read by this platform in
  either mode** — confirmed by reading the middleware directly, not inferred. Do not send it
  expecting propagation; if the SDK wants to correlate its own internal tracing with this
  platform's logs, use the returned `X-Trace-ID` from the response, not a `traceparent` you
  send on the request.

**No API-version header exists.** `/v1/` in the path is the only version signal.

---

### 3.8 `GET /v1/usage/{request_id}` — what one of your own requests was charged

Requires any authenticated token; **no admin scope**. Reads exactly one row, the caller's own.

Every inference response carries `x-request-id`. That id is what this takes:

```json
{
  "request_id": "a0f3ec1b-25e5-4025-abba-73bec9c8b390",
  "model": "qwen3-0.6b",
  "request_kind": "chat",
  "usage": {
    "prompt_tokens": 10,
    "completion_tokens": 8,
    "total_tokens": 18,
    "prompt_tokens_details": { "cached_tokens": 9 }
  },
  "image_count": 0,
  "interrupted": false,
  "termination_reason": "complete",
  "cost_usd": null,
  "instance_id": "qwen3-0-6b-iq4-nl-local-1",
  "created_at": "2026-09-14T19:33:54.580624"
}
```

The `usage` object mirrors the inference response field for field, including
`prompt_tokens_details.cached_tokens` — a **subset** of `prompt_tokens`, not a separate bucket.
That is deliberate: an aggregate could not be reconciled against what you received once caching
is involved, and reconciling is what this endpoint is for.

`termination_reason` is one of `complete`, `upstream_error` or `client_disconnected`, and
`interrupted` is derived from it — true for anything that is not `complete`. A request that was
billed for a half-delivered answer says so here.

**404 covers both "no such request" and "not yours."** They are deliberately indistinguishable: a
`403` would confirm that an id exists, which is what a probe wants to learn.

**A replay has its own id and no row of its own**, because replaying does not use the model and is
not billed. Looking up a replay's `x-request-id` therefore returns `404`, correctly. The replay
response carries `X-Idempotent-Replay-Of` naming the generation that *was* billed — use that id
here.

---

### 3.9 `GET /v1/usage/export` — the CSV, and how its columns change

Requires `admin:read`. Not something an SDK calls; documented because consumers parse the file
and had no written contract for its shape.

Columns, in order:

```
generated_at, period_start, period_end, client_id, recorded_at, model_id, request_kind,
prompt_tokens, completion_tokens, image_count, prompt_price_per_1m, completion_price_per_1m,
image_price_each, cost_usd, interrupted, termination_reason, request_id, cached_prompt_tokens,
model_slug
```

**New columns are appended at the end. Existing columns never move and never change meaning.**
That is a commitment, not a description of the current file: a consumer reading by position keeps
working when the file grows, and one reading by name gains whatever was added. It is the rule we
have followed each time — `interrupted`, then `termination_reason`, then `request_id` and
`cached_prompt_tokens`, now `model_slug` — and it lived only in correspondence until it was
written here.

**`model_id` changed meaning, and this is the one time we are telling you that instead of
appending.** It is now the model's **catalog id**, which never changes. It used to be the
model's public name — the same string you send as `model` — and that name can be set by an
operator, so naming a model split its billing history into two groups under two names and made
it stop matching its configured price. `model_slug` is the new column: the public name the model
answered to when that row was written.

What this means for you:

- **Group by `model_id`** for anything that has to add up across time. It is stable.
- **Show `model_slug`** to a person: it is what they sent, and what the model was called then.
- Rows written before this change have the old public name in **both** columns, which is exactly
  what it was called at the time — so grouping by `model_id` reunites a history that a rename had
  split only if the rename happened after this release. We did not rewrite old rows.

One row per `usage_events` row, not pre-aggregated, so a rate change mid-period is visible per
request. The last row is a `TOTAL` reconciliation line: columns that genuinely sum do
(`prompt_tokens`, `completion_tokens`, `image_count`, `cached_prompt_tokens`, `cost_usd`), and
columns that describe a single request are left blank rather than given an invented total
(`interrupted`, `termination_reason`, `request_id`, `model_slug` — a total spans whatever names
the model went by).

`cached_prompt_tokens` is a **subset** of `prompt_tokens`, matching
`prompt_tokens_details.cached_tokens` in the inference response — adding the two would double
count.

---

## 4. Timeouts — recommended SDK client defaults

The gateway's own backend-forwarding timeout is **600 seconds** for non-streaming requests
(chat/embeddings/images alike) — deliberately long because some image-generation backends
legitimately take 2–8 minutes per request. **A client-side timeout shorter than this, combined
with a client-side retry, is a known failure mode**: the backend keeps computing after the
gateway/SDK gives up waiting, and a naive retry just queues a second expensive generation on
top of the first one that's still running. Recommendation: set the SDK's own non-streaming
read timeout to **at least 600s** (ideally configurable per-call, since a chat completion
rarely needs that long but an image generation might), and think carefully before
auto-retrying on a client-side timeout at all.

Streaming requests use a **120-second** read timeout on the gateway's own connection to the
backend (applied uniformly to connect/read/write phases) — set the SDK's SSE read timeout
comfortably above 120s so the SDK doesn't time out before the gateway would.

---

## 5. Error handling

### 5.1 Envelope shape

Every gateway-generated error (as opposed to the OAuth2 token endpoint's own RFC 6749 shape
from §2.1) is RFC 9457 "Problem Details", `Content-Type: application/problem+json`:

```json
{
  "type": "https://prometheus.internal/errors/forbidden",
  "title": "Forbidden",
  "status": 403,
  "detail": "This client is not authorized to use model 'qwen3-0.6b'.",
  "instance": "/v1/chat/completions",
  "request_id": "5c1e2b3a-...",
  "trace_id": "b04044d6-..."
}
```

Parse `type`'s last path segment as the machine-readable error code (e.g. `forbidden`,
`unknown-model`, `spend-cap-exceeded`) — that's the stable field to branch logic on, not
`detail` (human-readable, may change wording). Rate-limit errors from the rate-limiting
middleware specifically use a slightly different envelope: no `trace_id`, plus an optional
`retry_after` (int seconds) field alongside the standard fields.

**`Retry-After` header vs. body `retry_after`, confirmed — they cannot disagree.** Both are
written from the exact same computed value inside the same function call that builds the
response; there is no code path where they're set independently. The SDK doesn't need a
header-vs-body precedence rule at all — read either one, they're guaranteed identical. (This
is specific to the rate-limiting middleware's error envelope; see the next section for the
circuit-breaker's `503 backend-unavailable`, which only ever sets the header, never a body
field — a different response builder entirely.)

**422 — request body validation, fixed (RM-65)**: an earlier version of the gateway let
FastAPI's default validation-error handler run for a malformed/incomplete request body,
which produced `Content-Type: application/json` and `{"detail": [...]}` — no `type`, no
`request_id`, breaking the "every gateway error is `problem+json`" contract stated above. This
is now fixed: a 422 uses the same envelope as everything else, with Pydantic's original
per-field errors preserved under an `errors` extension member:

```json
{
  "type": "https://prometheus.internal/errors/validation-error",
  "title": "Validation Error",
  "status": 422,
  "detail": "body.messages: Field required",
  "instance": "/v1/chat/completions",
  "request_id": "1890eba3-...",
  "trace_id": "d83c21ce-...",
  "errors": [{ "type": "missing", "loc": ["body", "messages"], "msg": "Field required", "input": { "model": "..." } }]
}
```

If your SDK already has a fallback path for a non-conforming error body (good defensive
practice regardless), it doesn't need to change — but you can now rely on `type`/`request_id`
being present for 422s the same as any other gateway error, against a gateway that includes
this fix.

**Modality check on `/v1/chat/completions` was one-directional, fixed (RM-66)**: `/v1/embeddings`
and `/v1/images/generations` always rejected a model of the wrong modality outright. Chat
completions only checked modality when an image content part was present (to require a
vision-capable model) — it never checked whether the target model could do chat/text
generation *at all*. Calling `/v1/chat/completions` with an embedding or image-generation
model used to return `200` with garbage/meaningless output (the model still ran, it just
isn't meant to produce chat completions) — billing the caller for a real, wasted generation
instead of a clear, free error. Found live by our own integration testing while building this
guide. Fixed: any model whose modality isn't `text` or `vision` now returns `400
modality-mismatch` immediately, before any backend call, for both streaming and
non-streaming. If your SDK has a "wrong-modality" client-side check of its own as a
convenience, it can stay — this fix just makes the server-side guarantee actually hold for
every direction, so you no longer need to treat "did I pick the right endpoint for this
model" as something only the SDK can catch.

### 5.2 Full error catalog (client-facing endpoints only)

| Status | `type` suffix | Meaning | Retryable? |
|---|---|---|---|
| 400 | `unknown-model` | Model ID not registered. Checked *before* any scope check — an unrecognized model is always 400, never 403, regardless of what the token can access. | No |
| 400 | `modality-mismatch` | Calling `/v1/rerank` with a non-rerank model, calling `/v1/chat/completions` with a model whose modality isn't `text`/`vision` (e.g. an embedding or image-generation model — fixed in RM-66, see note below), sending an image content part to a non-vision model, or calling `/v1/embeddings`/`/v1/images/generations` with the wrong modality. | No |
| 400 | `context-exceeded` | Request exceeds the model's context window. | No (shrink the request) |
| 400 | `unknown-parameter` | A request field outside the accepted subset (§3.3) — every offending name is listed in `detail`. Distinct from `422 validation-error` on purpose: this one means "that field does not exist here", not "that value is wrong". | No (drop the field) |
| 400 | `unknown-instance` | `X-Prometheus-Instance` (§3.7) names something that does not serve this model. A pin never falls back to another replica. | No (fix or drop the header) |
| 400 | `inconsistent-model-group` | The replicas serving this model disagree about their modality, so the gateway refuses the whole group rather than quietly dropping the odd one — answering a chat request from an embedding backend produces confident nonsense, not an error. The detail names each instance and what it claims. | No — needs operator action |
| 400 | `invalid-idempotency-key` | `Idempotency-Key` is malformed or over 255 characters. A `400`, not a `409`, on purpose: it never conflicted with anything, and calling it a conflict would tell you that you had repeated a request. | No (fix the key) |
| 409 | `idempotency-key-reuse` | The key was already used for a *different* request — the fingerprint spans path and payload. Retrying never helps; generate a new key, or resend the original request unchanged. | No |
| 409 | `idempotency-in-progress` | The first call with this key is still running. The one idempotency refusal that resolves by waiting, and the only one carrying `Retry-After`. | **Yes**, after `Retry-After` |
| 409 | `idempotency-response-not-retained` | The original succeeded, but its response was too large to store (over 1 MiB — in practice only images), so there is nothing to replay. Retrying **generates and bills again**; that is why this is refused rather than silently regenerated. | No — a deliberate decision, not a retry |
| 422 | `validation-error` | Request body failed schema validation (missing/wrong-typed field). `errors` extension member carries Pydantic's per-field detail. | No (fix the request) |
| 401 | `missing-credentials` | No/malformed `Authorization` header, or token passed as a query param. | No (fix the request) |
| 401 | `invalid-token` | Signature/algorithm/issuer/audience/`sub`-claim validation failed. | No |
| 401 | `token-expired` | JWT `exp` has passed. | **Yes** — refresh the token, then retry once |
| 401 | `token-revoked` | Token or client was explicitly revoked by an admin. | No — needs new credentials from the operator |
| 402 | `spend-cap-exceeded` | Client has hit its configured monthly spend cap. | No — needs the cap raised, or wait for next month |
| 403 | `forbidden` | Missing `inference:read`/`inference:stream`, or missing the specific `model:<id>` scope. | No |
| 429 | `rate-limit-exceeded-requests` | RPM budget exceeded (per client_id and per user_id, both enforced independently). `Retry-After` header + `retry_after` body field tell you exactly how long to wait. | **Yes**, after `Retry-After` |
| 502 | `upstream-error` | Backend returned repeated 502/503/504s and the gateway's own internal retries (3 attempts, exponential backoff) were exhausted. | Cautiously — see §6 |
| 503 | `model-not-loaded` | Model is registered but not currently deployed/running. | No — needs operator action |
| 503 | `backend-unavailable` | Two distinct causes share this same `type`, and only one of them sets `Retry-After` — see the note below the table. | See below |
| 503 | `rate-limiting-unavailable` | Redis (rate limiter backing store) is down and the deployment is configured fail-closed. | **Yes**, with backoff — transient infra issue |
| 503 | `usage-store-unavailable` | Only on `GET /v1/usage`/`/v1/usage/export` — DB read failed. | **Yes**, with backoff |
| 401 | `unauthorized` | Only on `GET /v1/usage/{request_id}` (§3.8) — the request carried no verified claims. Distinct from `missing-credentials`, which the auth middleware raises earlier for a missing or malformed header. | No |
| 400 | `invalid-date` | Only on `GET /v1/usage` / `/v1/usage/export` (§3.9) — `start`/`end` is not a `YYYY-MM-DD` date. | No (fix the request) |
| 400 | `invalid-range` | Only on `GET /v1/usage/export` — `end` is before `start`. | No (fix the request) |
| 400 | `range-too-large` | Only on `GET /v1/usage/export` — the range exceeds 366 days. Split it into several exports. | No (narrow the range) |
| 404 | `not-found` | Only on `GET /v1/usage/{request_id}` (§3.8) — no usage row with that id **belonging to this client**. Deliberately not a `403`: telling you which ids exist but aren't yours leaks other clients' traffic. | No |
| 503 | `upstream-unavailable` | Only on `POST /oauth2/token` (§2.1) — the gateway could not reach the auth-service. Note this is the *only* problem+json a token request can produce; every other token outcome uses the OAuth2 error shape. | **Yes**, with backoff |
| 503 | `not-configured` | Only on `POST /oauth2/token` — this deployment has no token endpoint wired up. | No — needs operator action |

There is no `404` on the inference-family endpoints for "model not found" — that's a `400
unknown-model`, not a `404`. The only `404` an SDK should expect from a client-facing endpoint
is the `not-found` row above; treat any other one as a genuinely unmapped route.

**`503 backend-unavailable`'s two causes, confirmed precisely (this is not the same code path
in both cases)**:
- **Circuit breaker open** — the request is fast-failed before any network call is made. This
  case **does** set a `Retry-After` header, computed from the circuit's actual recovery time.
  No body `retry_after` field is ever added here (unlike the 429 case above) — only the
  header carries the value.
- **Genuine connection failure** (the backend was unreachable even after the gateway's own
  internal retries) — this case sets **no `Retry-After` header and no body field at all**.
  There is zero backoff signal from the server in this specific case — confirmed by reading
  the response-building call directly, not inferred from absence of documentation. If your
  SDK needs a default backoff here, it is a genuine guess, not a value coming from the API —
  we'd suggest starting conservatively (e.g. 1s, doubling, capped) rather than assuming the
  same magnitude as the circuit-breaker case's typical recovery window, since the two failure
  modes have no guaranteed relationship to each other.

---

## 6. Resilience guidance for the SDK

### 6.1 What the gateway already does — don't duplicate it blindly

For **non-streaming** requests, the gateway itself retries against the backend up to 2 times
(3 attempts total) with exponential backoff + jitter (~200ms, ~400ms) on connection errors and
transient 502/503/504 backend responses, before it ever returns an error to the client. By the
time your SDK sees a `502 upstream-error` or a connection-error-driven `503
backend-unavailable`, the gateway has already tried 3 times — an SDK-side retry on top of that
should be sparing (1 retry, generous backoff) rather than symmetric with what the gateway
already did.

**Streaming requests get none of this internal retry** — a stream failure is entirely on the
SDK to detect (via the in-band error chunk, §3.3) and decide whether to retry from scratch.

### 6.2 Recommended SDK retry policy

- **Retry, with backoff, respecting `Retry-After` when present**: `429`, `503
  backend-unavailable` (when `Retry-After` is set — it tells you exactly when the circuit is
  expected to recover), `503 rate-limiting-unavailable`, `503 usage-store-unavailable`.
- **Retry `503 backend-unavailable` without `Retry-After`, but with your own default backoff**
  — this is the connection-failure variant (§5.2), which carries no server-provided signal at
  all; pick a conservative default (see §5.2's note) rather than assuming it behaves like the
  circuit-breaker variant.
- **Retry once, after a token refresh**: `401 token-expired`.
- **Retry cautiously, capped at 1 attempt, only for non-streaming**: `502 upstream-error` —
  this means the gateway's own 3 internal attempts already failed; a persistent backend problem
  won't be fixed by the SDK trying again immediately. Do not retry this for a request that
  already ran close to the full timeout — see the timeout/duplicate-generation warning in §4.
- **Never retry**: `400`, `401` (other than `token-expired`), `402`, `403`, `503
  model-not-loaded` (needs operator intervention, not a transient condition).
- **Retrying a generation without an `Idempotency-Key` bills twice.** Without a key a retried
  chat/embeddings/images request is a genuinely new generation, not a safe replay — which
  matters for cost and for correctness (streaming in particular: a partial response was already
  delivered to the caller before the failure). **With** a key (§3.7) the retry replays the first
  result: not billed, and the model is not used. This is the mechanism that makes the commonest
  retry — after the SDK's own timeout, where the platform cannot prove nothing ran — safe at
  all. Send one on every generation request the SDK might retry, and surface the tradeoff to
  callers rather than silently retrying without a key.

### 6.3 Proactive rate-limit awareness

Read the `X-RateLimit-Remaining-*` headers on every response (not just on 429s) to back off
before hitting the limit, rather than only reacting to `429`. Note the token-budget headers
reflect the gateway's own post-hoc accounting for TPM (not a hard pre-flight reservation) — a
burst of large requests can still occasionally exceed the token budget between header updates;
treat the TPM headers as a strong signal, not an absolute guarantee against ever seeing a 429.

### 6.4 Circuit breaker awareness

A `503 backend-unavailable` with a `Retry-After` header means the gateway fast-failed the
request without even attempting the backend call (the circuit is open) — this is cheap to
retry after the indicated wait, unlike a request that actually reached an overloaded backend.
`GET /v1/backends` (requires `admin:read` — likely not available to a typical SDK-consuming
client, but worth knowing about for platform-operator tooling built on the same SDK) exposes
live circuit state per model if a health-check style pre-flight is ever useful.

---

## 7. Language-specific implementation notes

**Python**: `httpx` (both sync and async clients) handles SSE reasonably well with
`client.stream(...)` and manual line iteration on `response.aiter_lines()`; parse each
`data: ` line as its own JSON chunk. Cache the token + expiry in the client instance; guard
refresh with `asyncio.Lock`/`threading.Lock` depending on sync vs async usage.

**Go**: `net/http` + `bufio.Scanner` (with a custom `SplitFunc` on blank-line-terminated SSE
records, since the default line-scanner won't correctly handle the blank-line-delimited SSE
format) for streaming; `context.Context` with a deadline for per-call timeout control, since
the 600s/120s defaults from §4 should be overridable per call, not just globally. A
`sync.RWMutex`-guarded token cache with a background goroutine or lazy-refresh-on-use pattern
both work; lazy is simpler and avoids idle-refresh traffic for infrequently-used clients.

**Rust**: `reqwest` (async, via `tokio`) with a manual byte-stream parser for the SSE
format (or `eventsource-stream`/similar crate) — reqwest itself doesn't parse SSE natively.
Token cache behind a `tokio::sync::Mutex` or `RwLock`, refreshed lazily on the read path with
double-checked-locking to avoid a refresh stampede under concurrent requests.

**Across all three**: implement the 7-field error envelope as a proper typed error (not just
a generic HTTP-error wrapper) so SDK callers can branch on the `type` suffix without
string-parsing `detail`. Implement the refresh-ahead token strategy identically across
languages so behavior is consistent for anyone using more than one language SDK against the
same account.

---

## 8. Testing guidance

- **Test credentials**: ask the platform operator for a registered `client_id`/`client_secret`
  scoped to a real (or a small/cheap) model for integration testing — don't test against
  production credentials with broad `model:*` access.
- **Happy path**: obtain a token → `GET /v1/models/mine` (confirm the expected model(s) show
  up) → non-streaming chat completion → streaming chat completion (assert the `[DONE]`
  sentinel is seen and content assembles correctly across chunks) → embeddings → image
  generation if the account has an image model.
- **Error paths worth an explicit test each**: expired token (force a very short TTL if the
  operator can issue one, or fast-forward past `expires_in`) → confirm the SDK refreshes and
  retries transparently; request a model outside the token's `model:<id>` grants → confirm
  `403 forbidden` surfaces clearly, not a generic error; request a nonexistent model → confirm
  `400 unknown-model`; drive request volume past the RPM limit → confirm `429` is handled with
  the `Retry-After` value, and that the rate-limit headers were visible on prior successful
  responses too; if spend caps are configured on the test account, confirm `402
  spend-cap-exceeded` surfaces distinctly from other errors (this is a "you need a different
  fix" error, not a transient one).
- **Streaming-specific test**: simulate/force a mid-stream backend failure if the test
  environment allows it, and confirm the SDK detects the in-band `{"error": "stream
  interrupted"}` chunk rather than treating the stream as having completed successfully.

---

## 9. Confirm with the platform operator before finalizing the SDK

- **The gateway's base URL and port** in each target environment (dev/staging/prod) — not
  defined anywhere in the codebase itself, it's deployment-specific. That is the only host the
  SDK should need; if an environment still hands you an auth-service URL as well, say so,
  because it means the token proxy isn't reachable there yet.
- **TLS trust chain** for each environment — self-signed dev cert (needs explicit client-side
  trust configuration, see §2.6) vs. a real CA-signed production certificate.
- Whether any **per-client rate-limit overrides** exist beyond the global RPM/TPM defaults —
  not found in the current codebase, but worth confirming this hasn't changed by the time the
  SDK ships, since it changes what "safe default throughput" the SDK should assume.
