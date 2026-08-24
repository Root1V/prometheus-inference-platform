---
description: "Use when writing specs, creating features, or following Spec Driven Development (SDD). Covers the full spec authoring workflow, spec format, status lifecycle, acceptance criteria, and the rule that no code is written without an approved spec."
applyTo: "**"
---

# Spec Driven Development (SDD) — Prometheus

## SDD Workflows

The canonical SDD workflows are defined here. Agent files implement their specific step, but this file defines the global pipeline.

### Normal Flow

draft → approved → branching → implementing → code-complete → testing → tests-passed → reviewing → security-approved → documenting → implemented → releasing → released → closed

### Hotfix Flow

A hotfix is triggered by a production-impacting issue.

The trigger is outside the formal spec lifecycle:

`production issue → validation-triage-agent → HOTFIX_REQUIRED`

After triage, the formal SDD lifecycle starts when `spec-writer-agent` creates a minimal hotfix spec:

`draft → approved → branching → implementing → code-complete → testing → tests-passed → reviewing → security-approved → documenting → implemented → releasing → released → closed`

Hotfix rules:
- use only for urgent production-impacting issues
- create a minimal spec with 1–3 ACs
- branch format: `hotfix/NNN-kebab-title`
- base branch: `main`
- release bump: patch
- do not include refactors, redesigns, or new capabilities

### Branch Ownership

`developer-agent` creates or checks out the required branch before implementation.

- `feat/NNN-*` starts from `develop`
- `fix/NNN-*` starts from `develop`
- `hotfix/NNN-*` starts from `main`

The branch must be prepared before any code is changed.

### Manual Gates

Human approval is required for:
- `draft` → `approved`
- E2E validation after `security-approved`
- production verification after `released`
- final `closed` status

## SDD Step-by-Step

Follow these steps **in order** for every new feature. Do not skip or reorder steps.

```
branch → spec-writer-agent → [user: approved] → developer-agent → test-agent → security-reviewer-agent
  → [user: human-approved] → docs-agent → [user: resume-pipeline] → release-agent → closed
```

| Step | Who acts | What happens | Gate |
|------|----------|--------------|------|
| **1. Branch** | Agent | `git checkout develop && git checkout -b feat/NNN-name` | Always branch from `develop` |
| **2. Write spec** | Agent (`spec-writer-agent`) | Generate `memory/specs/NNN-name.md` with `status: draft`. Never write specs manually. | — |
| **3. Approve** | **User** | Set `status: approved`. No implementation starts before this gate. | **No code without `approved`** |
| **4. Implement** | Agent (`developer-agent`) | Implement all acceptance criteria from the spec. Never write feature code directly. | All ACs must be implemented |
| **5. Test** | Agent (`test-agent`) | Write/update tests for every AC and pre-push hook validations if affected. Run full hook suite. | All checks must pass |
| **6. Security review** | Agent (`security-reviewer-agent`) | Review all changed files. Fix any findings before proceeding. | No known findings |
| **7. Human review** | **User** | Run `validations/NNN-name.py` against the full stack. If it passes, set `status: human-approved`. If it fails or the deliverable needs changes, give feedback to iterate back to step 4. | **No docs without `human-approved`** |
| **8. Document** | Agent (`docs-agent`) | Reconcile spec content with what was built. Update affected `AGENTS.md` files and `README.md`. | — |
| **9. Release** | Agent (`release-agent`) | Commit · push · PR feat→develop · merge · PR develop→main · merge · tag · GitHub release. | User must invoke `/resume-pipeline` |

> **CRITICAL AGENT RULES**:
> - Never write feature code, tests, or spec documents directly. Always delegate to the appropriate agent.
> - Never invoke `release-agent` without explicit user instruction (run `/resume-pipeline`).
> - Never start implementation until spec status is `approved`.
> - Never start documentation until spec status is `human-approved`.

## Spec Lifecycle

```
[draft] → [approved]
  → [implementing] → [code-complete]
  → [testing] → [tests-passed]
  → [reviewing] → [security-approved]
  → [human-approved]
  → [documenting] → [implemented]
  → [releasing] → [released]
  → [closed]
```

