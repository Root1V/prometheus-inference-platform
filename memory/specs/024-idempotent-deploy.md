---
id: "024"
title: "Idempotent Deployment Mode for install-rhel.sh"
status: closed
current-agent: release-agent
created: 2026-05-09
updated: 2026-05-11
pipeline-log:
  - agent: spec-writer-agent
    status: draft
    timestamp: 2026-05-09
  - agent: developer-agent
    status: implementing
    timestamp: 2026-05-09
  - agent: developer-agent
    status: code-complete
    timestamp: 2026-05-09
  - agent: test-agent
    status: testing
    timestamp: 2026-05-09
  - agent: test-agent
    status: tests-passed
    timestamp: 2026-05-09
  - agent: security-reviewer-agent
    status: reviewing
    timestamp: 2026-05-10
  - agent: security-reviewer-agent
    status: security-approved
    timestamp: 2026-05-09
  - agent: validation-triage-agent
    status: spec-amendment-required
    timestamp: 2026-05-09
  - agent: spec-writer-agent
    status: draft
    timestamp: 2026-05-09
  - agent: developer-agent
    status: implementing
    timestamp: 2026-05-09
  - agent: developer-agent
    status: code-complete
    timestamp: 2026-05-09
  - agent: test-agent
    status: testing
    timestamp: 2026-05-09
  - agent: test-agent
    status: tests-passed
    timestamp: 2026-05-09
  - agent: security-reviewer-agent
    status: reviewing
    timestamp: 2026-05-09
  - agent: security-reviewer-agent
    status: security-approved
    timestamp: 2026-05-09
  - agent: developer-agent
    status: implementing
    timestamp: 2026-05-10
  - agent: developer-agent
    status: code-complete
    timestamp: 2026-05-10
  - agent: test-agent
    status: testing
    timestamp: 2026-05-10
  - agent: test-agent
    status: tests-passed
    timestamp: 2026-05-10
  - agent: developer-agent
    status: code-complete
    timestamp: 2026-05-10
  - agent: test-agent
    status: testing
    timestamp: 2026-05-10
  - agent: test-agent
    status: tests-passed
    timestamp: 2026-05-10
  - agent: security-reviewer-agent
    status: reviewing
    timestamp: 2026-05-10
  - agent: security-reviewer-agent
    status: security-approved
    timestamp: 2026-05-10
  - agent: docs-agent
    status: documenting
    timestamp: 2026-05-11T15:45:00Z
  - agent: docs-agent
    status: implemented
    timestamp: 2026-05-11T15:50:00Z
  - agent: release-agent
    status: releasing
    timestamp: 2026-05-11T20:54:37Z
  - agent: release-agent
    status: closed
    timestamp: 2026-05-11T21:01:52Z
---

<!-- Scope: Improve scripts/install-rhel.sh to support a fast --deploy mode suitable for repeated
     execution after each feature release. The full installation path (packages, llama-server build,
     keypair generation) must remain accessible via --install and --force flags. -->

<!-- Amendment 2026-05-09 (validation-triage-agent): Real-server validation of --deploy on RHEL 9.7
     revealed that untracked files in the server working tree (e.g. .env.redhat.example) cause
     `git pull --ff-only` to abort with "untracked working tree files would be overwritten by merge".
     Root cause: the server's PROJECT_DIR contains files not tracked by git that conflict with
     incoming upstream paths. The current implementation (`git stash` + `git pull --ff-only`) does
     not handle untracked files.
     Proposed scope change: before any git operation, detect a dirty tree (untracked OR modified
     files). If dirty, create a compressed timestamped archive of the full project directory, then
     run `git clean -fd` to remove untracked files and `git checkout -- .` to reset tracked files,
     then proceed with `git pull --ff-only`. No secrets or persistent data live inside PROJECT_DIR
     (they reside in /etc/prometheus and /srv/prometheus), so the clean is safe.
     New AC-15 added. AC-1 through AC-14 are already implemented and remain unchanged. -->

# 024 — Idempotent Deployment Mode for install-rhel.sh

## Problem Statement

`scripts/install-rhel.sh` was designed as a one-shot installer for bare-metal RHEL 9.7 servers.
After the initial installation, operators pull each feature release and need to:

1. Update the code to the latest commit
2. Sync Python dependencies (only if `uv.lock` changed)
3. Rebuild container images
4. Restart running services

Currently, running the script again forces all 10 steps — including slow operations like
`dnf install` (1-2 min), `uv sync` (30-60 s), and `llama-server` build (5-15 min) — even when
none of that software changed. This makes post-release deployments unnecessarily slow and risky
(a failed build mid-deploy can leave the system in a degraded state).

