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
| RM-35 | Native tool-calling (OpenAI-style function calling) | todo | `tools`/`tool_calls` on `/v1/chat/completions` — new backend surface, not just a UI |
| RM-36 | Playground: streaming responses | todo | Gateway already supports `stream:true`; Playground deliberately shipped non-streaming first |
| RM-37 | Playground: embedding model testing | todo | `/v1/embeddings` already exists (RM-09); Playground only lets you pick text models today |
| RM-38 | Image generation model support | todo | Speculative — new modality, new backend integration, no known model/backend chosen yet |
| RM-39 | Video generation model support | todo | Speculative — same as RM-38, even less proven for self-hosted use |
| RM-40 | Playground: image upload for Vision/VLM models | todo | Builds on RM-09's vision content parts; upload only enabled when the selected model's modality is vision |
| RM-41 | Playground: show which model answered | todo | Small label next to the copy button per response — matters once you switch models mid-conversation |
| RM-42 | Playground: animate the "waiting for a response" state | todo | Replace the static text with something that reads as active waiting |

Adding an item: append the next `RM-NN` row here with a one-liner, then add the full
Why/Scope writeup to `docs/roadmap.md`.
