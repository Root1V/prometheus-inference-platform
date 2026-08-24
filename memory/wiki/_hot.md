# Prometheus Wiki — Hot Context

Last updated: 2026-05-11T18:32:11Z

## Active Feature Work
- Spec 041: rate limiting migration in progress
- Spec 042: OAuth2 token rotation pending validation
- Spec 024: Idempotent deployment mode (--deploy flag) now live — enables fast post-release updates with state tracking

## Recent Security Constraints
- JWT validation order standardized
- Raw JWT logging forbidden in all services
- Backup archives created during --deploy are gzipped but not encrypted — inspect before sharing

## Runtime / Infra Notes
- llama.cpp now requires TLS proxying
- Redis connection pooling changed in gateway
- Deployment: use `install-rhel.sh --deploy` for fast incremental updates (skips package install, llama-build, key gen)
- State file: `.deploy-state` in project root tracks last deployment commit, uv.lock hash, and timestamp for idempotency
- Dirty-tree recovery: if git pull would fail, --deploy creates timestamped backup archive (`prometheus-backup-YYYYMMDD-HHMMSS.tar.gz`) before cleaning

## Validation Risks
- RHEL validation failing on cert generation for non-internal hosts
- Podman rootless builder may silently ignore `COPY --chown=username:username` if user doesn't exist at build-time — watch for Permission denied on entrypoint scripts

## Recently Introduced Decisions
- ADR-014: centralized quota enforcement
- Spec 024: idempotent deploy mode uses git clean instead of re-clone for faster recovery