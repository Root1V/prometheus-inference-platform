#!/usr/bin/env bash
# gateway/certs/gen-dev-cert.sh
#
# Generate a self-signed TLS certificate for LOCAL DEVELOPMENT ONLY.
# Produces:
#   gateway/certs/dev.crt  — certificate (PEM)
#   gateway/certs/dev.key  — private key  (PEM, unencrypted)
#
# Implements: memory/specs/013-web-chat-ui-proxy.md — AC-20
#
# Usage:
#   bash gateway/certs/gen-dev-cert.sh
#
# After running, add dev.crt to your browser / OS trust store once so
# the browser accepts the self-signed cert and keeps the Secure cookie.
#
# macOS quick trust:
#   sudo security add-trusted-cert -d -r trustRoot \
#       -k /Library/Keychains/System.keychain gateway/certs/dev.crt
#
# WARNING: NEVER use this certificate in production.
#          These files are gitignored.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CERT="${SCRIPT_DIR}/dev.crt"
KEY="${SCRIPT_DIR}/dev.key"

command -v openssl >/dev/null 2>&1 || { echo "ERROR: openssl not found in PATH"; exit 1; }

echo "Generating self-signed dev certificate..."

openssl req \
  -x509 \
  -newkey rsa:2048 \
  -keyout "${KEY}" \
  -out "${CERT}" \
  -days 3650 \
  -nodes \
  -subj "/CN=localhost" \
  -addext "subjectAltName=DNS:localhost,IP:127.0.0.1"

echo "Done."
echo "  Certificate : ${CERT}"
echo "  Private key : ${KEY}"
echo ""
echo "Set these in gateway/.env:"
echo "  GATEWAY_TLS_CERT_FILE=${CERT}"
echo "  GATEWAY_TLS_KEY_FILE=${KEY}"
