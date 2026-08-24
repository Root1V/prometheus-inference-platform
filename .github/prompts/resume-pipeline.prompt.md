---
description: "Resume the SDD pipeline after human review: document → commit → push → PR feat→develop → merge → PR develop→main → merge → tag → GitHub release. Requires status: human-approved before starting."
argument-hint: "Path to the human-approved spec (e.g. memory/specs/007-rate-limiting.md)"
---

Resume the SDD release pipeline for the following spec:

**Spec**: $input

## Pre-flight

1. Read `$input` — verify `status: human-approved`. **If the status is not `human-approved`, stop immediately and notify the user. Do not proceed until the user sets the status to `human-approved`.**

## Pipeline execution

Execute each step in order. Verify the gate before starting the next step. Do not skip or reorder.

### Step 1 — Document (`docs-agent`)

Invoke `@docs-agent` with `$input`.

Gate: verify `status: implemented` in `$input` before continuing.

---

### Step 2 — Release (`release-agent`)

Invoke `@release-agent` with `$input`.

Gate: verify `status: released` in `$input` after completion.

---

## Completion

Once `status: released` is confirmed, report:

```
Pipeline complete — spec is now `released`.

  Branch merged to develop ✓
  Branch merged to main ✓
  Tag and GitHub release created ✓

Set status to `closed` manually after verifying in production.
```
