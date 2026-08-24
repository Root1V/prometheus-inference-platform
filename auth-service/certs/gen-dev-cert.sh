#!/usr/bin/env bash
# auth-service/certs/gen-dev-cert.sh
#
# Generate a self-signed TLS certificate for LOCAL DEVELOPMENT ONLY.
# Produces:
#   auth-service/certs/dev.crt  — certificate (PEM)
#   auth-service/certs/dev.key  — private key  (PEM, unencrypted)
#
# Implements: memory/specs/017-auth-service-tls.md — AC-16, AC-17
#
# Usage:
#   bash auth-service/certs/gen-dev-cert.sh
#
# After running, add dev.crt to your browser / OS trust store once so
# the browser accepts the self-signed cert.
#
# macOS quick trust:
#   sudo security add-trusted-cert -d -r trustRoot \
#       -k /Library/Keychains/System.keychain auth-service/certs/dev.crt
#
# WARNING: NEVER use this certificate in production.
#          These files are gitignored.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CERT="${SCRIPT_DIR}/dev.crt"
KEY="${SCRIPT_DIR}/dev.key"

command -v openssl >/dev/null 2>&1 || { echo "ERROR: openssl not found in PATH"; exit 1; }

# AC-17: idempotent — do not overwrite existing certs
if [[ -f "$CERT" && -f "$KEY" ]]; then
    echo "Notice: dev.crt and dev.key already exist — skipping generation."
    echo "  Certificate : ${CERT}"
    echo "  Private key : ${KEY}"
    echo "Delete them manually to regenerate."
    exit 0
fi

echo "Generating self-signed dev certificate..."

openssl req \
  -x509 \
  -newkey rsa:2048 \
  -keyout "${KEY}" \
  -out "${CERT}" \
  -days 365 \
  -nodes \
  -subj "/CN=localhost" \
  -addext "subjectAltName=DNS:localhost,IP:127.0.0.1"

chmod 600 "${KEY}"

echo "Done."
echo "  Certificate : ${CERT}"
echo "  Private key : ${KEY}"
echo ""
echo "Set these in auth-service/.env:"
echo "  AUTH_TLS_CERT_FILE=${CERT}"
echo "  AUTH_TLS_KEY_FILE=${KEY}"
