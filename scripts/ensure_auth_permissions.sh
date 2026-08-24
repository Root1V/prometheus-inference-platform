#!/usr/bin/env bash
# Ensure correct ownership/permissions for auth-service host directory
# Usage: ./scripts/ensure_auth_permissions.sh [container-name] [host-auth-dir]

set -euo pipefail

CONTAINER=${1:-auth-service}
AUTH_DIR=${2:-/var/lib/prometheus/auth-service}

echo "Using container: ${CONTAINER}"
echo "Target host directory: ${AUTH_DIR}"

# find running container if the given name is not present
if ! podman ps --format '{{.Names}}' | grep -q "^${CONTAINER}$"; then
  echo "container '${CONTAINER}' not found running — searching for any container with 'auth' in its name"
  CANDIDATE=$(podman ps --format '{{.Names}}' | grep auth | head -n1 || true)
  if [ -n "$CANDIDATE" ]; then
    echo "Found candidate container: $CANDIDATE"
    CONTAINER=$CANDIDATE
  else
    echo "No running auth container found. Please start the container or pass the container name as first arg." >&2
    exit 2
  fi
fi

# determine UID inside the container
if ! UID="$(podman exec "$CONTAINER" id -u 2>/dev/null)"; then
  echo "Failed to run 'id -u' inside container '$CONTAINER'" >&2
  exit 3
fi

# Validate UID is a non-negative integer before using in sudo chown
if ! [[ "${UID}" =~ ^[0-9]+$ ]]; then
  echo "ERROR: container returned non-numeric UID: '${UID}' — aborting" >&2
  exit 4
fi

echo "Detected UID inside container: $UID"

echo "Applying ownership and permissions on host (requires sudo)..."
sudo mkdir -p "$AUTH_DIR"
sudo chown -R ${UID}:${UID} "$AUTH_DIR"
sudo chmod -R u+rwX,g+rwX "$AUTH_DIR"

echo "Applying SELinux label (if SELinux enabled)..."
sudo chcon -Rt container_file_t "$AUTH_DIR" || true

echo "Done. Verify with: ls -ld $AUTH_DIR && sudo stat -c '%U:%G %u:%g' $AUTH_DIR"

exit 0
