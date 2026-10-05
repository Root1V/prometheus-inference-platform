# Prometheus Gateway — SDK Integration Guide

**Revision**: 2026-10-05a · `PRM-187/188`
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
  - This holds for an internet-facing deployment too, and the reason is worth stating because
    it is the question people arrive with: mTLS is not the answer to a *distributed* client.
    A client certificate shipped inside an app someone downloads is a secret shipped inside an
    app someone downloads — the same problem in a different encoding. What grows in front of an
    internet-facing gateway is TLS termination, a WAF and per-address limits. See §2.7 for who
    may hold a credential at all, which is the question underneath.
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

### 2.7 Credentials — whose they are, and who issues them

**A credential identifies whoever pays for consumption.** That is the whole rule, and
the two questions people arrive with fall out of it.

#### Whose credential is it

Model grants (`model:<id>`, §2.5) attach to a `client_id`, and every usage row and
billing setting is keyed by it. So a credential is not a key to the platform — it is
**an account**, and it belongs to whoever holds that account.

* **An integrator's credential** belongs on a machine the integrator controls. It must
  never be shipped inside a distributed application: a copy on every user's device is
  a copy of the identity that is granted models and that is billed, and one leak
  exposes every user's usage and the integrator's whole account.
* **An end client's own credential** may live on that client's own devices, including
  a desktop or mobile app they installed. The principal, the grants and the bill are
  theirs, and the blast radius of a leak is their own account and nothing else. This
  is the "bring your own key" shape, and it is supported.

**The distinction is not where the secret sits — it is whose it is.** An app with a
pasted secret is still a public client in RFC 8252's terms, and that is accepted here
when the secret and the bill belong to the same person.

#### Who issues them

**A human administrator, always.** There is no self-service issuance, no registration
endpoint, and no API an integrator can call to mint credentials for its users. This is
a rule rather than a gap in the surface, so it will not appear later:

* The two surfaces that create a principal are the admin dashboard (`admin:write`,
  behind a human login) and auth-service's own admin API with the platform admin key.
  Neither is reachable by an integrator or by a client, and neither is proxied for
  them.
* Probing for one will find a `405` or a bare `404` under `/admin/...`. That is the
  dashboard's static mount answering, not a hidden endpoint — there is nothing behind
  it.

**Why it is a rule**: issuing a credential opens a billing account. A client asking to
use the models is asking to become a customer, and that is a commercial act with a
person on our side of it.

#### What that means for an application's onboarding

There is a human step between *a person wants the AI features* and *their credential
exists*. Design for it rather than around it:

* Treat **"no credential yet"** as a first-class state in the app, not an error. It is
  where every new user starts.
* The request goes to **this platform**, not to the integrator — the integrator cannot
  create it, by design.
* One credential per client, usable on as many of that client's own devices as they
  like. Revocation is per client. Per-device credentials are not issued, so an app
  that assumes one install per credential will be wrong for any user with a phone and
  a laptop.

**And the shape of the arrangement**, because it explains all of the above: the
integrator's product is their application; this platform's product is consumption of
the models. A person can use the application without ever holding a credential here.
The moment they want the model-backed features, they are this platform's customer —
with their own credential, their own grants and their own bill.

## 3. Core API endpoints

All request bodies are **allowlist schemas** — fields not explicitly documented below are
silently dropped and never forwarded to the backend model. Don't rely on passing through
provider-specific extras (e.g. `seed`, `presence_penalty`, `logit_bias`) — they won't reach the
model.

Response bodies for the three inference endpoints (chat/embeddings/images) are **passed
through from the backend verbatim** — the gateway does not reshape or validate them beyond
what's documented here. Treat fields not explicitly guaranteed by this doc (e.g. `id`,
`created`, `system_fingerprint`) as backend-dependent/optional, not a guaranteed contract.

### 3.1 `GET /v1/models` — the models this token may call

**Breaking change, 2026-09-29 (PRM-167).** This endpoint required no authentication and
returned every deployed model. It now requires a Bearer token and returns only the models
that token has `model:<id>` scope for — the same answer `/v1/models/mine` has always given,
which is now an alias of this one.

Two consequences an SDK has to handle, and the second is the quiet one:

* **`401` without a token**, in the usual problem+json envelope. Nothing else changed about
  the shape of a successful response.