| Status | Set by | Meaning |
|--------|--------|---------|
| `draft` | `spec-writer-agent` | Spec being written, awaiting user approval |
| `approved` | **User** | Cleared for implementation — first human gate |
| `implementing` | `developer-agent` | AC implementation in progress |
| `code-complete` | `developer-agent` | All ACs implemented successfully — ready for testing |
| `testing` | `test-agent` | Tests being written and hook suite running |
| `tests-passed` | `test-agent` | All tests written, pre-push hook suite green |
| `reviewing` | `security-reviewer-agent` | Security review in progress |
| `security-approved` | `security-reviewer-agent` | No CRITICAL/HIGH findings — awaiting human review |
| `human-approved` | **User** | Deliverable reviewed and accepted — second human gate. Cleared for docs and release |
| `documenting` | `docs-agent` | Docs and spec content being updated |
| `implemented` | `docs-agent` | Spec reconciled, AGENTS.md and README updated |
| `releasing` | `release-agent` | Commit · push · PRs · merges · tag · release in progress |
| `released` | `release-agent` | Merged to main, tagged, GitHub release created |
| `closed` | `release-agent` | Released to main, tagged, GitHub release created, spec fully closed |

**Rule**: Only implement specs with `status: approved`.

**Parallel specs**: Multiple specs can be `implementing` simultaneously — each on its own `feat/NNN-*` branch. The `current-agent` field shows who is actively working on each one.

## Spec File Location & Naming

```
memory/specs/NNN-kebab-case-name.md
```

- `NNN` is a zero-padded sequential number: `001`, `002`, `003`, …
- Filename is the canonical identifier — never rename after `approved`.

## Spec Template

```markdown
---
id: "NNN"
title: "Feature Name"
status: draft
current-agent: spec-writer-agent
created: YYYY-MM-DDTHH:MM:SSZ
updated: YYYY-MM-DDTHH:MM:SSZ
pipeline-log:
  - agent: spec-writer-agent
    status: draft
    timestamp: YYYY-MM-DDTHH:MM:SSZ
---

<!-- Historical notes (scope changes, major revisions): add here as HTML comments, not as a Changelog section -->

# NNN — Feature Name

## Problem Statement
What problem does this solve? Who is affected? What is the impact if not solved?

## Goals
- [ ] Goal 1
- [ ] Goal 2

## Non-Goals
- Not in scope: X
- Will not address: Y

## Proposed Solution
High-level description. Include diagrams if helpful (Mermaid).

### Key Design Decisions
| Decision | Rationale |
|----------|-----------|
| ... | ... |

## API Contract
- If this spec adds/modifies API endpoints, document them here in a table with HTTP method, endpoint path, required scopes, request body summary, response body summary and expected response codes.
- Reference the target OpenAPI file path for `developer-agent` to create 

## Data Model
Describe any new or modified data structures.

## Security Considerations
- Auth requirements
- Input validation
- Rate limiting implications
- Data sensitivity
- authorization/scopes
- prompt injection
- secret handling
- logging/PII
- error disclosure

## Acceptance Criteria
Each item maps 1-to-1 with a test case.

- [ ] AC-1: Given X, when Y, then Z
- [ ] AC-2: ...

## E2E Validation
> Script: `validations/NNN-kebab-case-name.py`
> Run against the full stack (llama-server + Podman containers) before setting `status: human-approved`.
> Leave blank if this spec does not require full-stack validation.

## Open Questions
- [ ] Q1: ...

## References
- Related specs: `memory/specs/NNN-related.md`
- External docs: ...
```

## Authoring Rules

1. **AC format**: "Given [context], when [action], then [outcome]" — every AC is testable.
2. **No ambiguous ACs**: If you can't write a test for it, rewrite the AC.
3. **Security section is mandatory** for any spec that touches auth, token handling, or llama.cpp forwarding.
4. **Link OpenAPI** for any spec adding or modifying API endpoints.
5. **Update `updated` date** every time the spec is modified.
6. **Historical notes** (scope changes, major revisions) go in an HTML comment immediately after the frontmatter — never as a `## Changelog` section in the spec body.
7. **`pipeline-log` is immutable**: every agent appends one entry (with `agent`, `status`, `timestamp`) when it changes status. Never overwrite or delete existing entries.
8. **`current-agent` lifecycle**: set to the agent's name when it starts working; set to `""` when handing off to the next stage.
9. **E2E Validation script**: if the spec touches the API or integration between components, create `validations/NNN-kebab-case-name.py` and reference it in the `## E2E Validation` section. The script must be runnable with `uv run validations/NNN-name.py` against the full stack.

## Implementing from a Spec

When implementing, each function/module that originates from a spec MUST include:
```python
# Implements: memory/specs/003-rate-limiting.md — AC-2
```

When writing tests, each test name MUST reference the AC:
```python
def test_rate_limit_per_user_AC2():  # memory/specs/003-rate-limiting.md
    ...
```
## Use memory for context

All agents must consult project memory (`memory/`) before implementation.

Use:
- wiki/_index.md for structure
- wiki/_hot.md for recent changes
- decisions/ for constraints
- specs/ for historical context