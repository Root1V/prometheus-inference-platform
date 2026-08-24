# Decision — llama.cpp runs bare-metal, never containerised

| Field | Value |
|-------|-------|
| **Date** | 2026-03-28 |
| **Status** | Accepted |
| **Deciders** | Prometheus core team |
| **Related spec** | [memory/specs/003-llama-cpp-runtime.md]../specs/003-llama-cpp-runtime.md) |

---

## Context

Prometheus needs to run quantized LLM models efficiently on two hardware environments:

- **Mac M2 (dev)**: 16 GB unified memory, Apple Metal GPU.
- **HPE DL380 × 2 (test)**: 256 GB RAM, 16-core CPU, no discrete GPU.

The question was whether to containerise llama.cpp inside Docker or run it directly on the host.

## Decision

**llama.cpp (`llama-server`) runs directly on the bare-metal host.**
It is never placed inside a Docker container.

## Rationale

### GPU / hardware access

- Containerising llama.cpp on macOS requires passing through Apple Metal, which has no stable
  Docker support. Running bare-metal gives full, direct access to the Metal GPU.
- On the HPE servers (CPU-only), OpenBLAS can saturate all 16 cores with zero container overhead.

### Simplified dependency model

- llama.cpp has complex build requirements (CMake, platform-specific BLAS/Metal flags).
  Building a portable Docker image that works on both Apple Silicon and x86 RHEL is non-trivial
  and couples release cadence to llama.cpp upstream changes.
- Bare-metal install via `runtime/scripts/install-server.sh` is simpler and more transparent.

### Security — network isolation is equivalent

- The gateway (Docker) reaches llama.cpp via `host.docker.internal:8080`.
- llama.cpp binds exclusively to `127.0.0.1` — not reachable from the Docker bridge network
  or any external interface.
- The Gateway enforces all auth, rate limiting, and input sanitisation before any request
  reaches llama.cpp.
- This gives the same security posture as containerising with a host-network bridge,
  without the GPU-passthrough complexity.

## Consequences

- **Positive**: full GPU access with no passthrough complexity.
- **Positive**: simpler build process per platform (Metal on macOS, OpenBLAS on RHEL).
- **Negative**: llama.cpp is a host process — not managed by Podman lifecycle.
- **Negative**: operator must install llama.cpp separately (`install-server.sh`).

## Rejected alternatives

| Alternative | Reason rejected |
|-------------|----------------|
| Docker with GPU passthrough | No stable Apple Metal passthrough; RHEL OpenBLAS is viable but adds complexity |
| Single containerised process | Couples llama.cpp build to container runtime; loses Metal GPU on macOS |