* **The list can be shorter than it used to be**, with no error. A client that rendered a
  picker from this endpoint will now see only what it may actually call. That is the point
  — model access has been deny-by-default since RM-07 while discovery was allow-all — but
  it is a silent change in a number, so it is stated here rather than left to be noticed.

**An empty `data` array means this token has no `model:<id>` grants**, not that the platform
has no models. Those are different facts and only an operator can tell them apart: ask for
the grant rather than concluding the catalog is empty.

A token with `admin:write` sees every model regardless of individual grants — an
internal-tooling carve-out (RM-14), not something to expect for a normal client integration.

Returns:

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
      "modality": "text",
      "served_by": 1,
      "payload_schema": "prometheus.chat.v1"
    }
  ]
}
```

`modality` decides which endpoint a model can be called on (see the modality-mismatch error in
§5.2). There are eight, and the last three all route to §3.10:

| `modality` | Endpoint |
|---|---|
| `text` | `POST /v1/chat/completions` (§3.3) |
| `vision` | `POST /v1/chat/completions` with image content parts (§3.3) |
| `embedding` | `POST /v1/embeddings` (§3.4) |
| `image` | `POST /v1/images/generations` (§3.5) |
| `rerank` | `POST /v1/rerank` (§3.6) |
| `classification` | `POST /v1/models/{model}/predict` (§3.10) |
| `zero_shot` | `POST /v1/models/{model}/predict` (§3.10) |
| `typed_decision` | `POST /v1/models/{model}/predict` (§3.10) |

`served_by` is how many replicas serve this model. `1` is the ordinary case; more means requests
load-balance across them, and an individual replica can still be addressed deliberately with
`X-Prometheus-Instance` (§3.7).

**`payload_schema` is a versioned identifier for the request body's contract** — dispatch on
this, not on `modality`. For the five endpoints above whose body this gateway defines, it names
our contract (`prometheus.chat.v1`, `prometheus.embeddings.v1`, `prometheus.images.v1`,
`prometheus.rerank.v1`) and does **not** change when a model moves between engines, because
nothing a caller sends changes. For the pass-through route the body is the engine's, so it names
the engine's contract (`hf-inference.text-classification.v1`,
`hf-inference.zero-shot-classification.v1`, `tei.predict.v1`, `typed-decision.v1`) and a
different engine serving the same modality can mean a different shape — which is precisely the
half `modality` cannot answer.

**`tei.predict.v1` is the clearest example of why**, added in `PRM-184`. It covers both
`classification` and `zero_shot` on that engine, because there one endpoint and one body serve
both — and it is **not** convertible to `hf-inference.zero-shot-classification.v1`. The same
model answers differently: hf-serve returns `{sequence, labels, scores}` normalised across the
candidate labels you supplied, while TEI returns scores across the **model's own** classes and
has no notion of candidate labels at all. A caller dispatching on `modality` would read one as
the other.

Its body, measured against a running server rather than read from a schema, because one case is
a trap:

```
inputs: "a text"                     → one flat list of {label, score}
inputs: ["premise", "hypothesis"]    → ONE PAIR, not a batch of two texts
inputs: ["a", "b", "c"]              → 422
inputs: [["a"], ["b"]]               → a batch of two single texts → two lists
inputs: [["p1","h1"], ["p2","h2"]]   → a batch of two pairs → two lists
raw_scores: true                     → logits instead of probabilities
```

**A batch is always a list of lists.** A flat array of two strings is silently read as a single
pair and answers once; a flat array of three or more is a `422`. So the natural-looking "send me
my N texts as an array" is the one form that will quietly return a single wrong answer, and
`[[t] for t in texts]` is the form that batches. The cap on how many entries one request may
carry is set per instance (64 on this deployment).

The names are not `openai.chat.v1` on purpose: this gateway accepts an allowlisted subset of the
OpenAI request fields (§3.3), so that name would promise a compatibility it does not have.

**`null` means "we cannot state a shape", not "there is no field".** Two cases produce it: a
modality newer than this field's map, and a model whose replicas run on engines with different
body shapes — a misconfiguration the catalog reports as unknown rather than resolving by picking
one replica's answer. Treat `null` as "do not guess".

The engine serving a model is deliberately **not** published, here or anywhere else. It would key
your dispatch table to the name of our implementation, so an internal swap that preserves the
contract would break you for no reason a caller could see.

### 3.2 `GET /v1/models/mine` — an alias of §3.1

Identical to `GET /v1/models` since PRM-167: same requirement, same filtering, same response.
It is kept because it is documented and SDKs call it; there is no reason to migrate off it,
and no reason to prefer it.

It exists because §3.1 used to be the full public catalog and a token had no other way to
find out what it could call.

**This is the right endpoint behind a "Test connection" button.** It proves three things in
one request that `GET /health` cannot prove at all: that the gateway is reachable, that the
credential works, and that there is something this caller may actually send. A green light
from `/health` means only that a process answered.

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
schemas); `logprobs` / `top_logprobs` (see below).

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

**`logprobs` — how confident the model was (PRM-187).** Ask for the per-token probability
behind the answer, so an agent can decide when to escalate to a human instead of acting on a
guess:

```json
{
  "model": "qwen3-0.6b",
  "messages": [{ "role": "user", "content": "yes or no?" }],
  "logprobs": true,
  "top_logprobs": 3
}
```

`logprobs: true` returns the chosen token's own probability. `top_logprobs: N` (0–20) adds the
N most likely alternatives at each position. The result arrives in OpenAI's shape, under
`choices[0].logprobs.content[]` — one entry per generated token:

```json
{ "token": "yes", "logprob": -0.00054,
  "top_logprobs": [ { "token": "yes", "logprob": -0.00054 },
                    { "token": "no",  "logprob": -7.6 } ] }
