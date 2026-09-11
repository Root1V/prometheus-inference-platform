# Roadmap

Quick-glance list of every feature/change tracked so far. Full detail (why, scope,
tradeoffs) lives in [`docs/roadmap.md`](docs/roadmap.md) — this file is just the index.

Status: `done` · `todo`

| # | Feature | Status | Description |
|---|---------|--------|-------------|
| RM-01 | Restore CI on GitHub Actions | done | `.github/workflows/ci.yml` runs the same checks as the local pre-push hook |
| RM-02 | Extend pre-push hook to manager/telemetry | done | Lint/format/test coverage for `runtime/manager` and `telemetry` |
| RM-03 | Add a real LICENSE | done | Apache-2.0 |
| RM-04 | Dependency vulnerability scanning | done | Dependabot for pip and GitHub Actions deps |
| RM-05 | Split manager into core/api/tui | done | `runtime/manager` split into independent packages |
| RM-06 | Research inference-serving stack | done | llama.cpp vs vLLM vs MLX vs SGLang, per hardware |
| RM-07 | Fine-grained per-model auth scopes | done | `model:<id>` scopes, deny-by-default |
| RM-08 | Distributed inference across hosts | done | Gateway aggregates model availability across manager nodes |
| RM-09 | VLM + embeddings support | done | Vision content parts + `/v1/embeddings` |
| RM-10 | Gateway admin dashboard (phase 1) | done | React SPA — register/edit/start/stop/restart instances |
| RM-11 | Auth & Users dashboard | done | Users section with roles; login via OAuth2 client_id/secret or email+password (default) |
| RM-12 | E2E LLM tracing with Langfuse | todo | Prompt/completion/token-level tracing, alongside existing OTel/Tempo |
| RM-13 | Live log viewer per instance | done | Expand a dashboard row to tail that instance's log |
| RM-14 | Model playground | done | Send test prompts to a running instance from the dashboard |
| RM-15 | Usage: wire up today's per-client totals | done | New Usage page — connects the existing `GET /v1/usage`, no new backend work |
| RM-16 | Routing & rate-limit visibility | done | Surface gateway's rate-limit/circuit-breaker state in the dashboard |
| RM-17 | Guardrails / content filtering | todo | Speculative — no known need yet |
| RM-18 | ~~Teams / multi-user RBAC~~ | merged | Merged into RM-11 |
| RM-19 | Dashboard branding: logo + favicon | done | Icon next to "Prometheus" in the sidebar, reused as the page favicon |
| RM-20 | Node registry | done | Node inventory (name, manager-api URL, hardware type, tag) — replaces MANAGER_NODES as the live routing source |
| RM-22 | Platform overview: page shell + at-a-glance strip | done | New landing page: node/instance/user counts, links to Instances/Nodes/Users |
| RM-23 | Active sessions / connected users | done | Who's connected now (dashboard, chat UI, API, SDK) and to what model |
| RM-24 | Model picker in Create User | done | Pick from the existing Instances/registry list instead of typing `model:<id>` scopes by hand |
| RM-25 | Node SSH/remote-maintenance credentials | todo | Speculative — no consuming feature yet |
| RM-26 | Instances list: numbered, paginated, active-first | done | Add a row-number column, paginate when the list is long, sort running instances first |
| RM-27 | Delete user | done | Permanent delete action in the Users table, alongside the existing deactivate/reactivate |
| RM-28 | Overview: golden signals row | done | Requests/errors/latency p50-p95-p99/circuits-open, live from gateway `/metrics` |
| RM-29 | Overview: models needing attention | done | Instances ⋈ circuit state, sorted unhealthy-first |
| RM-30 | Overview: usage & cost placeholder | done | "Coming soon" card on the Overview page; real version blocked on RM-15 |
| RM-31 | Overview: link out to Grafana/Tempo | done | Needs a `GRAFANA_URL`-style config first — dropped from RM-22 to avoid a fragile guessed link |
| RM-32 | Usage: persisted history + per-model breakdown | done | Replaces Redis daily counters with a real store — needed for any trend chart |
| RM-33 | Usage: pricing table + real cost | done | Per-model price config; turns token counts into a dollar figure |
| RM-34 | Overview: wire the usage & cost card to real data | done | Replaces RM-30's placeholder once RM-32/33 land |
| RM-35 | Native tool-calling (OpenAI-style function calling) | done | `tools`/`tool_calls` on `/v1/chat/completions` — new backend surface, not just a UI |
| RM-36 | Playground: streaming responses | done | Gateway already supports `stream:true`; Playground deliberately shipped non-streaming first |
| RM-37 | Playground: embedding model testing | done | New "Embeddings" tab — single input, vector preview + dims; also fixed a missing admin:write bypass on `/v1/embeddings` |
| RM-38 | Image generation model support | done | New `sd_cpp` backend (stable-diffusion.cpp) + `image` modality, gateway endpoint, Playground UI |
| RM-39 | Video generation model support | todo | Speculative — same as RM-38, even less proven for self-hosted use |
| RM-40 | Playground: image upload for Vision/VLM models | done | Builds on RM-09's vision content parts; also fixed Chat's model picker excluding vision models entirely |
| RM-41 | Playground: show which model answered | done | Small label next to the copy button per response — matters once you switch models mid-conversation |
| RM-42 | Playground: animate the "waiting for a response" state | done | Replace the static text with something that reads as active waiting |
| RM-43 | Stop stripping client-supplied system messages | done | Found while scoping RM-35 — broke the RM-14 Playground's own System prompt field |
| RM-44 | Dashboard: light/dark mode, auto-detected + manual toggle | done | 3-way sidebar toggle (Light/System/Dark), live OS-preference sync, persisted choice, no per-component changes needed |
| RM-45 | Let a client list which models it's actually allowed to use | done | New `GET /v1/models/mine` — authenticated, filtered to the caller's own `model:<id>` grants |
| RM-46 | Per-model performance metrics: avg response time, TTFT, inter-token latency | done | New per-backend MetricsStore fields + a Latency column on Instances, verified live against a real request |
| RM-47 | Evaluate whether `GET /v1/models` should stay unauthenticated | todo | Raised while building RM-45 — confirm nothing depends on public access before considering any change |
| RM-48 | Dashboard: Models page — discover, download, and manage the model lifecycle | done | Search Hugging Face, read the model card, download with live progress/cancel/retry, delete removes the file too |
| RM-49 | Model registry: migrate registry.yaml → SQLite | done | Fixes a real non-atomic-write bug; sync stdlib sqlite3 (no async, no new dependency); dropped dead `log_level`/redundant `backend_url` fields, consolidated `hf_filename`/`hf_filenames` |
| RM-50 | fix: Registry data-quality cleanup — ghost downloads, port collision, size-undercounted shards | done | Deleted 3 broken entries + 1 orphan partial file, fixed the port collision, fixed `_file_size_bytes()` to sum all shards for manually-registered multi-part models |
| RM-51 | fix: Separate the model catalog from running instances; confirm before deleting a model | done | `registry.db` split into `models` (catalog) + `instances` tables; deleting an instance no longer deletes the catalog entry; cascade model-delete requires `confirm=true` when instances are live |
| RM-52 | sd_cpp: support split-file diffusion models (FLUX.1, SD3.5) | done | New `vae_path`/`clip_l_path`/`t5xxl_path`/`cfg_scale` fields; also fixed sd-server's wrong cfg-scale default and a too-short gateway backend timeout |
| RM-53 | Playground: unify Chat/Embeddings/Images into one adaptive chat | done | One composer + one persistent mixed-content timeline whose config switches based on the selected model's modality; new `PlaygroundModelPicker` groups all ready instances by modality |
| RM-54 | Audio/music generation support (text-to-audio, style transfer) | todo | New modality — prompt-to-music and audio-to-audio (upload a track, restyle or continue it); backend choice needs its own research pass, same as RM-38 |
| RM-55 | Richer multi-instance-per-model management UX | todo | RM-51 made multi-instance possible; this adds the polished UI (per-model instance list, port-conflict-aware picker) deliberately deferred from that PR |
| RM-56 | Admin dashboard: edit rate limits live, no restart | done | Editable on the Limits page, applied to the very next request (the middleware already re-read Settings per request) and persisted in a `rate_limit_config` row; "Reset to .env" restores the startup values, and an admin-RPM floor stops an operator locking themselves out of the page |
| RM-57 | Multi-instance-per-model: a shared logical name for routing | done | The catalog `model_id` RM-51 already ships per instance becomes the routable name: instances sharing it are replicas, addressable as one model. Instance ids stay addressable; usage/pricing follow the requested name, metrics/circuit breaker stay per replica |
| RM-58 | Gateway: intelligent load balancing across instances of the same model | superseded | Absorbed by RM-72 — load balancing can't be designed independently of the identity model settled in `docs/model-identity-proposal.md` |
| RM-59 | Dashboard: search/filter for Models and Instances | done | Shared `TableSearchInput` above both tables — client-side filter over data already in memory, no new endpoint; distinct "no matches" empty state, and a match count while filtering |
| RM-60 | Billing: real per-client cost, multi-currency display, tax, dashboard, budget alerts | done | Fixed a retroactive-repricing bug (cost now stored at write time, not recomputed later); added an audit trail, CSV export, PEN/USD/EUR display, a tax line, a hard cap + soft alert thresholds, and a billing dashboard; real card charging stays deferred |
| RM-61 | Peru/SUNAT e-invoicing compliance for individual (B2C) clients | todo | Blocked on the user's own business/legal decision (bill Peru individuals at all? which e-invoicing provider?) — not something to silently code under RM-60 |
| RM-62 | Cost-based model price suggestion | done | Per-node $/hour field, a real prefill (prompt) tokens/sec metric, and a "Suggest price" calculator button in the Model Pricing table — break-even = node $/hour ÷ observed throughput, with an editable margin |
| RM-63 | Prepaid credits/quotas | todo | Clients buy a credit balance upfront and draw it down using models, topping up whenever it runs out; coexists with RM-60's post-paid billing — needs real payment collection (card, Yape/Plin, manual bank transfers), which RM-60 deliberately left out |
| RM-64 | Tiered model access by plan | todo | A purchased plan grants a default bundle of the existing `model:<id>` scopes automatically; admin can still add/remove individual grants on top — split out of RM-63, direction confirmed after industry research |
| RM-65 | fix: 422 validation errors break the RFC 9457 error contract | done | FastAPI's default validation-error handler returned `application/json`/`{"detail": [...]}` instead of the gateway's own problem+json envelope every other error uses — found live by the Axonium SDK integration team; now wrapped in the same envelope, per-field detail preserved under `errors` |
| RM-66 | fix: chat completions accepted a wrong-modality model | done | An embedding/image model called via `/v1/chat/completions` returned 200 with garbage output instead of 400 — the modality check only fired when an image content part was present, never as a baseline gate; `/v1/embeddings`/`/v1/images/generations` already rejected the wrong modality unconditionally, chat completions didn't — found live by the Axonium SDK integration team |
| RM-67 | Admin dashboard: edit circuit-breaker thresholds live | done | Same treatment as RM-56 for failure/recovery/success thresholds — but these needed an explicit push into BackendPool and every live CircuitBreaker, which hold their own copies instead of re-reading Settings per request |

