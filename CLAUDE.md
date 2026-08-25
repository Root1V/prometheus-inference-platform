# CLAUDE.md

Behavioral guidelines to reduce common LLM coding mistakes. Merge with project-specific instructions as needed.

**Tradeoff:** These guidelines bias toward caution over speed. For trivial tasks, use judgment.

## 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them - don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

## 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

## 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it - don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

## 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:
```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```

Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.

## 5. Roadmap: Two-File Pattern

**Index and detail live in separate files. Never merge them.**

A single roadmap file with full detail on every item forces reading the whole thing for
even a simple "what's the status of X?" Splitting it keeps most lookups cheap (read the
index) and only pays the cost of full detail when actually working on that one item.

- **`roadmap.md`** (repo root) — the index. One table, one line per item: `#`, Feature,
  Status, one-sentence description. ID prefix `RM-NN`, also used in branch names and
  commit messages. Status: `done` / `todo` (add `in-progress`/`blocked` only if actually
  needed). This file never grows beyond a table — no exceptions.
- **`docs/roadmap.md`** — the detail. One section per item, capped at **Why** (1-3
  sentences) and **Scope** (what's in/out, a few bullets). Not a changelog — history of
  *what happened* lives in commits/PRs; this file holds *what was decided and why*.

Maintenance rules:
- Never duplicate text between the two files — the index links, it doesn't restate.
- Once an item is `done` and stable, trim its detail section to 2-3 lines + a link to the
  commit/PR — don't preserve full reasoning forever once the code and git history are the
  real record.
- Before adding anything to either file, ask: is this needed to decide or act, or is it
  just history? History doesn't go in the roadmap.
- Big architectural decisions don't live here — reference `memory/decisions/` instead of
  inlining them.