```

These are **natural-log** probabilities, so `-0.00054` is ~99.95% and `-7.6` is ~0.05%. Use
`exp(logprob)` for a probability.

**`top_logprobs` requires `logprobs: true`.** Sending it alone — or alongside
`logprobs: false` — is a `422 validation-error`, and the gateway refuses it before the engine
sees it. The rule is the engine's; it is enforced here so the refusal reaches you in the same
problem+json envelope as everything else (§7) rather than in llama.cpp's own error shape.

Supported on `llama_cpp`, which is where it was measured. On an engine without it the engine's
own answer stands — the gateway does not emulate it. Send `require_parameters: true` if you
need to be told rather than quietly given a response with no `logprobs` key.

**Anything else is accepted, ignored, and named back to you (PRM-127).** This endpoint takes an
OpenAI-compatible *subset*. A field outside it — `n`, `presence_penalty`, `frequency_penalty`,
`logit_bias`, `user`, `seed` — does not fail your request and does not reach the engine either.
It comes back listed in a response header:

```
X-Prometheus-Ignored-Parameters: logit_bias, seed
```

The header is absent when there is nothing to report, so its presence always means something.
Until PRM-127 these were dropped in silence, and this guide said so in as many words — which
made it a decision rather than an oversight, and the decision was wrong: a setting that does
nothing and says nothing is indistinguishable from one that works.

**`require_parameters: true` turns that into a `400 unknown-parameter` instead.** Set it when
you would rather fail than be quietly given something else — reproducibility runs, structured
extraction, anything where a silently-dropped parameter invalidates the result. Off by default,
because most callers want the completion more than they want the argument.

The shape is [OpenRouter's](https://openrouter.ai/docs/guides/routing/provider-selection): route
and ignore what cannot be honoured, with an opt-in for callers who need every parameter
respected. The header is our own addition — OpenRouter's clients can look up what each provider
supports, and you cannot, so the ignoring has to announce itself.

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
- **A streamed request can fail *before* the stream begins, with a real HTTP status.** The
  gateway opens the connection to the engine and reads its status **before** the
  `200`/`text/event-stream` headers exist, so a request the engine refuses comes back as an
  ordinary error response: the engine's own status and OpenAI-shaped body, exactly as the
  non-streaming form of the same endpoint returns it — a client that sets `stream: true` does
  not get a different error contract for doing so. A connection that never opened is a
  `503 backend-unavailable` in the problem+json envelope. Neither is billed.
  **An SDK must not assume `stream: true` implies a `200`**: check the status before starting
  to parse SSE. Until `PRM-143` this case arrived as a `200` whose body was nothing but the
  terminal frame, which no caller could distinguish from a legitimately empty answer.
- **Mid-stream failures, by contrast, don't produce an HTTP error status** — once generation
  has begun the `200`/`text/event-stream` headers are already committed. Instead, the
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
- `raw_scores` — optional boolean, **PRM-183**. `relevance_score` becomes the model's raw logit
  instead of a probability. A reranker's probabilities saturate near 1.0 — Centinela measured
  0.99 for a document only loosely related to the query — and a saturated probability cannot be
  calibrated while the logit behind it can.

  **Not every engine has it.** Where the engine serving the model does not, the request still
  succeeds and the field is named in `X-Prometheus-Ignored-Parameters`; with
  `require_parameters: true` it is a `400 unknown-parameter` whose `detail` names the engines
  that do. So it is safe to send unconditionally and discoverable when it is dropped — the same
  contract PRM-127 defines for every other parameter, for a second reason: not "this gateway does
  not act on it" but "the engine behind it does not have it".

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

### 3.6b Traffic splits — one name, several models

A model name you send may be a **traffic split** rather than a model: several real
models behind one public name, with weights. SageMaker calls these production variants,
KServe calls it a canary percentage. An operator defines them; nothing in the request
selects one.

**Three things follow, and an SDK only needs to know the third.**

- **The request does not change.** You send the public name in `model`, exactly as before.
- **`model` in the response is still the name you sent**, never the variant. A canary must
  not change what a response says, or every client parsing `model` would see a name it
  did not ask for.
- **`X-Prometheus-Variant` names the model that actually served it**, when a split was
  applied and only then. Same idea as `X-Prometheus-Instance` (§3.7), one level up: that
  header says which replica, this one says which model.

```
POST /v1/chat/completions   {"model": "chat", ...}
  200  X-Prometheus-Variant: qwen3-8b-q6
       body: {"model": "chat", ...}
