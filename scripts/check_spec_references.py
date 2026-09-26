#!/usr/bin/env python3
"""Every `Implements:` in the source must point at a document that exists — PRM-148.

The code carries 288 `Implements: <path>` comments naming the spec or roadmap
entry each piece satisfies, and the acceptance criteria it cites by number
(`AC-8`, `AC-27`). That is the only trace from a line of code back to the
requirement it exists for.

All 218 of the ones pointing into `memory/specs/` were dangling: commit 546a196
removed the spec corpus and the comments were never updated, so every `AC-N` in
the codebase had become unverifiable. It went unnoticed for months because a
comment cannot fail.

This is what makes it fail. Run from the repo root; exits non-zero and names
every unresolved reference.
"""

from __future__ import annotations

import pathlib
import re
import sys
from collections import Counter

_REFERENCE = re.compile(r"Implements:\s*([A-Za-z0-9_./-]+\.md)")

# Where source lives. Tests are excluded: a test naming a spec is describing the
# code it covers, and the code's own comment is the reference that matters.
_SOURCE_GLOBS = (
    "gateway/src/**/*.py",
    "auth-service/src/**/*.py",
    "runtime/manager/*/src/**/*.py",
    "telemetry/src/**/*.py",
)


def main() -> int:
    root = pathlib.Path(__file__).resolve().parents[1]
    references: Counter[str] = Counter()
    where: dict[str, list[str]] = {}

    for glob in _SOURCE_GLOBS:
        for path in root.glob(glob):
            if ".venv" in path.parts:
                continue
            for match in _REFERENCE.finditer(path.read_text(errors="ignore")):
                target = match.group(1)
                references[target] += 1
                where.setdefault(target, []).append(str(path.relative_to(root)))

    if not references:
        print("FAIL: found no `Implements:` references at all — the scan is broken,")
        print("      not the documentation. Check _SOURCE_GLOBS.")
        return 1

    missing = {t: n for t, n in references.items() if not (root / t).exists()}

    total = sum(references.values())
    print(f"  {total} references to {len(references)} documents")

    if missing:
        print(f"\nFAIL: {sum(missing.values())} reference(s) point at documents that do not exist.")
        print("A comment naming a requirement that is gone reads as authoritative and is not.\n")
        for target, count in sorted(missing.items(), key=lambda kv: -kv[1]):
            print(f"  {count:3}x  {target}")
            for source in sorted(set(where[target]))[:3]:
                print(f"         {source}")
        print("\nEither restore the document (it may be in git history — this corpus was")
        print("recovered from 546a196^) or update the comments to where it moved.")
        return 1

    print("  PASS: every referenced document exists")
    return 0


if __name__ == "__main__":
    sys.exit(main())
