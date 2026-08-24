# auth-service/Dockerfile
# Implements: specs/005-auth-service.md
#
# Multi-stage build — mirrors the gateway/Dockerfile pattern (specs/004).
# Build from repo root:
#   podman build -f auth-service/Dockerfile -t prometheus-auth .

# ── Stage 1: builder ──────────────────────────────────────────────────────────
FROM python:3.13-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /build

ENV UV_PROJECT_ENVIRONMENT=/app/.venv

# Copy root workspace manifests for all members so uv can resolve the full graph.
# auth-service is now a uv workspace member (specs/020) and depends on
# prometheus-telemetry, so we must use the root uv.lock, not a standalone one.
COPY pyproject.toml uv.lock ./
COPY auth-service/pyproject.toml auth-service/
COPY gateway/pyproject.toml gateway/
COPY runtime/manager/pyproject.toml runtime/manager/
COPY telemetry/pyproject.toml telemetry/

# Sync all 3rd-party dependencies (workspace packages excluded).
RUN uv sync --frozen --no-dev --no-install-workspace

# Copy application source.
COPY auth-service/src ./auth-service/src
# Copy telemetry source — workspace dep of prometheus-auth.
COPY telemetry/src ./telemetry/src

# Install only the auth-service package in non-editable mode.
RUN uv sync --frozen --no-dev --no-editable --package prometheus-auth

# ── Stage 2: runtime ──────────────────────────────────────────────────────────
FROM python:3.13-slim AS runtime

RUN groupadd --gid 1001 prometheus-auth && \
    useradd --uid 1001 --gid 1001 --no-create-home --shell /sbin/nologin prometheus-auth

RUN pip uninstall pip setuptools wheel -y 2>/dev/null || true

WORKDIR /app

COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /build/auth-service/src ./src

# Writable directory for the SQLite database (non-root user needs write access)
RUN mkdir -p /data && chown prometheus-auth:prometheus-auth /data

ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONPATH="/app/src"
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Copy entrypoint before switching to non-root user so root can set ownership and permissions.
# Mirrors the gateway/Dockerfile pattern (--chown + chmod before USER).
# Implements: specs/017-auth-service-tls.md — AC-10
COPY --chown=prometheus-auth:prometheus-auth auth-service/docker-entrypoint.sh /app/docker-entrypoint.sh
RUN chmod 0755 /app/docker-entrypoint.sh

USER prometheus-auth

EXPOSE 9000

# AC-11: HEALTHCHECK reads AUTH_TLS_CERT_FILE at runtime to choose http vs https
HEALTHCHECK --interval=10s --timeout=5s --retries=3 --start-period=20s \
    CMD python -c "import urllib.request, ssl, os; url = ('https' if os.getenv('AUTH_TLS_CERT_FILE') else 'http') + '://localhost:9000/health'; ctx = ssl.create_default_context(); ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE; urllib.request.urlopen(url, context=ctx)"

CMD ["sh", "/app/docker-entrypoint.sh"]
