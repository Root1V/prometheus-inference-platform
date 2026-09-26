# Prometheus — Specs

**This is a historical record, not a live process — read this paragraph before the
next one.** These 24 specs were written under Spec Driven Development, which commit
`546a196` retired in favour of the lightweight roadmap workflow described in
`CLAUDE.md`: a roadmap entry and a branch per item, no spec. New work does not add
a spec here.

They were restored in `PRM-148` because **288 `Implements:` comments in the source
cite them**, along with the acceptance criteria they number (`AC-8`, `AC-27`). With
this directory deleted every one of those citations was unverifiable — the code
said which requirement it satisfied and the requirement was gone. That is what
these documents are for now: reading what a piece of code was built to do, and why.
`scripts/check_spec_references.py` fails the push if a reference stops resolving.

There is no spec 019; the number was skipped when the corpus was written, and
nothing is missing.

## Spec Lifecycle (as it was)

```
[draft] → [review] → [approved] → [in-progress] → [implemented] → [closed]
```

Under that process, no feature was implemented without a spec in `approved` status.
Every spec below reached `implemented`.

## Index

| # | Spec | Status | Description |
|---|------|--------|-------------|
| 001 | [Gateway Core](001-gateway-core.md) | `implemented` | Core gateway setup: request routing, health, and llama.cpp proxy |
| 002 | [JWT Authentication Middleware](002-jwt-authentication-middleware.md) | `implemented` | RS256 JWT validation, JWKS rotation, token revocation |
| 003 | [llama.cpp Bare-Metal Runtime Setup](003-llama-cpp-runtime.md) | `implemented` | Install, compile, and run the llama.cpp inference server on Mac M2 (Metal) and RHEL 9.7 (OpenBLAS) |
| 004 | [Podman Containerization of the Gateway](004-podman-containerization.md) | `implemented` | Dockerfile + podman-compose.yml for the gateway and Redis; RHEL 9.7 rootless deployment |
| 005 | [Authentication & Authorization Service](005-auth-service.md) | `implemented` | Standalone OAuth2 server: client credentials grant, RS256 JWT issuance, client registry, JWKS endpoint |
| 006 | [Multi-Model Backend Routing](006-multi-model-gateway.md) | `implemented` | BackendPool, per-model routing, /v1/models endpoint, /v1/backends admin endpoint |
| 007 | [Rate Limiting & Throughput Optimisation](007-rate-limiting-and-throughput.md) | `draft` | Sliding-window RPM/TPM limits, per-backend concurrency cap, JWKS Redis cache, structured metering |

## Creating a New Spec

Use the `/new-spec` prompt in GitHub Copilot Chat, or use the `spec-writer` agent.

The next available number is determined by the highest existing `NNN` in this directory — currently **008**.

## Spec Format

See `.github/instructions/sdd.instructions.md` for the full template and rules.