```

**Usage attribution is per variant, and this matters if you reconcile.**
`GET /v1/usage/{request_id}` (§3.8) reports `model` as the name you sent — `chat` — so
reconciling against the response still matches. The row is *billed* against the variant
that served, at that variant's rate, because two checkpoints priced the same is a
coincidence rather than a rule. So a split name's total cost is not
`requests × one rate`, and computing it that way will drift the moment the variants are
priced differently.

**A variant with no healthy replica fails, and does not fall back.** If the split sends
10% of traffic to a variant that is down, you see 10% failures — deliberately. Falling
back to the stable variant would mean a canary could never fail its rollout: it would
report perfect health while the other variant carried everything.

### 3.7 Headers

**Required**: `Authorization: Bearer <token>` on **every** endpoint except `GET /health`
and `POST /oauth2/token` (which carries its own credentials in the body, so demanding a
token to obtain one would be circular). `GET /v1/models` was an exception until PRM-167 and
is not one any more. `Content-Type: application/json` on every POST.

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

**Response headers worth reading.** The rate-limit headers below are on the inference
endpoints. `X-Request-ID` and `X-Trace-ID` are on **every** response including `/health` and
`/v1/models` — this list previously excluded them, which Axonium measured and reported
(`A-29`); they were there all along:

```
X-Request-ID                       — fresh UUID per request, generated server-side
X-Trace-ID                         — for log correlation; see the exact adoption rule below
X-RateLimit-Scope                  — which budget the six numbers below describe (PRM-129)
X-RateLimit-Limit-Requests
X-RateLimit-Remaining-Requests
X-RateLimit-Reset-Requests         — unix timestamp of the next window
X-RateLimit-Limit-Tokens
X-RateLimit-Remaining-Tokens
X-RateLimit-Reset-Tokens
X-Prometheus-Instance              — which replica answered (its per-model label, e.g. "#2")
X-Prometheus-Ignored-Parameters    — request fields outside the accepted subset (§3.3); absent when none
X-Prometheus-Instance-Id           — the same replica's instance id
Idempotent-Replay                  — "true" only on a replayed response
X-Idempotent-Replay-Of             — on a replay: the request id that was actually billed
```

**The rate-limit window, stated precisely (asked for in `C-01 §4`, and it was nowhere)**: the
budget is a **fixed 60-second bucket aligned to the wall clock**, not a sliding window per
request. A new bucket begins at second 0 of each minute and the whole allowance is available
again at that instant — `X-RateLimit-Reset-Requests` is that timestamp. Two consequences worth
designing for:

- A burst can span a boundary and pass, where the same burst a few seconds earlier would be
  refused. If you pace requests, pace against `X-RateLimit-Remaining-Requests` rather than
  against an assumed rate.
- **The budget is counted per credential**, so one client's traffic never consumes another's.
  The *limit value*, however, is platform configuration per endpoint — not per client — so a
  429 means your own credential exhausted its own bucket, and raising it is an operator action.

Each **scope** in `X-RateLimit-Scope` is its own bucket with its own limit. A 429 on one does
not imply the others are exhausted, which is exactly what that header exists to tell you — back
off the scope it names, not the whole API.

**Read the set from the header rather than from a list here.** The previous revision of this
paragraph enumerated it and got it wrong: it said `default`, `chat_completions`, `admin` and
`predict`, **omitting `embeddings` and `rerank`**, which have had their own buckets since
PRM-129 — the paragraph further down says so, and that is the one that was right. Axonium caught
the contradiction, and a caller who had indexed their quota accounting against the short list
would have been missing two buckets. The header names the bucket that answered; that is the
value to key on.

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
  "cost_usd": 0.000021,
  "rates": {
    "prompt_price_per_1m": 1.5,
    "completion_price_per_1m": 2.0,
    "image_price_each": null
  },
  "instance_id": "qwen3-0-6b-iq4-nl-local-1",
  "created_at": "2026-09-14T19:33:54.580624"
}
```

