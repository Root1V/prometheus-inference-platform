# Answers to the Axonium SDK questions — September 2026

Answering `preguntas-axonium-a-prometheus-nuevo_Cambio.md`. Everything below was checked
against the code and, where it was observable, against the running deployment — not answered
from memory.

**Two of your questions found real defects.** B1 and C3 are fixed and live; §B and §C3 below
describe what changed. Block A is answered, not changed, and one of those answers is a "yes"
you won't like.

---

## A · Retries and failover

### A1 · Can generation have started on the first instance before failover?

**Yes — but it cannot double-bill you, and that distinction is the answer to your question.**

Usage is recorded once per *returned response*, never per attempt: an aborted attempt raises,
failover moves on, and nothing is written. So however many instances a request touches
internally, **your invoice shows one generation**. What a mid-exchange failure wastes is our
compute on the abandoned instance, not your money.

The retry that *can* double-bill is **yours**, and that is what idempotency keys are for —
see the end of this section.

With that settled, here is when work may already have started before failover, because it
still bounds what a retry of yours means:

| Condition | Did the backend start work? |
|---|---|
| `ConnectError` / `ConnectTimeout` | **No.** The instance never accepted the connection. |
| `RemoteProtocolError` | **Possibly.** The connection broke mid-exchange. |
| Backend returned `502` / `503` / `504` | It answered. On llama.cpp this is usually "no free slot" or "still loading", but that is a convention, not a guarantee. |
| **Read timeout** | **Not retried and not failed over.** |

So the window where a first instance may already have been generating is
`RemoteProtocolError`. Note the last row: the case where generation almost certainly *did*
start — the backend accepted the work and then took too long — is deliberately the one we
never retry.

**What this means for your retry policy.** Your rule (retry only where the platform proves
nothing ran) stays correct, and our internal failover doesn't widen it — a `502` still means
every internal attempt failed and nothing was billed. What you cannot make safe today is a
retry after *your* client timed out: the gateway may still have been generating, and your
retry would be a second billable generation.

**That case is now closed** — see §D. Send an `Idempotency-Key` and a replay returns the first
result instead of generating again.

### A2 · Are the 3 internal attempts per instance or shared across instances?

**Per instance.** `backend_retry_max` is 2, so 3 attempts each. With two instances that is up
to 6 attempts before you see `502`. Your reading was right.

In practice it is usually fewer: the per-instance circuit breaker opens after 5 consecutive
failures and that instance stops being offered at all, so a genuinely dead instance is not
retried indefinitely across requests.

Your current policy — treat `502` as "the reasonable thing was already tried", don't retry
unless the caller asks — matches what the gateway actually did.

### A3 · Which instance does `Retry-After` come from?

**The earliest recovery among the instances whose circuits are open** — a floor across the
group, not one instance's number. If you respect it, by then at least one instance is eligible
to be probed again.

One thing the document didn't say and you should know: **`Retry-After` is absent when the
instances are unreachable rather than circuit-open.** An unreachable backend has no predicted
recovery time — it is discovered by a liveness probe, not by a timer — so we send no header
rather than invent one. **Absence does not mean "retry immediately."** Use your own backoff in
that case.

### A4 · How should a client index a cooldown?

**By model.** `503 backend-unavailable` is now a per-model condition: it means every instance
of *that* model is out, and says so, naming each one. Other models on the same gateway are
unaffected and will serve normally. Cooling by gateway would reject requests we would have
answered.

### A5 · If an instance dies mid-stream, does the gateway fail over?

**No. The stream dies, cleanly, and two generations are never spliced.**

A streamed response is bound to the instance that started it, for its whole life. There is no
mid-stream failover — once bytes are on the wire there is nowhere to go, and splicing is
exactly the outcome we also consider unacceptable.

What you receive on a mid-stream failure:

```
data: {"error": "stream interrupted"}

data: [DONE]
```

The `[DONE]` is always sent, so the stream terminates normally at the protocol level and your
reader doesn't hang. Treat the in-band `error` object as the signal.

### A6 · On retry, should a pin be kept?

**Keep it.** Your instinct matches the design: a pin is explicit and never silently falls back.
A pinned request whose instance is unavailable returns `503`, and it should stay that way on
your retry. Dropping the pin would answer a different question than the caller asked.

---

## B · The response `model` field — you were right, it was a bug

**Fixed.** The body now names the model on every endpoint and on every streamed chunk.

```
request : {"model": "qwen3-0.6b"}
response: {"model": "qwen3-0.6b"}
header  : x-prometheus-instance-id: qwen3-0-6b-iq4-nl-local-1
```

What was happening: llama.cpp echoes its own `--alias`, which is the instance id, and the
gateway passed the body through untouched. You identified the consequence precisely — anyone
attributing cost by `response.model` was billing an identifier absent from the catalog, split
across replica names nobody recognises.

**An alias request is answered with the canonical slug.** Send `qwen3-0-6b-iq4-nl-local-2`, get
back `qwen3-0.6b`. This is deliberate and follows OpenAI, which answers a `gpt-4o` request with
the snapshot it resolved to: the body reports the identity that was actually used, so a
consumer never has to know which spelling the caller happened to use.

The replica remains knowable through the `X-Prometheus-Instance` headers — the place the
contract always said it lives.

---

## C · Stability and meaning

### C1 · Is the `#N` label stable over time?

