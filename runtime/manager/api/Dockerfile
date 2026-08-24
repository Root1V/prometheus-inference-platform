# runtime/manager/api/Dockerfile
# Implements: specs/008-llama-server-manager.md — AC-11 (Manager REST API)
# See: memory/roadmap.md — RM-05 (split from the single prometheus-manager package)
#
# Runs ONLY the Manager REST API (pmgr-api). No Textual/Rich/click-TUI deps —
# those live in the separate prometheus-manager-tui package, installed on
# bare-metal only (see runtime/manager/tui/).
# The TUI runs on bare-metal and writes to registry.yaml, which is
# bind-mounted into this container at /data/registry.yaml.
#
# Build (from repo root — needs the whole workspace for `uv sync`):
#   podman build -f runtime/manager/api/Dockerfile -t prometheus-manager-api .
#
# Run (standalone, for testing):
#   podman run --rm -p 8090:8090 \
#     -v ./runtime/manager/registry.yaml:/data/registry.yaml:ro \
#     -e PMGR_PROXY_HOST=host.containers.internal \
#     -e PMGR_JWKS_URL=http://host.containers.internal:9000/v1/jwks \
#     prometheus-manager-api

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
COPY runtime/manager/core/pyproject.toml runtime/manager/core/
COPY runtime/manager/api/pyproject.toml runtime/manager/api/
COPY runtime/manager/tui/pyproject.toml runtime/manager/tui/
COPY gateway/pyproject.toml gateway/
COPY auth-service/pyproject.toml auth-service/
COPY telemetry/pyproject.toml telemetry/

# Sync all 3rd-party dependencies (no workspace packages yet).
RUN uv sync --frozen --no-dev --no-install-workspace

# Copy the two packages this image actually needs — NOT tui (keeps
# Textual/Rich, and their transitive deps, out of the API image entirely).
COPY runtime/manager/core/src ./runtime/manager/core/src
COPY runtime/manager/api/src ./runtime/manager/api/src
# Copy telemetry source — workspace dep of prometheus-manager-core.
COPY telemetry/src ./telemetry/src

# Install only the manager-api package (and its prometheus-manager-core
# dependency) in non-editable mode.
RUN uv sync --frozen --no-dev --no-editable --package prometheus-manager-api

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

CMD ["pmgr-api"]