**`rates` is the arithmetic that produced `cost_usd`, so this row can be checked and not only
copied.** It answers A-25: the export (§3.9) had carried the applied rates since PRM-115 and needs
`admin:read`, while this row — the one path a consumer without admin scope can walk — carried the
cost alone.

- **`cost_usd == prompt_tokens × prompt_price_per_1m / 1e6 + completion_tokens ×
  completion_price_per_1m / 1e6`**, and for images `image_count × image_price_each`. Verify it;
  do not re-derive it from a price list.
- **The rates are frozen at the moment the row was billed** and never recomputed on read (RM-60).
  A price change does not re-rate history — and now that guarantee is *verifiable*, because a
  consumer checking an old row against today's prices would otherwise find a mismatch it could not
  explain.
- **`null` means no price was configured for that model, not zero.** `cost_usd` is `null` for the
  same reason. The platform never reports an unpriced request as free.
- `request_kind` is `chat`, `embedding`, `image` or `predict` (§3.10). **Four values, and
  `predict` is newer than the others** — if your usage type enumerates this field as a closed set,
  that is the value that breaks it.

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

### 3.10 `POST /v1/models/{model}/predict` — the tasks OpenAI has no shape for

**Added after revision `2026-09-19b`, which is why A-26 found three modalities in the catalog
with no endpoint to call them with.** Three modalities route here and only here:

| `modality` | Example model | What it does |
|---|---|---|
| `classification` | `sst2-clf` | Assigns a label from a fixed set the model was trained on |
| `zero_shot` | `von-decide` | Scores arbitrary candidate labels supplied per request (NLI) |
| `typed_decision` | `laya-decide` | Answers several typed questions in one forward pass, each with a probability distribution *and* a separate confidence |

**The body is forwarded to the engine verbatim, and its answer comes back verbatim.** Every
other route here is OpenAI-shaped because every task it serves has an OpenAI endpoint to be
shaped like. These do not, and inventing a body for them would be this gateway deciding, on the
engine's behalf, what its API should look like.

**So the request and response shape is the engine's, and it is not stable across engines within
one modality.** This is the cost of pass-through, and it is paid by the caller. Model it as a
`predict(model, body)` that interprets nothing — a `classify(text)` typed per modality would
promise a stability this endpoint does not offer. `payload_schema` in `GET /v1/models` (§3.1) is
what identifies the shape.

Real requests and responses from a live deployment:

```
POST /v1/models/sst2-clf/predict
{"inputs": "El servicio ha sido excelente"}

200  [{"label": "POSITIVE", "score": 0.9783}]
```

