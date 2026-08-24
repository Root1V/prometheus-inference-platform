#!/usr/bin/env bash
# start.sh — Start the Prometheus Gateway with TLS if configured.
# Reads GATEWAY_TLS_CERT_FILE and GATEWAY_TLS_KEY_FILE from gateway/.env
# (or env vars already exported) and passes them to uvicorn automatically.
#
# Usage (from repo root):
#   bash gateway/start.sh [--port 8000] [--reload] [-- <extra uvicorn args>]
#
# See memory/specs/013-web-chat-ui-proxy.md — AC-14, AC-15, AC-16
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

PORT="${GATEWAY_PORT:-8000}"
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
if [[ -n "${GATEWAY_TLS_CERT_FILE:-}" && -n "${GATEWAY_TLS_KEY_FILE:-}" ]]; then
    if [[ ! -f "$GATEWAY_TLS_CERT_FILE" ]]; then
        echo "ERROR: GATEWAY_TLS_CERT_FILE not found: $GATEWAY_TLS_CERT_FILE" >&2
        echo "       Run: bash gateway/certs/gen-dev-cert.sh" >&2
        exit 1
    fi
    if [[ ! -f "$GATEWAY_TLS_KEY_FILE" ]]; then
        echo "ERROR: GATEWAY_TLS_KEY_FILE not found: $GATEWAY_TLS_KEY_FILE" >&2
        echo "       Run: bash gateway/certs/gen-dev-cert.sh" >&2
        exit 1
    fi
    SSL_ARGS=(--ssl-certfile "$GATEWAY_TLS_CERT_FILE" --ssl-keyfile "$GATEWAY_TLS_KEY_FILE")
    echo "TLS enabled: $GATEWAY_TLS_CERT_FILE"
fi

cd "$SCRIPT_DIR/.."   # repo root so 'uv run' resolves the venv correctly

echo "Starting Prometheus Gateway on port $PORT ..."
exec uv run uvicorn prometheus_gateway.asgi:app \
    --host 0.0.0.0 \
    --port "$PORT" \
    ${RELOAD_FLAG} \
    "${SSL_ARGS[@]+"${SSL_ARGS[@]}"}" \
    "${EXTRA_ARGS[@]+"${EXTRA_ARGS[@]}"}"
