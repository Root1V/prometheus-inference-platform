#!/bin/sh
# docker-entrypoint.sh — Start the Prometheus Gateway with optional TLS.
# Reads GATEWAY_TLS_CERT_FILE and GATEWAY_TLS_KEY_FILE from the container env.
# When both are set, uvicorn is started in HTTPS mode.
#
# Implements: memory/specs/013-web-chat-ui-proxy.md — AC-14, AC-15
set -e

SSL_ARGS=""
if [ -n "${GATEWAY_TLS_CERT_FILE:-}" ] && [ -n "${GATEWAY_TLS_KEY_FILE:-}" ]; then
    echo "TLS enabled: ${GATEWAY_TLS_CERT_FILE}"
    SSL_ARGS="--ssl-certfile ${GATEWAY_TLS_CERT_FILE} --ssl-keyfile ${GATEWAY_TLS_KEY_FILE}"
fi

# shellcheck disable=SC2086
exec python -m uvicorn prometheus_gateway.asgi:app \
    --host 0.0.0.0 \
    --port 8000 \
    --no-access-log \
    $SSL_ARGS
