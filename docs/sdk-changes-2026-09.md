# Changes for SDK consumers — September 2026

Companion to [`sdk-integration-guide.md`](sdk-integration-guide.md), which stays the full
reference. This document covers only what changed since the guide was written, and what you
have to do about it.

**Nothing here breaks a working integration today.** Every previous model name still resolves.
Read §1 first — it is the only part that eventually needs action from you.

---

## 1. Models have real names now

A model used to be identified by whatever string happened to be its registry id. It now has a
**slug**: a public, stable name that is the thing you send as `model`.

| Name you use today | New name | Modality |
|---|---|---|
| `qwen3-0-6b-iq4-nl-local-2` | **`qwen3-0.6b`** | text |
| `qwen3vl-8b-q4` | **`qwen3-vl-8b`** | vision |
| `qwen3-embedding-0-6b-q8-0-local` | **`qwen3-embedding`** | embedding |
| `sd-turbo-test` | **`sd-turbo`** | image |
| `gpt-oss-20b-mxfp4` | unchanged | text |

**The old names still work.** They resolve as aliases, and there is no removal date. Migrate
when it suits you.

**Your grants have already been reissued** to the new names, so both spellings are authorised.
A grant on a slug covers every alias that model answers to — you will not need a second grant
when you switch.

Two properties worth relying on:

- **A slug never changes.** Once published it is frozen. If a model needs a different name it
  is a different model, with its own name.
- **A slug is never reused.** When a model is retired its name is retired with it, so a name
  you once used can never silently start pointing at a different model.

---

## 2. `GET /v1/models` returns one entry per model

It used to list a model's catalog id *and* every instance id serving it, so a model with one
replica appeared three times. If you build a picker from this endpoint you were rendering
duplicates.

It now returns exactly one entry per model, with a new field:

```json
{
  "id": "qwen3-0.6b",
  "object": "model",
  "owned_by": "prometheus",
  "context_length": 4096,
  "family": "",
  "quantization": "IQ4_NL",
  "modality": "text",
  "served_by": 2
}
```

`served_by` is how many instances currently serve that model. Informational — you never
address an instance through `model`.

`context_length` for a multi-instance model is the **smallest** of its instances, so a request
that fits the advertised number fits whichever instance serves it.

Aliases still resolve on request; they are simply no longer advertised here.

---

## 3. One model, several machines

A model can now be served by more than one process, possibly on different hosts. This is
transparent: you send the model name, the gateway picks an instance, and:

- **Failover is automatic.** If an instance is unreachable the request moves to another one.
  You do not see an error unless *every* instance is down.
- **Usage is billed to the model**, never to the instance that happened to serve it. One
  model, one line on the invoice, however many machines are behind it.
- **Retries are still yours to own** for the cases in the integration guide. Nothing about
  §"Resilience" changes.

### Knowing which instance answered

Every successful inference response now carries:

```
X-Prometheus-Instance: #2
X-Prometheus-Instance-Id: qwen3-0-6b-iq4-nl-local-1
```

`X-Prometheus-Instance` is a short label unique within the model (`#1`, `#2`, …).
`X-Prometheus-Instance-Id` is the full instance id. Log whichever you prefer — when you report
a slow or odd response, one of these is what lets us find the machine.

### Pinning to one instance

Send the same header on the **request** to force a specific instance, by either spelling:

```http
POST /v1/chat/completions
X-Prometheus-Instance: #2
```

Intended for reproducing a problem or comparing two machines — not for normal traffic, since
it opts out of load balancing and failover.

- A name that doesn't serve the requested model → **400 `unknown-instance`**.
- The instance exists but is unavailable → **503 `backend-unavailable`**. A pin never silently
  falls back to another instance; if you asked for that one, you get that one or an error.

**Do not put an instance id in the `model` field.** It still resolves today, but `model` is
for models: a grant covers a model, billing attributes to a model, and `/v1/models` lists
models. Instance targeting lives in the header.

---

## 4. Errors

Two fixes you reported, both shipped:

- **422 validation errors** now use the same `application/problem+json` envelope as every
  other error, with `type`, `title`, `status`, `detail`, `request_id` and per-field detail
  under an `errors` member. They were previously FastAPI's default shape with no `type` and no
  `request_id`.
- **A chat request against an embedding or image model** now returns **400 `modality-mismatch`**
  instead of 200 with nonsense output.

One new error type:

| Type | Status | When |
|---|---|---|
| `unknown-instance` | 400 | `X-Prometheus-Instance` names something that doesn't serve this model |

And one changed message: `503 backend-unavailable` now names each instance and why it can't
take the request. The `type` and status are unchanged; only `detail` is more specific.

---

## 5. What you need to do

Nothing urgent. In rough order of value:

1. **Stop treating `/v1/models` entries as instances.** If you deduplicated its output, or
   filtered it, that workaround can go.
2. **Log `X-Prometheus-Instance-Id`** on responses. It costs nothing and makes any report
   about a specific slow or wrong response actionable.
3. **Move to the new model names** when convenient. Old ones keep working.
4. **If you address instances anywhere**, move that to the header.

## What has *not* changed

The token flow, JWT claims, TTLs and refresh behaviour; the four endpoints and their request
and response shapes; scopes and deny-by-default; TLS; timeouts and the resilience guidance.
[`sdk-integration-guide.md`](sdk-integration-guide.md) remains correct on all of it.