```
POST /v1/models/von-decide/predict
{"inputs": "Me cobraron dos veces la misma factura",
 "parameters": {"candidate_labels": ["hubo un cargo duplicado", "el cliente está satisfecho"]}}

200  {"sequence": "...", "labels": [...], "scores": [0.9963, 0.0037]}
```

```
POST /v1/models/laya-decide/predict
{"state": {"email": "Me cobraron dos veces y necesito el reembolso hoy o cancelamos el plan"},
 "questions": {
   "category":   {"type": "choice", "instructions": "¿Qué equipo debe atenderlo?",
                  "criteria": ["facturación", "soporte", "ventas"]},
   "churn_risk": {"type": "noul",   "instructions": "¿El cliente amenaza con cancelar?"}}}

200  {"answers": {"category":   {"choice": "facturación", "confidence": 1.0,
                                 "probabilities": {...}},
                  "churn_risk": {"noul": 0.6354, "confidence": 0.6354}},
      "usage": {"input_tokens": 92, "output_tokens": 0},
      "routing": {...}}
```

**What does not pass through** — the model still resolves, `inference:read` plus the specific
`model:<id>` scope is still required, a dead replica is still skipped, and the request is still
metered and still counts against a spend cap. The shape is the backend's; the policy is ours.

- **A model that *has* an OpenAI endpoint is refused here** with `400 modality-mismatch` — the
  inverse of every other handler's check. Without it the same model would be reachable two ways,
  with two billing paths and two rate-limit budgets, and the one that bills correctly would be
  whichever the caller did not use.
- **The engine's errors are wrapped, not forwarded** — see `predict-backend-rejected` in §5.2.
- **`request_kind` on the usage row is `"predict"`** (§3.8), a fourth value beside `chat`,
  `embedding` and `image`. If your usage types enumerate that field as a closed set, this is the
  value that breaks them.
- **The rate-limit budget is `predict`** (§6.3), shared by all three modalities.

**One caveat about `typed_decision` that is not in the model's own documentation**, measured here
by asking the same question three ways against the same text:

```
texto: "...me cobraron dos veces..."        laya-decide   von-decide
  "¿le cobraron dos veces?"                   0.988         0.999
  "¿hubo un cargo duplicado?"                 0.945         0.999
  "¿se produjo una facturación errónea?"      0.700         0.998

texto: "...o cancelamos el plan"            laya-decide   von-decide
  "¿amenaza con cancelar?"                    0.830         0.927
  "¿amenaza con irse?"                        0.024         0.613
```

`von-decide` is an NLI model — judging whether one sentence entails another *written differently*
is the task it was trained on, so a paraphrase costs it a thousandth. `laya-decide` matches
vocabulary more than meaning, and on the harder pair it does not hedge: it inverts. Word a
`typed_decision` question with the vocabulary that appears in the text, or use `zero_shot`.

---

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
middleware add an optional `retry_after` (int seconds) and a `scope` (the budget that ran out —
§6.3) alongside the standard fields. They used to omit `trace_id`; **as of `2026-09-19b` they no
longer do**, so this envelope is now a strict superset of the standard one rather than a variant
of it. Axonium found the `scope` header missing from these same responses and named the cause —
this middleware writing its own envelope — which is why the omission was closed rather than
documented again.

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

**PRM-188 — and that contract now actually holds for every 422.** RM-65's handler was only
ever exercised by field-constraint failures (`Field required`, a number out of range). A
failure raised by a *validator* carries the exception object itself in `ctx.error`, which
does not serialise — so the handler threw while building its own response and the request
left as a **500 with no envelope at all**. Two requests reached it: a `role` outside
`{system, user, assistant, tool}`, and an `image_url.url` that is not a `data:` URI (§3.3's
SSRF rule), which meant the gateway answered its own security refusal with a 500.