## Goals

- [ ] Add a `--deploy` mode that runs only the steps needed after a code update
- [ ] `--deploy` must be safe to run repeatedly: idempotent and non-destructive
- [ ] Skip system packages, llama-server build, keypair/TLS generation, and .env copying unless forced
- [ ] Run `uv sync` only if `uv.lock` has changed since the last deploy
- [ ] Rebuild and restart Podman containers after a code pull
- [ ] Restart the Manager API and llama-server if their config changed
- [ ] Record a deploy timestamp/commit hash in a state file to support idempotency checks
- [ ] Display the git tag/version and commit hash being deployed at the start and end of `--deploy` execution
- [ ] Keep all existing flags (`--force`, `--proxy`, `--git-credentials`, `--project-dir`, `--user`) working in both modes
- [ ] Handle dirty server working trees by creating a timestamped backup archive before any git operation

## Non-Goals

- Will not replace the full `--install` path — that must remain unchanged
- Will not implement rolling deploys or zero-downtime switching (out of scope)
- Will not add container health polling or wait-for-healthy loops
- Will not modify `scripts/validate.sh`

## Proposed Solution

Add a `--deploy` flag to `install-rhel.sh`. When set, the script runs a reduced step set:

```
--deploy step set (fast path):
  0. Print deploy header: version tag + commit hash being deployed (git describe --tags / git rev-parse --short HEAD)
  1. Dirty-tree check + git pull:
       a. Run `git status --porcelain` — if any output (modified or untracked files):
            i.  Create ${PROJECT_DIR}/../prometheus-backup-YYYYMMDD-HHMMSS.tar.gz from PROJECT_DIR
            ii. Run `git clean -fd` to remove all untracked files
            iii.Run `git checkout -- .` to reset any tracked-file modifications
       b. Run `git pull --ff-only`
  2. uv sync (only if uv.lock hash changed vs. state file)
  3. podman compose down + up --build (rebuild images, restart containers)
  4. Restart Manager API process (pmgr serve) if running
  5. Restart llama-server if running
  6. Record deploy state (commit hash, timestamp) to .deploy-state
  7. Print deployment summary: version tag + commit hash + elapsed time + next step (run validate.sh)
```

**Why this is safe**: persistent data (TLS certs, secrets, `.env` files, JWT keys, models) all live
outside `${PROJECT_DIR}` — under `/etc/prometheus/`, `/opt/prometheus-ai-inference/keys/`, and
`/srv/prometheus/models/`. Nothing critical is lost when the project tree is cleaned.

The full `--install` step set (all 10 steps) remains unchanged and is invoked when `--deploy` is **not** set.

### Mode Detection and State File

A state file at `${PROJECT_DIR}/.deploy-state` tracks the last successful deploy:

```
# .deploy-state — written by install-rhel.sh --deploy
LAST_DEPLOY_COMMIT=abc1234
LAST_DEPLOY_TIMESTAMP=2026-05-09T18:00:00
LAST_UVSYNC_LOCK_HASH=sha256:deadbeef
```

The `uv sync` step reads `LAST_UVSYNC_LOCK_HASH`, computes the current `sha256sum uv.lock`, and
skips sync only if they match. `--force` always ignores the state file.

### Key Design Decisions

| Decision | Rationale |
|----------|-----------|
| `--deploy` as a new flag, not a subcommand | Keeps the existing invocation style; no breaking change to existing automation |
| State file in `${PROJECT_DIR}/.deploy-state` | Co-located with the repo, easy to `cat` for debugging; gitignored |
| `uv sync` gated on `uv.lock` hash | `uv sync` is idempotent but slow (~30s); hash check makes repeated deploys fast |
| `podman compose down && up --build` | Ensures new images are always built; no stale layers |
| No health-wait loop | Keeps scope small; `scripts/validate.sh` is the correct tool for post-deploy validation |
| `--force` overrides state file | Provides an escape hatch for corrupted state |
| Backup before `git clean -fd` | Prevents accidental data loss; operator can inspect the archive before discarding |
| Archive location `${PROJECT_DIR}/..` | Stays on the same filesystem (no cross-device `mv`); outside repo so it is never tracked by git |
| `git checkout -- .` + `git clean -fd` over re-clone | Faster and preserves git history/remotes; re-clone only needed when `.git` itself is corrupt |

## API Contract

N/A — this spec modifies a shell script, not an API endpoint.

## Data Model

New file: `${PROJECT_DIR}/.deploy-state` (plain key=value, gitignored).

```
LAST_DEPLOY_COMMIT=<git short hash>
LAST_DEPLOY_TIMESTAMP=<ISO-8601>
LAST_UVSYNC_LOCK_HASH=<sha256:hex>
```

