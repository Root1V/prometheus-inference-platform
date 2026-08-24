---
name: run-validations
description: "Run the full Prometheus validation suite: lint, format, type-check, and tests for all modules (gateway, auth-service, runtime/manager, runtime scripts). Use before any handoff, commit, or push. Invoked by test-agent and developer-agent."
argument-hint: "Optional: module to validate (gateway | auth-service | runtime/manager | all). Defaults to all."
---

# Run Validations

Runs the full pre-push validation suite for Prometheus. The authoritative implementation
is [.githooks/pre-push](../../.githooks/pre-push) — this skill reflects its exact command set.

## When to use

- Before any agent handoff that requires code to be valid
- Before a `git commit` or `git push`
- When a user asks "run all checks", "validate", "run tests", or "pre-push"

## Procedure

Run in this exact order. Stop immediately on first failure and report which check failed and why.

### 1. Install / sync dependencies

```bash
uv sync
```

### 2. gateway

```bash
uv run ruff check gateway/
uv run ruff format --check gateway/
uv run mypy gateway/src/
uv run pytest gateway/tests/ -v --tb=short --cov=gateway/src --cov-fail-under=80
```

### 3. auth-service

```bash
cd auth-service
uv run ruff check src/
uv run ruff format --check src/
uv run mypy src/
uv run pytest tests/ -v --tb=short
```

### 4. runtime/manager

```bash
cd runtime/manager
uv run pytest tests/ -v --tb=short
```

### 5. runtime scripts

```bash
bash runtime/tests/test_runtime_scripts.sh
```

## Scope argument

If invoked with a specific module (e.g. `/run-validations gateway`), run only the checks
for that module. If no argument is given, run all modules in the order above.

Valid scopes: `gateway` | `auth-service` | `runtime/manager` | `runtime-scripts` | `all`

### E2E scope (requires full stack)

If invoked with `e2e <spec-path>` (e.g. `/run-validations e2e memory/specs/007-rate-limiting.md`):

1. Read the spec and locate the `## E2E Validation` section.
2. If the section is blank or absent, report "No E2E validation script defined for this spec" and stop.
3. Run the referenced script: `uv run validations/NNN-name.py`
4. **Prerequisite**: the full stack must be running (llama-server + Podman containers). If the script
   fails with a connection error, report "Full stack not running — start llama-server and Podman containers first."

The E2E scope is **never run automatically** by `test-agent` or `developer-agent` — it is only
invoked explicitly by the user during human review (step 7 of the SDD workflow).

## Output format

```
Validation suite: <scope>

  gateway     ruff check       ✓ / ✗
  gateway     ruff format      ✓ / ✗
  gateway     mypy             ✓ / ✗
  gateway     pytest           ✓ / ✗  (coverage: N%)
  auth-service ruff check      ✓ / ✗
  auth-service ruff format     ✓ / ✗
  auth-service mypy            ✓ / ✗
  auth-service pytest          ✓ / ✗
  runtime/manager pytest       ✓ / ✗
  runtime scripts              ✓ / ✗

Result: ALL PASSED / FAILED at <check>
```

If any check fails: report the exact error output and **do not continue** to the next step in the calling agent's process.
