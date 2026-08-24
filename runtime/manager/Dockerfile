# runtime/manager/Dockerfile
# Implements: specs/008-llama-server-manager.md — AC-11 (Manager REST API)
#
# Runs ONLY the Manager REST API (pmgr serve).
# The TUI runs on bare-metal and writes to registry.yaml, which is
# bind-mounted into this container at /data/registry.yaml.
#
# Build:
#   podman build -f runtime/manager/Dockerfile -t prometheus-manager .
#
# Run (standalone, for testing):
#   podman run --rm -p 8090:8090 \
#     -v ./runtime/manager/registry.yaml:/data/registry.yaml:ro \
#     -e PMGR_PROXY_HOST=host.containers.internal \
#     -e PMGR_JWKS_URL=http://host.containers.internal:9000/v1/jwks \
#     prometheus-manager

# ── Stage 1: builder ──────────────────────────────────────────────────────────
FROM python:3.13-slim AS builder

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /build

# Build the venv at /app/.venv so shebangs reference the correct runtime path.
ENV UV_PROJECT_ENVIRONMENT=/app/.venv

# Copy dependency manifests first for layer caching.
# All workspace members must be present for uv to resolve the graph.
COPY pyproject.toml uv.lock ./
COPY runtime/manager/pyproject.toml runtime/manager/
COPY gateway/pyproject.toml gateway/
COPY auth-service/pyproject.toml auth-service/
COPY telemetry/pyproject.toml telemetry/

# Sync all 3rd-party dependencies (no workspace packages yet).
RUN uv sync --frozen --no-dev --no-install-workspace

# Copy manager application source.
COPY runtime/manager/src ./runtime/manager/src
# Copy telemetry source — workspace dep of prometheus-manager.
COPY telemetry/src ./telemetry/src

# Install only the manager package in non-editable mode.
RUN uv sync --frozen --no-dev --no-editable --package prometheus-manager

# ── Stage 2: runtime ──────────────────────────────────────────────────────────
FROM python:3.13-slim AS runtime

# Non-root user (uid 1002 — gateway=1000, auth-service=1001, manager=1002)
RUN groupadd --gid 1002 pmgr && \
    useradd --uid 1002 --gid 1002 --no-create-home --shell /sbin/nologin pmgr

# Remove pip/setuptools — no installs at runtime
RUN pip uninstall pip setuptools wheel -y 2>/dev/null || true

WORKDIR /app

COPY --from=builder --chown=pmgr:pmgr /app/.venv /app/.venv

# /data is the mount point for registry.yaml (bind-mounted from host at runtime)
RUN mkdir /data && chown pmgr:pmgr /data

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Default env vars — overridden by podman-compose.yml
# PMGR_REGISTRY_PATH : path to registry.yaml inside the container
# PMGR_PROXY_HOST    : host alias for reaching llama-server from inside the container
# PMGR_JWKS_URL      : auth-service JWKS endpoint (internal network)
ENV PMGR_REGISTRY_PATH=/data/registry.yaml \
    PMGR_PROXY_HOST=host.containers.internal \
    PMGR_JWKS_URL=http://auth-service:9000/v1/jwks

# Manager REST API port
EXPOSE 8090

USER pmgr

CMD ["pmgr", "serve"]