**Yes, for the life of the instance.** It is stored with the instance, assigned once at
creation, and survives gateway restarts, manager restarts and the instance itself stopping and
starting. `#2` will not become a different machine next week.

One honest caveat: labels are assigned as *highest + 1*, never as "lowest free", precisely so a
removed `#2` isn't handed to a new instance. The one case where a number can return is deleting
the **highest-numbered** instance — delete `#3` of three and the next one created is `#3` again.
Closing that would mean persisting a high-water mark, which we judged not worth it for a label.

So: log the label if you want something readable, but `X-Prometheus-Instance-Id` is the one to
key on. That is why both headers are sent.

### C2 · Is load balancing intended, or is failover all that's promised?

**Balancing is real and shipped** — the document undersold it, which is our fault.

Your six sequential calls landing on one instance is correct behaviour, not a missing feature.
Selection is **least-loaded**, not round-robin: it picks the instance handling the fewest
requests right now, weighted by the concurrent slots each engine reports. A sequential client
has zero requests in flight when each call is selected, so both instances tie and the tie
breaks deterministically — the same instance every time, which *is* the least loaded one.

Under real concurrency it spreads. Measured here: eight concurrent requests to that
two-instance model split 4/4.

The practical consequence for you: a single-threaded caller will keep using one instance, and
that is fine. The second instance is there for concurrency and for failover.

### C3 · `context_length: 0` and `family: ""` — not applicable, or not measured?

Two different things that looked the same. **One is fixed, one is a data gap.**

**`context_length` now returns `null` for image models.** You were right that `0` needs prior
knowledge — it reads as "a window of zero", and a client checking `prompt_tokens <
context_length` would reject every image request. `null` says "no such concept".

> **Contract change.** If your Go or Rust types declare `context_length` as a non-optional
> integer, this will fail to deserialise. It has to become optional. Text, vision and embedding
> models are unaffected — they still return an integer.

**`family: ""` is a data gap, not a type.** Nobody filled it in for two catalog entries. The
field means what it says when populated; the fix is populating it, not changing its shape.

### C4 · Can aliases be discovered?

**No, and there is no endpoint for it.** You haven't missed anything.

Aliases are a migration aid, not part of the catalog: they exist so names you stored before the
rename keep working, and the catalog deliberately advertises only the names you should be
moving to. Two guarantees make a stored old name safe to keep using without checking it:

- **A slug never changes.** Once published it is frozen; a model needing a different name is a
  different model.
- **A slug is never reused.** Retiring a model retires its name with it, so a name you once
  used can never start pointing at a different model.

If a stored name stops resolving, the model was removed — `400 unknown-model`, which is
unambiguous and doesn't need a catalog lookup to interpret.

---

## Summary of what changed on our side

| | |
|---|---|
| Response `model` now names the model, not the replica — all endpoints, all chunks | **fixed, live** |
| An alias request is answered with the canonical slug | **fixed, live** |
| `context_length: null` for image models | **fixed, live — contract change** |
| `Idempotency-Key`, so *your* retry doesn't regenerate (A1) | **shipped — see §D** |
| `family: ""` on two catalog entries | data to fill in, not a code change |

Nothing else in `sdk-changes-2026-09.md` changed. **Block A is fully unblocked**: A2, A3, A5
and A6 confirm the behaviour you had assumed, A1 turns out never to have billed you twice, and
the one case that did — retrying after your own timeout — is what §D closes.

---

## D · `Idempotency-Key`

Send the header on `/v1/chat/completions` (non-streaming), `/v1/embeddings` or
`/v1/images/generations`:

```http
POST /v1/chat/completions
Idempotency-Key: 4f3a1c88-2b6e-4f2a-9c31-7e0d5a1b9f42
```

A replay returns the stored result, marked `Idempotent-Replay: true`, and **does not reach the
model, record usage, or count against a spend cap** — that is the point.

| | |
|---|---|
| Window | 24 hours, as on OpenAI and Anthropic |
| Scope | Per client. Another client's identical key never answers yours |
| Key length | Up to 255 characters |
| Without the header | Nothing changes — deduplication is opt-in |

Four refusals, each with **its own `type`** — they need opposite handling, and only one of
them is ever worth retrying:

| `type` | Status | Cause | Retry? |
|---|---|---|---|
| `invalid-idempotency-key` | 400 | The key is malformed — today, longer than 255 characters | **Never.** Fix the key |
| `idempotency-key-reuse` | 409 | The same key was already used for a different request. The fingerprint covers path *and* body, so a different endpoint counts too | **Never.** Use a fresh key per logical request |
| `idempotency-in-progress` | 409 | The first request with this key is still running | **Yes**, after waiting |
| `idempotency-response-not-retained` | 409 | The original succeeded, but its response exceeded 1 MiB and wasn't kept | **Never.** This tells you the original worked |

A malformed key is a `400`, not a conflict: it never conflicted with anything, and calling it
one would tell you that you had repeated a request when your key simply didn't fit.

Branch on `type`. The `detail` is written for a human and may be reworded.

**A failed request hands its key back.** If the request errored, nothing is stored and the same
key is free — retrying with it is exactly right.

**Streaming is not covered.** Replaying a stream means storing every chunk, and neither OpenAI
nor Anthropic documents that semantics clearly. A key on a streaming request is ignored rather
than refused; retrying a stream remains a new generation.
