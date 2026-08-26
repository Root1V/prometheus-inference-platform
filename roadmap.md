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
| RM-13 | Live log viewer per instance | todo | Expand a dashboard row to tail that instance's log |
| RM-14 | Model playground | todo | Send test prompts to a running instance from the dashboard |
| RM-15 | Usage & spend analytics | todo | Per-model/per-client token & request usage in the dashboard |
| RM-16 | Routing & rate-limit visibility | todo | Surface gateway's rate-limit/circuit-breaker state in the dashboard |
| RM-17 | Guardrails / content filtering | todo | Speculative — no known need yet |
| RM-18 | ~~Teams / multi-user RBAC~~ | merged | Merged into RM-11 |
| RM-19 | Dashboard branding: logo + favicon | done | Icon next to "Prometheus" in the sidebar, reused as the page favicon |
| RM-20 | Node registry | todo | Register nodes (IP/DNS, Mac/Nvidia, connection creds, name, tag) |
| RM-21 | Simplified instance creation | todo | Pick node + model (auto-filled from discovery); port auto-assigned |
| RM-22 | Platform overview / home page | todo | Landing page summarizing overall platform state |
| RM-23 | Active sessions / connected users | todo | Who's connected now (dashboard, chat UI, API, SDK) and to what model |
| RM-24 | Model picker in Create User | todo | Pick from discovered models instead of typing `model:<id>` scopes by hand |

Adding an item: append the next `RM-NN` row here with a one-liner, then add the full
Why/Scope writeup to `docs/roadmap.md`.
