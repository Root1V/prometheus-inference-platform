# gateway/Dockerfile
# Implements: specs/004-podman-containerization.md — AC-1, AC-2, AC-3, AC-9
#
# Multi-stage build:
#   Stage 1 (builder): install uv and sync dependencies into /app/.venv
#   Stage 2 (runtime): copy only app source + venv; run as non-root user
#
# Build:
#   podman build -f gateway/Dockerfile -t prometheus-gateway .
#
# Run (standalone, for testing):
#   podman run --rm -p 8000:8000 --env-file gateway/.env prometheus-gateway

# ── Stage 1: builder ──────────────────────────────────────────────────────────
FROM python:3.13-slim AS builder

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /build

# Build the venv at /app/.venv so all venv script shebangs reference /app/.venv/bin/python.
# If built at /build/.venv, shebangs would embed /build/... paths that don't exist at runtime
# after the COPY --from=builder /build/.venv /app/.venv step, causing
# "No such file or directory" when the container tries to exec uvicorn.
ENV UV_PROJECT_ENVIRONMENT=/app/.venv

# Copy dependency manifests first for layer caching.
# All workspace members must be present for uv to resolve the full graph.
COPY pyproject.toml uv.lock ./
COPY gateway/pyproject.toml gateway/
COPY runtime/manager/pyproject.toml runtime/manager/
COPY auth-service/pyproject.toml auth-service/
COPY telemetry/pyproject.toml telemetry/

# Sync all dependencies into an isolated venv at /app/.venv
# --frozen: never update uv.lock inside the build
# --no-dev: skip dev dependencies (pytest, ruff, mypy, etc.)
# --no-install-workspace: install only 3rd-party deps, NOT the gateway package yet
#   (gateway/src doesn't exist at this point — copy happens next)
RUN uv sync --frozen --no-dev --no-install-workspace

# Copy application source.
COPY gateway/src ./gateway/src
# Copy telemetry source — workspace dep of prometheus-gateway.
COPY telemetry/src ./telemetry/src

# Install only the gateway package in non-editable mode.
# --package ensures other workspace members are not installed.
RUN uv sync --frozen --no-dev --no-editable --package prometheus-gateway

# ── Stage 2: runtime ──────────────────────────────────────────────────────────
# AC-3: runtime stage contains no uv, pip, setuptools, or build tools
FROM python:3.13-slim AS runtime

# AC-2, AC-7: create non-root user prometheus (uid 1000)
RUN groupadd --gid 1000 prometheus && \
    useradd --uid 1000 --gid 1000 --no-create-home --shell /sbin/nologin prometheus

# AC-3: remove pip and setuptools from the runtime layer — the app runs
# entirely from /app/.venv; no package installation should be possible at runtime
RUN pip uninstall pip setuptools wheel -y 2>/dev/null || true

WORKDIR /app

# Copy only the venv and app source from the builder stage
# AC-9: no key material, .env files, or secrets — only source + deps
COPY --from=builder --chown=prometheus:prometheus /app/.venv /app/.venv
COPY --from=builder --chown=prometheus:prometheus /build/gateway/src /app/src

# Entrypoint: starts uvicorn with optional TLS (specs/013-web-chat-ui-proxy.md — AC-14)
COPY --chown=prometheus:prometheus gateway/docker-entrypoint.sh /app/docker-entrypoint.sh
RUN chmod +x /app/docker-entrypoint.sh

# Model registry is mounted at runtime; provide the default path as env var
ENV PYTHONPATH="/app/src" \
    PATH="/app/.venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MODEL_REGISTRY_PATH="/app/config/registry.yaml"

# Expose gateway port (unprivileged — uid 1000 can bind to 8000)
EXPOSE 8000

# AC-2: switch to prometheus user before the entrypoint
USER prometheus

# Health check for Podman / compose
# AC-5: /health must respond within 30 s of container start
# Uses https:// when GATEWAY_TLS_CERT_FILE is set (specs/013 — AC-14)
HEALTHCHECK --interval=10s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "\
import urllib.request, ssl, os; \
url = ('https' if os.getenv('GATEWAY_TLS_CERT_FILE') else 'http') + '://localhost:8000/health'; \
ctx = ssl.create_default_context(); ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE; \
urllib.request.urlopen(url, context=ctx)" || exit 1

# Start the gateway — entrypoint adds --ssl-certfile/--ssl-keyfile if TLS env vars are set
# Implements: specs/004-podman-containerization.md — AC-1
# Implements: specs/013-web-chat-ui-proxy.md — AC-14
CMD ["sh", "/app/docker-entrypoint.sh"]
