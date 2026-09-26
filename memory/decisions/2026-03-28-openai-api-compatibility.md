# OpenAI API compatibility

> **Reviewed 2026-09-26 — deliberately amended, not abandoned.**
>
> The decision below says the gateway preserves the exact OpenAI shape for **all**
> inference endpoints. `PRM-136` broke that on purpose with
> `POST /v1/models/{model}/predict`, which forwards a body verbatim to engines
> whose task has no OpenAI endpoint to be shaped like — classification, zero-shot
> decisions, and typed decisions.
>
> **The principle as it actually stands**: OpenAI's shape wherever OpenAI has one,
> and the engine's own shape where it does not, because inventing a body would be
> this gateway deciding what an engine's API should look like on its behalf. What
> never passes through is the policy — auth, scope, metering, budget, and, since
> `PRM-142`, the error envelope.
>
> `payload_schema` in `GET /v1/models` (`PRM-144`) is how a caller tells which of
> the two a model expects, so this is now a documented contract rather than an
> exception.


**Date**: 2026-03-28  
**Status**: accepted  
**Scope**: all gateway inference endpoints

---

## Context

The gateway needs an API contract for client applications to send inference requests. Two options exist: design a proprietary Prometheus API, or preserve the shape of the OpenAI Chat Completions API.

---

## Decision

The gateway preserves the **exact OpenAI Chat Completions request and response shape** for all inference endpoints.

- Request: `POST /v1/chat/completions` with `model`, `messages`, `stream`, `max_tokens`, `temperature`
- Response: same JSON structure as OpenAI (non-streaming and SSE streaming with `data: [DONE]`)
- Model discovery: `GET /v1/models`

---

## Rationale

- Client applications can use the `openai-python` SDK and other OpenAI-compatible clients without any wrapper code.
- The llama.cpp HTTP server (`llama-server`) already implements the OpenAI-compatible API — the gateway proxies it transparently with no schema translation.
- Future model backends that support OpenAI compatibility (vLLM, Ollama, etc.) can be added without changing the client contract.

---

## Consequences

- **All future specs that add or modify inference endpoints must preserve OpenAI schema compatibility.** Breaking changes to the request/response shape require a new spec and a versioned path (e.g. `/v2/`).
- Fields not in the OpenAI spec must not be added to the response body — extend via headers or a separate endpoint.
- The `model` field in the request maps to an internal model ID in `registry.yaml` — it is not the HuggingFace repo name or GGUF filename.

---

## Rejected alternatives

| Alternative | Reason rejected |
|-------------|----------------|
| Proprietary Prometheus API | Forces every client to write a custom adapter; no SDK support |
| Full OpenAI proxy (pass-through everything) | Cannot enforce security controls, rate limits, or input sanitisation on unknown fields |

---

## References

- `memory/specs/001-gateway-core.md` — initial gateway implementation
- OpenAI Chat Completions API: https://platform.openai.com/docs/api-reference/chat
- llama.cpp server OpenAI-compatible API: https://github.com/ggerganov/llama.cpp/blob/master/examples/server/README.md