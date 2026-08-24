---
title: "Canonical project directory for RHEL installer"
date: 2026-05-09
---

# Decision: Canonical project directory

## Context

The RHEL installer and host bind-mounts require a predictable repository root for service unit files, podman-compose binds, SELinux labels, and operator instructions. Without a canonical path, operators and automation scripts would need per-server configuration which increases operational risk.

## Decision

Use `/opt/prometheus-ai-inference/` as the canonical project directory on RHEL hosts for all deployment and installer operations initiated by `scripts/install-rhel.sh`.

## Rationale

- `/opt` is appropriate for optional, add-on software on Unix-like systems and avoids mixing application code with system directories.
- A single canonical path simplifies service unit files, SELinux labeling, and podman bind-mounts.
- Operators and automation can assume a standard location, reducing configuration drift across multiple servers.

## Consequences

- All installer scripts, systemd unit files, and operator documentation will reference `/opt/prometheus-ai-inference/`.
- Bind mounts in `podman-compose.yml` and other host paths must use absolute paths under `/opt`.
- Future specs that require a different canonical location must create a follow-up decision and explicitly justify the change.

## Rejected alternatives

- `/srv/prometheus` — more appropriate for runtime data; not chosen to avoid mixing code and storage.
- `/home/llmops/prometheus` — user-home paths complicate system services and SELinux labeling.

## References

- memory/specs/023-redhat-compatibility.md
- memory/wiki/deployment.md
