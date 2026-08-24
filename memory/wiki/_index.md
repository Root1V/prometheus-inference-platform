# Prometheus Wiki — Content Catalog

Entry point to all wiki pages. Updated by `docs-agent` when pages are added, removed, or substantially changed.

---

## System & operations

| Page | Description |
|------|-------------|
| [architecture.md](architecture.md) | System overview · C4 L1+L2 diagrams · component responsibilities · threat model · request lifecycle |
| [deployment.md](deployment.md) | Full stack startup/stop · env vars · macOS and RHEL procedures · troubleshooting |
| [inference-server-startup.md](inference-server-startup.md) | Runbook: start/stop/verify `llama-server` on macOS (Metal) and RHEL (OpenBLAS) |
| [observability.md](observability.md) | Canonical log schema · `trace_id` propagation · privacy rules · `GET /metrics` · Langfuse field mapping |

## Authentication & security

| Page | Description |
|------|-------------|
| [auth-model.md](auth-model.md) | OAuth2 Client Credentials flow · client roles · scopes · JWT structure · gateway validation order · revocation |
| [key-rotation.md](key-rotation.md) | RS256 key pair rotation procedure · JWKS multi-key · compromised key response |
| [web-chat-ui.md](web-chat-ui.md) | Browser auth flow · session cookie · `ui:chat` scope · TLS requirement · security rules · env vars |

## Inference & models

| Page | Description |
|------|-------------|
| [model-registry.md](model-registry.md) | registry.yaml schema · request routing · discovery flag lifecycle · capacity warnings · port assignments |
| [rate-limiting.md](rate-limiting.md) | Sliding-window RPM/TPM limits · Redis key layout · circuit breaker · strict mode · observability fields |
| [inference-engines.md](inference-engines.md) | llama.cpp vs vLLM vs MLX vs SGLang per hardware (Mac/DGX Spark/Linux) · recommendation for RM-08/RM-09 |

---

## Decisions

Important cross-cutting decisions that shaped the architecture. Full list in [`memory/decisions/`]../decisions/).

| File | Decision |
|------|---------|
| [2026-03-28-llama-cpp-bare-metal.md]../decisions/2026-03-28-llama-cpp-bare-metal.md) | llama.cpp runs bare-metal, never containerised |
| [2026-03-28-rs256-jwt.md]../decisions/2026-03-28-rs256-jwt.md) | RS256 JWT for all authentication — never HS256 |
| [2026-03-28-podman-over-docker.md]../decisions/2026-03-28-podman-over-docker.md) | Podman as sole container runtime — required by RHEL 9.7 |
| [2026-03-28-redis-for-state.md]../decisions/2026-03-28-redis-for-state.md) | Redis for rate-limit counters, revocation cache, and JWKS sharing |
| [2026-03-28-openai-api-compatibility.md]../decisions/2026-03-28-openai-api-compatibility.md) | Gateway preserves OpenAI Chat Completions API shape — clients use standard SDKs |
| [2026-05-03-manager-owns-registry.md]../decisions/2026-05-03-manager-owns-registry.md) | Manager API is the source of truth for model registry — not the gateway |
| [2026-05-09-canonical-project-dir.md]../decisions/2026-05-09-canonical-project-dir.md) | Use `/opt/prometheus-ai-inference/` as the canonical project directory on RHEL hosts |

---

## Related folders

| Folder | Description |
|--------|-------------|
| [`memory/specs/`]../specs/) | SDD specifications — source of truth for every feature |
| [`memory/roadmap.md`]../roadmap.md) | Prioritized backlog of improvements/new features — lightweight, non-SDD |

## Hot context

See [_hot.md](_hot.md) for what is actively changing, open questions, and recent decisions.