## Security Considerations

- `.deploy-state` contains no secrets — only commit hashes and timestamps. Safe to read by any user.
- `podman compose down` briefly interrupts service. Operators must schedule deploys during low-traffic windows.
- The script still validates file permissions on `--git-credentials` (mode 600) before copying to `~/.netrc`.
- `--force` bypasses state checks but does NOT bypass OS guard (Linux-only check remains).
- No new network calls introduced — all operations are local git/podman commands.
- Backup archive `prometheus-backup-YYYYMMDD-HHMMSS.tar.gz` is created with the same ownership as the process running the script; operators should verify it does not contain unintended secrets before sharing. The file is created one directory above `PROJECT_DIR` and never tracked by git.
- `git clean -fd` permanently removes untracked files from the working tree. The backup archive is the only recovery path. Log a clear warning before running it.

## Acceptance Criteria

- [x] AC-1: Given `--deploy` flag, when run on an up-to-date system, then only git pull, uv.lock hash check, container rebuild, and service restarts are executed (steps 1–7); system package install and llama-server build steps are skipped
- [x] AC-2: Given `uv.lock` has NOT changed since last deploy, when `--deploy` runs, then `uv sync` is skipped and `LAST_UVSYNC_LOCK_HASH` in `.deploy-state` is unchanged
- [x] AC-3: Given `uv.lock` HAS changed since last deploy, when `--deploy` runs, then `uv sync` is executed and `LAST_UVSYNC_LOCK_HASH` in `.deploy-state` is updated to the new hash
- [x] AC-4: Given `--deploy` completes successfully, when `.deploy-state` is read, then `LAST_DEPLOY_COMMIT` matches `git rev-parse --short HEAD` and `LAST_DEPLOY_TIMESTAMP` is set to the current UTC time
- [x] AC-5: Given `--deploy --force`, when run, then `uv sync` is always executed regardless of hash, and `.deploy-state` is overwritten
- [x] AC-6: Given running containers exist, when `--deploy` executes the container step, then `podman compose down` is called before `podman compose up --build -d`
- [x] AC-7: Given no running containers, when `--deploy` executes the container step, then `podman compose down` exits gracefully (no error) and `up --build -d` starts them
- [x] AC-8: Given `--deploy` is NOT set, when the script runs, then all 10 original installation steps execute (existing behaviour is unchanged)
- [x] AC-9: Given `--help`, when `--deploy` flag exists, then the help text includes a description of the `--deploy` mode and when to use it
- [x] AC-10: Given `--deploy` is passed without `--project-dir`, when the script runs, then it defaults to `/opt/prometheus-ai-inference` and logs the resolved path
- [x] AC-11: Given any Manager API process (`pmgr serve`) is running, when `--deploy` executes the service restart step, then the process is sent SIGTERM and restarted via `pmgr serve &`
- [x] AC-12: Given `llama-server` is running, when `--deploy` executes the service restart step, then the process is sent SIGTERM and must be restarted manually (log a clear warning with the restart command)
- [x] AC-13: Given `--deploy` starts, then the header banner displays the current git tag (from `git describe --tags --abbrev=0`) and short commit hash (from `git rev-parse --short HEAD`) before any step executes
- [x] AC-14: Given `--deploy` completes successfully, then the final summary displays the deployed version tag, commit hash, total elapsed time in seconds, and the command to validate: `bash scripts/validate.sh`
- [x] AC-15: Given untracked or modified files exist in `${PROJECT_DIR}` when `--deploy` runs step 1, then: (a) a compressed archive `${PROJECT_DIR}/../prometheus-backup-YYYYMMDD-HHMMSS.tar.gz` is created from the full project directory, (b) a warning is logged naming the archive path, (c) `git clean -fd` removes untracked files and `git checkout -- .` resets modified tracked files, and (d) `git pull --ff-only` succeeds; if the tree is already clean, no archive is created

## E2E Validation

> Script: `validations/024-idempotent-deploy.py`
> Run against the full RHEL stack after deploying a test feature branch.
> Steps: pull a known commit, run `--deploy`, assert `.deploy-state` was written,
> assert containers are running, assert validate.sh exits 0.

## Open Questions

- [ ] Q1: Should `--deploy` also re-inject secrets that still contain placeholder values (safety net), or always leave secrets untouched?

## References

- Related specs: [memory/specs/023-redhat-compatibility.md](memory/specs/023-redhat-compatibility.md)
- `scripts/install-rhel.sh` — file to modify
- `scripts/validate.sh` — post-deploy validation tool
