#!/usr/bin/env -S uv run --script
"""Reunify billing history that a model rename split in two — PRM-113.

Usage rows used to be keyed on the model's public name, which RM-70 lets an
operator set once. Naming a model therefore started a second pile of rows under
the new name, with no link to the first. PRM-113 keys new rows on the catalog id
instead, but it cannot retroactively know which old name belonged to which
model — that mapping lives in the manager's registry, not in the gateway's
database.

So this is a separate, deliberate step rather than part of the migration.
Rewriting billing history should be something an operator chooses, reads the
plan of, and then runs — not a side effect of starting a service.

    python scripts/reunify_usage_history.py                  # plan only
    python scripts/reunify_usage_history.py --apply          # do it

Take a copy of gateway.db first. This edits rows you may have already invoiced.
"""

import argparse
import sqlite3
import sys
from pathlib import Path


def slug_to_id(registry_db: Path) -> dict[str, str]:
    conn = sqlite3.connect(registry_db)
    try:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(models)")]
        rows = (dict(zip(cols, r, strict=True)) for r in conn.execute("SELECT * FROM models"))
        return {d["slug"]: d["id"] for d in rows if d.get("slug") and d["slug"] != d["id"]}
    finally:
        conn.close()


def plan(gateway_db: Path, mapping: dict[str, str]) -> list[tuple]:
    """(table, old_name, new_id, rows, collisions) per model that has history."""
    conn = sqlite3.connect(gateway_db)
    out = []
    try:
        for old, new in mapping.items():
            for table in ("usage_events", "usage_daily"):
                n = conn.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE model_id = ?",  # noqa: S608
                    (old,),
                ).fetchone()[0]
                if not n:
                    continue
                collisions = 0
                if table == "usage_daily":
                    # (day, client_id, model_id) is unique, so a day that
                    # already has rows under the new id has to be merged rather
                    # than renamed — otherwise the update violates the key.
                    collisions = conn.execute(
                        """SELECT COUNT(*) FROM usage_daily a
                           WHERE a.model_id = ?
                             AND EXISTS (SELECT 1 FROM usage_daily b
                                         WHERE b.model_id = ? AND b.day = a.day
                                           AND b.client_id = a.client_id)""",
                        (old, new),
                    ).fetchone()[0]
                out.append((table, old, new, n, collisions))
        return out
    finally:
        conn.close()


def apply(gateway_db: Path, mapping: dict[str, str]) -> int:
    conn = sqlite3.connect(gateway_db)
    changed = 0
    try:
        for old, new in mapping.items():
            # The name the rows carried is preserved — that is what the model
            # was called at the time, and an invoice should still say so.
            conn.execute(
                "UPDATE usage_events SET model_slug = COALESCE(model_slug, model_id), "
                "model_id = ? WHERE model_id = ?",
                (new, old),
            )
            changed += conn.total_changes
            # Merge colliding daily rows into the surviving one, then rename
            # what is left. Counters add; a NULL cost stays NULL unless the
            # other side has one, matching record_usage()'s own rule.
            conn.execute(
                """UPDATE usage_daily AS b SET
                     prompt_tokens = b.prompt_tokens + (
                       SELECT a.prompt_tokens FROM usage_daily a
                       WHERE a.model_id=? AND a.day=b.day AND a.client_id=b.client_id),
                     completion_tokens = b.completion_tokens + (
                       SELECT a.completion_tokens FROM usage_daily a
                       WHERE a.model_id=? AND a.day=b.day AND a.client_id=b.client_id),
                     request_count = b.request_count + (
                       SELECT a.request_count FROM usage_daily a
                       WHERE a.model_id=? AND a.day=b.day AND a.client_id=b.client_id),
                     cost_usd = CASE
                       WHEN b.cost_usd IS NULL AND (SELECT a.cost_usd FROM usage_daily a
                            WHERE a.model_id=? AND a.day=b.day AND a.client_id=b.client_id) IS NULL
                       THEN NULL
                       ELSE COALESCE(b.cost_usd,0) + COALESCE((SELECT a.cost_usd FROM usage_daily a
                            WHERE a.model_id=? AND a.day=b.day AND a.client_id=b.client_id),0)
                     END
                   WHERE b.model_id = ?
                     AND EXISTS (SELECT 1 FROM usage_daily a
                                 WHERE a.model_id=? AND a.day=b.day AND a.client_id=b.client_id)""",
                (old, old, old, old, old, new, old),
            )
            conn.execute(
                """DELETE FROM usage_daily WHERE model_id = ?
                   AND EXISTS (SELECT 1 FROM usage_daily b
                               WHERE b.model_id = ? AND b.day = usage_daily.day
                                 AND b.client_id = usage_daily.client_id)""",
                (old, new),
            )
            conn.execute(
                "UPDATE usage_daily SET model_slug = COALESCE(model_slug, model_id), "
                "model_id = ? WHERE model_id = ?",
                (new, old),
            )
        conn.commit()
        return changed
    finally:
        conn.close()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--gateway-db", default="gateway.db", type=Path)
    ap.add_argument("--registry-db", default="runtime/manager/registry.db", type=Path)
    ap.add_argument("--apply", action="store_true", help="write the changes")
    args = ap.parse_args()

    for path in (args.gateway_db, args.registry_db):
        if not path.exists():
            print(f"not found: {path}", file=sys.stderr)
            return 2

    # The script writes model_slug, so the PRM-113 migration has to have run.
    # Without this the failure is a raw sqlite3 error halfway through.
    conn = sqlite3.connect(args.gateway_db)
    try:
        columns = {r[1] for r in conn.execute("PRAGMA table_info(usage_events)")}
    finally:
        conn.close()
    if "model_slug" not in columns:
        print(
            f"{args.gateway_db} has no model_slug column — start the gateway once so its "
            "migrations run, then re-run this.",
            file=sys.stderr,
        )
        return 2

    mapping = slug_to_id(args.registry_db)
    if not mapping:
        print("No model has a public name different from its id — nothing to reunify.")
        return 0

    rows = plan(args.gateway_db, mapping)
    if not rows:
        print("No usage rows are filed under an old name — nothing to reunify.")
        return 0

    print(f"{'table':14} {'filed under':34} {'becomes':34} {'rows':>6} {'merges':>7}")
    for table, old, new, n, collisions in rows:
        print(f"{table:14} {old:34} {new:34} {n:>6} {collisions:>7}")
    total = sum(r[3] for r in rows)
    print(f"\n{total} row(s) would be re-keyed. Their display name is preserved in model_slug.")

    if not args.apply:
        print("\nPlan only. Re-run with --apply to write, after copying gateway.db.")
        return 0

    changed = apply(args.gateway_db, mapping)
    print(f"\nDone. {changed} row(s) updated.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
