# Prometheus — Specs

This directory contains all feature specifications following **Spec Driven Development (SDD)**.

## Spec Lifecycle

```
[draft] → [review] → [approved] → [in-progress] → [implemented] → [closed]
```

No feature is implemented without a spec in `approved` status.

## Index

| # | Spec | Status | Description |
|---|------|--------|-------------|
| 001 | [Gateway Core](001-gateway-core.md) | `implemented` | Core gateway setup: request routing, health, and llama.cpp proxy |
| 002 | [JWT Authentication Middleware](002-jwt-authentication-middleware.md) | `implemented` | RS256 JWT validation, JWKS rotation, token revocation |
| 003 | [llama.cpp Bare-Metal Runtime Setup](003-llama-cpp-runtime.md) | `implemented` | Install, compile, and run the llama.cpp inference server on Mac M2 (Metal) and RHEL 9.7 (OpenBLAS) |
| 004 | [Podman Containerization of the Gateway](004-podman-containerization.md) | `implemented` | Dockerfile + podman-compose.yml for the gateway and Redis; RHEL 9.7 rootless deployment |
| 005 | [Authentication & Authorization Service](005-auth-service.md) | `implemented` | Standalone OAuth2 server: client credentials grant, RS256 JWT issuance, client registry, JWKS endpoint |
| 006 | [Multi-Model Backend Routing](006-multi-model-routing.md) | `implemented` | BackendPool, per-model routing, /v1/models endpoint, /v1/backends admin endpoint |
| 007 | [Rate Limiting & Throughput Optimisation](007-rate-limiting-and-throughput.md) | `draft` | Sliding-window RPM/TPM limits, per-backend concurrency cap, JWKS Redis cache, structured metering |

## Creating a New Spec

Use the `/new-spec` prompt in GitHub Copilot Chat, or use the `spec-writer` agent.

The next available number is determined by the highest existing `NNN` in this directory — currently **008**.

## Spec Format

See `.github/instructions/sdd.instructions.md` for the full template and rules.
