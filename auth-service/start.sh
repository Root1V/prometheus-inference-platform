#!/usr/bin/env bash
# auth-service/start.sh — Start the Prometheus Auth Service with TLS if configured.
# Reads AUTH_TLS_CERT_FILE and AUTH_TLS_KEY_FILE from auth-service/.env
# (or env vars already exported) and passes them to uvicorn automatically.
#
# Usage (from repo root):
#   bash auth-service/start.sh [--port 9000] [--reload] [-- <extra uvicorn args>]
#
# See memory/specs/017-auth-service-tls.md — AC-4, AC-5, AC-6, AC-7
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$SCRIPT_DIR/.env"

# Source .env if it exists (without exporting shell builtins)
if [[ -f "$ENV_FILE" ]]; then
    set -o allexport
    # shellcheck disable=SC1090
    source "$ENV_FILE"
    set +o allexport
fi

PORT="${AUTH_PORT:-9000}"
EXTRA_ARGS=()
RELOAD_FLAG=""

# Parse simple flags
while [[ $# -gt 0 ]]; do
    case "$1" in
        --port)   PORT="$2"; shift 2 ;;
        --reload) RELOAD_FLAG="--reload"; shift ;;
        --)       shift; EXTRA_ARGS+=("$@"); break ;;
        *)        EXTRA_ARGS+=("$1"); shift ;;
    esac
done

SSL_ARGS=()
if [[ -n "${AUTH_TLS_CERT_FILE:-}" && -n "${AUTH_TLS_KEY_FILE:-}" ]]; then
    if [[ ! -f "$AUTH_TLS_CERT_FILE" ]]; then
        echo "ERROR: AUTH_TLS_CERT_FILE not found: $AUTH_TLS_CERT_FILE" >&2
        echo "       Run: bash auth-service/certs/gen-dev-cert.sh" >&2
        exit 1
    fi
    if [[ ! -f "$AUTH_TLS_KEY_FILE" ]]; then
        echo "ERROR: AUTH_TLS_KEY_FILE not found: $AUTH_TLS_KEY_FILE" >&2
        echo "       Run: bash auth-service/certs/gen-dev-cert.sh" >&2
        exit 1
    fi
    SSL_ARGS=(--ssl-certfile "$AUTH_TLS_CERT_FILE" --ssl-keyfile "$AUTH_TLS_KEY_FILE")
    echo "TLS enabled: $AUTH_TLS_CERT_FILE"
fi

cd "$SCRIPT_DIR/.."   # repo root so 'uv run' resolves the venv correctly

echo "Starting Prometheus Auth Service on port $PORT ..."
exec uv run uvicorn prometheus_auth.asgi:app \
    --host 0.0.0.0 \
    --port "$PORT" \
    ${RELOAD_FLAG} \
    "${SSL_ARGS[@]+"${SSL_ARGS[@]}"}" \
    "${EXTRA_ARGS[@]+"${EXTRA_ARGS[@]}"}"