Both are now ordinary 422s in the envelope above. If you probed either of these and wrote a
fallback for a 500, you can drop it. `errors[].ctx.error` renders as `{}`; the validator's
message is in `errors[].msg` and in `detail`, which is where to read it.

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
| 400 | `unknown-parameter` | **Only when you sent `require_parameters: true`** — a request field outside the accepted subset (§3.3), every offending name listed in `detail`. Without that flag the same request succeeds and the fields come back in `X-Prometheus-Ignored-Parameters`. Distinct from `422 validation-error` on purpose: this means "that field does not exist here", not "that value is wrong". | No (drop the field, or the flag) |
| 400 | `unknown-instance` | `X-Prometheus-Instance` (§3.7) names something that does not serve this model. A pin never falls back to another replica. | No (fix or drop the header) |
| 400 | `inconsistent-model-group` | The replicas serving this model disagree about their modality, so the gateway refuses the whole group rather than quietly dropping the odd one — answering a chat request from an embedding backend produces confident nonsense, not an error. The detail names each instance and what it claims. | No — needs operator action |
| 400 | `invalid-idempotency-key` | `Idempotency-Key` is malformed or over 255 characters. A `400`, not a `409`, on purpose: it never conflicted with anything, and calling it a conflict would tell you that you had repeated a request. | No (fix the key) |
| 409 | `idempotency-key-reuse` | The key was already used for a *different* request — the fingerprint spans path and payload. Retrying never helps; generate a new key, or resend the original request unchanged. | No |
| 409 | `idempotency-in-progress` | The first call with this key is still running. The one idempotency refusal that resolves by waiting, and the only one carrying `Retry-After`. | **Yes**, after `Retry-After` |
| 409 | `idempotency-response-not-retained` | The original succeeded, but its response was too large to store (over 1 MiB — in practice only images), so there is nothing to replay. Retrying **generates and bills again**; that is why this is refused rather than silently regenerated. | No — a deliberate decision, not a retry |
| 4xx | `predict-backend-rejected` | Only on `POST /v1/models/{model}/predict` (§3.10) — the engine refused the request. **The engine's own error body is preserved verbatim under the `backend_error` extension member**, and the engine's status code is kept (a 422 stays a 422). Named for what the gateway can see: a 429 or 403 from the engine is also a 4xx and is not about the payload, so the type does not claim a cause — `backend_error` and the status carry that. | No, unless the status says so |
| 422 | `validation-error` | Request body failed schema validation (missing/wrong-typed field). `errors` extension member carries Pydantic's per-field detail. | No (fix the request) |
| 401 | `missing-credentials` | No/malformed `Authorization` header, or token passed as a query param. | No (fix the request) |
| 401 | `invalid-token` | Signature/algorithm/issuer/audience/`sub`-claim validation failed. | No |
| 401 | `token-expired` | JWT `exp` has passed. | **Yes** — refresh the token, then retry once |
| 401 | `token-revoked` | Token or client was explicitly revoked by an admin. | No — needs new credentials from the operator |
| 402 | `spend-cap-exceeded` | Client has hit its configured monthly spend cap. | No — needs the cap raised, or wait for next month |
| 403 | `forbidden` | Missing `inference:read`/`inference:stream`, or missing the specific `model:<id>` scope. | No |
| 429 | `rate-limit-exceeded-requests` | RPM budget exceeded. `Retry-After` header + `retry_after` body field tell you exactly how long to wait, and the `scope` body field says **which** budget ran out. Charged per `client_id` and per `user_id` independently — except where they are the same string, as in a `client_credentials` token, which is charged once (PRM-128; before that fix a 60 RPM budget stopped at 30 while the header still read 60). | **Yes**, after `Retry-After` |
| 502 | `upstream-error` | Backend returned repeated 502/503/504s and the gateway's own internal retries (3 attempts, exponential backoff) were exhausted. On `/v1/models/{model}/predict` (§3.10) it also covers any 5xx the engine returns — there the engine's body is under `backend_error` and its real status under `backend_status`, because a 500 the engine produced is not one the caller can act on. | Cautiously — see §6 |
| 503 | `capacity-exhausted` | **Every replica of the model is at capacity** — the request was refused rather than queued behind work that would outlive its own timeout. Distinct from `backend-unavailable` on purpose: that one means the replicas are broken or unreachable and somebody should look at them, this one means they are busy and working. Carries `Retry-After: 1`, which is a hint rather than a promise — a slot frees when a request finishes, and how long that takes is the model's business. | **Yes**, shortly. Back off and retry; repeated occurrences mean the model needs another replica |
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
| 404 | `unknown-route` | **No route at that URL.** A mistake in the caller's code, not a fact about their data — deliberately a different `type` from `not-found` above, which an SDK may reasonably retry or treat as an empty result. `detail` names the method and path. | No (fix the URL) |
| 405 | `method-not-allowed` | The URL exists, the verb does not. The `Allow` response header lists the verbs that do. | No (fix the method) |
| 503 | `rerank-dialect-unknown` | Only on `POST /v1/rerank` — the model is running, but on an engine whose rerank request shape this gateway has not recorded. Not a transient fault: a reranker on a new engine is not llama.cpp's shape just because the last one was, so the shape is recorded deliberately rather than assumed. | No — needs operator action |

