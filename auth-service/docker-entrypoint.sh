#!/bin/sh
# docker-entrypoint.sh — Start the Prometheus Auth Service with optional TLS.
# Reads AUTH_TLS_CERT_FILE and AUTH_TLS_KEY_FILE from the container env.
# When both are set, uvicorn is started in HTTPS mode.
#
# Implements: memory/specs/017-auth-service-tls.md — AC-1, AC-2, AC-3
set -e

SSL_ARGS=""
if [ -n "${AUTH_TLS_CERT_FILE:-}" ] && [ -n "${AUTH_TLS_KEY_FILE:-}" ]; then
    echo "TLS enabled: ${AUTH_TLS_CERT_FILE}"
    SSL_ARGS="--ssl-certfile ${AUTH_TLS_CERT_FILE} --ssl-keyfile ${AUTH_TLS_KEY_FILE}"
fi

# shellcheck disable=SC2086
exec python -m uvicorn prometheus_auth.asgi:app \
    --host 0.0.0.0 \
    --port 9000 \
    --no-access-log \
    $SSL_ARGS