| RM-68 | Alembic migrations for the gateway database | done | `create_tables()` now runs migrations; a database predating Alembic is lifted to the baseline and stamped rather than re-created, so RM-70 can alter tables holding real usage and billing rows. A drift test fails if a model changes without its migration |
| RM-69 | Replica failover, active health checks, and group-based pricing | done | Today a replica adds neither throughput nor fault tolerance: the circuit breaker is checked only on the deterministic pick, retries hammer the same dead instance, and routing by instance id escapes both pricing and the spend cap |
| RM-70 | Model/instance identity: immutable slug, opaque ids, per-model label | in-progress | Manager-side identity columns landed: catalog `slug`/`name`/`modality` plus per-model instance `label`, backfilled with every existing id preserved. Gateway/UI switchover and the opaque-id primary keys are still pending |
| RM-71 | Replica UX: "Add instance" flow and a model-level scope picker | in-progress | Creating a replica should ask only node/engine/port and auto-increment the label; the scope picker should list models with a replica-count pill, never individual instances |
| RM-72 | Gateway: intelligent load balancing across replicas | in-progress | Absorbs RM-58. Least-in-flight shipped and verified live (6 concurrent requests split 3/3 across two replicas); queue/slot-aware and session affinity still pending. Least-in-flight first (engine-agnostic), then free-slot/queue-aware from llama.cpp's `/slots` and `/metrics`, then optional session affinity — strategy configurable per model |
| RM-73 | Billing traceability per replica + per-model metrics rollup | todo | `usage_events` records which replica served alongside the model id and slug; metrics gain a per-model aggregate so the dashboard can show a model's throughput rather than N loose instance rows |

Adding an item: append the next `RM-NN` row here with a one-liner, then add the full
Why/Scope writeup to `docs/roadmap.md`.