There is no `404` on the inference-family endpoints for "model not found" — that's a `400
unknown-model`, not a `404`. The only `404` an SDK should expect from a client-facing endpoint
is the `not-found` row above; any other one is a genuinely unmapped route, and **since
`PRM-174` it says so**: an unmapped route and a wrong verb both arrive in this same envelope,
with `unknown-route` or `method-not-allowed` as the `type`.

That is worth one line of history, because this paragraph used to end at "treat any other one
as a genuinely unmapped route" without saying what shape one arrived in — and the shape was
Starlette's bare `{"detail": "Not Found"}`, with no `type`, no `request_id` and no `trace_id`.
It was invisible because the auth middleware runs **before** routing: on any endpoint that
needs a token, an unmapped URL is a `401` in this envelope and the router's `404` is never
reached. Only the unauthenticated surfaces could show it — which is exactly where Axonium
found it, probing `/admin/...`. If your error handling has a branch for "a `404` that isn't
problem+json", you can delete it.

**`503 backend-unavailable`'s two causes, confirmed precisely (this is not the same code path
in both cases)**:
- **Circuit breaker open** — the request is fast-failed before any network call is made. This
  case **does** set a `Retry-After` header, computed from the circuit's actual recovery time.
  No body `retry_after` field is ever added here (unlike the 429 case above) — only the
  header carries the value.
- **Genuine connection failure** (the backend was unreachable even after the gateway's own
  internal retries) — this case sets **no `Retry-After` header and no body field at all**.
  Since `PRM-143` this also covers a **streaming** request whose connection never opened. There
  the gateway performs no internal retries at all (see §6.1), so the 503 arrives after one
  attempt rather than three — the backoff guidance below applies unchanged, but the elapsed
  time before you see it is shorter.
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

**There is more than one budget, and the response says which one it is reporting (PRM-129).**
Endpoints are grouped, and each group has its own RPM/TPM budget:

| Budget | Endpoints |
|---|---|
| `chat_completions` | `POST /v1/chat/completions` |
| `embeddings` | `POST /v1/embeddings` |
| `rerank` | `POST /v1/rerank` |
| `predict` | `POST /v1/models/{model}/predict` (§3.10) — all three pass-through modalities share one budget, not one each |
| `default` | everything else (images, usage, token) |

`X-RateLimit-Scope` names the budget the six `X-RateLimit-*` numbers on that response belong to,
and a `429` carries the same name in its `scope` body field. **Key it before you cache it**: one
logical operation that calls embeddings, then rerank, then chat, gets three responses describing
three different budgets, and a single "last seen" slot would end up holding whichever answered
last while looking entirely plausible.

`embeddings` and `rerank` shared `default` until PRM-129 — not a decision anybody made, just what
happens when only one route is on the grouping map. A caller spending two of its three requests on
that pair had half the ceiling it expected.

### 6.4 Circuit breaker awareness

A `503 backend-unavailable` with a `Retry-After` header means the gateway fast-failed the
request without even attempting the backend call (the circuit is open) — this is cheap to
retry after the indicated wait, unlike a request that actually reached an overloaded backend.
`GET /v1/backends` (requires `admin:read` — likely not available to a typical SDK-consuming
client, but worth knowing about for platform-operator tooling built on the same SDK) exposes
live circuit state per model if a health-check style pre-flight is ever useful.

**`GET /metrics` returns the same circuit state and also requires `admin:read`** (PRM-163). Until
2026-09-28 it required nothing at all, so the sentence above was misleading in the direction that
matters: `admin:read` described what one door asked for while an adjacent door asked for nothing
and answered with the same figures, plus per-instance ids, replica counts, redis reachability and a
count of failed JWT validations. If you built anything against an unauthenticated `/metrics`, it
now needs the scope; nothing in the three SDKs read it.

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
