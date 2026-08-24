---
description: "Run the SDD implementation pipeline for a spec: implement → test → security review. Stops at security-approved and waits for human review. Requires status: approved before starting."
argument-hint: "Path to the approved spec (e.g. memory/specs/007-rate-limiting.md)"
---

Run the SDD implementation pipeline for the following spec:

**Spec**: $input

## Pre-flight

1. Read `$input` — verify `status: approved`. **If the status is not `approved`, stop immediately and notify the user. Do not proceed until the user sets the status to `approved`.**

## Pipeline execution

Execute each step in order. Verify the gate before starting the next step. Do not skip or reorder.

### Step 1 — Implement (`developer-agent`)

Invoke `@developer-agent` with `$input`.

Gate: verify `status: code-complete` in `$input` before continuing. If the agent did not set `code-complete`, stop and report the failure.

---

### Step 2 — Test (`test-agent`)

Invoke `@test-agent` with `$input`.

Gate: verify `status: tests-passed` in `$input` before continuing. If any check failed, stop and report which tests or hook validations failed.

---

### Step 3 — Security review (`security-reviewer-agent`)

Invoke `@security-reviewer-agent` with `$input`.

Gate: verify `status: security-approved` in `$input` before continuing. If CRITICAL or HIGH findings remain open, stop and list them. Do not proceed until all findings are resolved.

---

## ⏸ Human gate

The pipeline stops here. Notify the user:

```
Pipeline complete — spec is now `security-approved`.

Review the implementation and, if satisfied:
  1. Set status: human-approved in $input
  2. Run /resume-pipeline $input to document and release.

If changes are needed, describe the feedback — the pipeline will iterate from Step 1.
```

**Do not invoke `docs-agent` or `release-agent` until the user explicitly runs `/resume-pipeline`.**


## Final report

```
Pipeline complete for: $input

Steps completed:
  ✓ developer-agent  → code-complete
  ✓ test-agent       → tests-passed
  ✓ security-reviewer-agent → security-approved
  ✓ docs-agent       → implemented
  ✓ commit

Spec status: implemented
Next: set status to `closed` manually after verifying in production.
Create PR only when explicitly requested by the user.
```
