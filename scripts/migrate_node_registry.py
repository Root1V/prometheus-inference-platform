#!/usr/bin/env python3
"""Move the node registry from auth-service to the fleet coordinator — PRM-134.

Deliberately a script and not part of any service's startup. Copying data across
a service boundary is an operator action with a verification step: the two
databases belong to different services, may sit on different hosts, and somebody
has to be able to see what was copied before anything starts depending on it.

Idempotent and additive. A node already in the coordinator — matched by `name`,
which is unique and is what dashboard URLs use — is reported and left alone, so
running this twice is safe and running it after an edit does not undo the edit.

**It deletes nothing.** auth-service keeps its `nodes` table and its rows, which
is the rollback path: revert the code and the old registry is still there,
intact. This codebase's additive-only convention does not drop tables.

Usage, from the repo root, on the node that is the coordinator:

    uv run --project runtime/manager/core python scripts/migrate_node_registry.py --dry-run
    uv run --project runtime/manager/core python scripts/migrate_node_registry.py

The destination is that node's `fleet.db`, resolved from its own `manager.toml`.
Only the coordinator has one, and the script refuses to run anywhere else rather
than write a registry no manager will ever open.

The source is read with plain `sqlite3` rather than by importing auth-service's
models, so this does not couple the two packages and keeps working if
auth-service's schema moves on.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sqlite3
import sys

_DEFAULT_SOURCE = "data/auth-service/auth.db"
_DEFAULT_CONFIG = "runtime/manager/manager.toml"

# Copied verbatim. `hourly_cost_usd` is not among them: RM-62 superseded it with
# the two components, and the total is computed on read — carrying the stale
# column over would create a second answer to the same question.
_COLUMNS = (
    "id",
    "name",
    "manager_url",
    "node_type",
    "tag",
    "is_active",
    "hardware_amortization_usd_per_hour",
    "electricity_usd_per_hour",
    "price_margin_multiplier",
    "engines",
    "created_at",
    "updated_at",
)


def _read_source(path: pathlib.Path) -> list[dict]:
    if not path.exists():
        print(f"FAIL: source database not found: {path}")
        print(
            "      Pass --source if auth-service's database is elsewhere; `lsof` on the"
        )
        print("      running process shows which file it actually opened.")
        raise SystemExit(2)

    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        tables = {
            r[0]
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        if "nodes" not in tables:
            print(
                f"FAIL: {path} has no `nodes` table. Is this auth-service's database?"
            )
            raise SystemExit(2)
        present = {r[1] for r in conn.execute("PRAGMA table_info(nodes)")}
        if missing := [c for c in _COLUMNS if c not in present]:
            print(f"FAIL: source `nodes` is missing columns this expects: {missing}")
            print(
                "      auth-service's schema changed; update _COLUMNS before running."
            )
            raise SystemExit(2)
        cols = ", ".join(_COLUMNS)
        return [
            dict(r)
            for r in conn.execute(f"SELECT {cols} FROM nodes ORDER BY created_at")
        ]
    finally:
        conn.close()


def _engines(raw: object, name: str) -> list[str] | None:
    """Parsed, or None. Never `[]` on a parse failure.

    PRM-133: `[]` means "this node declared it has no engines" and NULL means
    "never declared". Turning unparseable JSON into `[]` would invent a
    declaration the operator never made.
    """
    if raw is None:
        return None
    try:
        parsed = json.loads(str(raw))
    except (TypeError, json.JSONDecodeError):
        print(f"  WARN {name}: `engines` is not valid JSON — carried over as NULL")
        return None
    return [str(x) for x in parsed] if isinstance(parsed, list) else None


def _migrate(rows: list[dict], *, dry_run: bool, config: pathlib.Path | None) -> int:
    from prometheus_manager_core.config import load_config
    from prometheus_manager_core.fleet import FleetRegistry, Node, NodeExistsError

    # The coordinator's own manager.toml, not the built-in defaults: whether this
    # node is the coordinator and where its fleet.db lives are both in that file,
    # and `load_config(None)` would answer "not the coordinator" for everyone.
    cfg = load_config(config)
    if not cfg.fleet.coordinator:
        print("\nFAIL: this manager is not the fleet coordinator.")
        print(
            "      Set `[fleet] coordinator = true` in its manager.toml, or run this on"
        )
        print(
            "      the node that is. Writing a fleet registry no manager will open is"
        )
        print("      worse than not writing one.")
        return 2

    target = cfg.resolved_fleet_path
    print(f"  target: {target}")
    fleet = FleetRegistry(target)
    try:
        existing = {node.name for node in fleet.list()}

        copied: list[str] = []
        skipped: list[str] = []
        for row in rows:
            name = str(row["name"])
            if name in existing:
                skipped.append(name)
                continue

            node = Node(
                id=str(row["id"]),
                name=name,
                manager_url=str(row["manager_url"]),
                node_type=str(row["node_type"]),
                tag=row["tag"],
                is_active=bool(row["is_active"]),
                hardware_amortization_usd_per_hour=row[
                    "hardware_amortization_usd_per_hour"
                ],
                electricity_usd_per_hour=row["electricity_usd_per_hour"],
                price_margin_multiplier=row["price_margin_multiplier"],
                engines=_engines(row["engines"], name),
                created_at=str(row["created_at"] or ""),
                updated_at=str(row["updated_at"]) if row["updated_at"] else None,
            )
            if not dry_run:
                try:
                    fleet.add(node)
                except NodeExistsError:
                    skipped.append(name)
                    continue
            copied.append(name)

        verb = "would copy" if dry_run else "copied"
        print(
            f"\n  {verb}: {len(copied)}" + (f" — {', '.join(copied)}" if copied else "")
        )
        if skipped:
            print(f"  already in the coordinator, left untouched: {', '.join(skipped)}")

        if not dry_run and copied:
            # Read back rather than trust the write: the only check that the rows
            # are queryable through the path the coordinator will actually use.
            after = {n.name: n for n in fleet.list()}
            if unverified := [n for n in copied if n not in after]:
                print(f"\nFAIL: wrote but cannot read back: {unverified}")
                return 1
            print(
                "  verified: every copied node reads back through FleetRegistry.list()"
            )
            for name in copied:
                node_read = after[name]
                print(
                    f"    {node_read.name:10} {node_read.manager_url:32} "
                    f"active={node_read.is_active} engines={node_read.engines}"
                )
    finally:
        fleet.close()

    print("\n  auth-service's `nodes` table is untouched — that is the rollback path.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="PRM-134 node registry migration")
    parser.add_argument(
        "--source", default=_DEFAULT_SOURCE, help=f"default: {_DEFAULT_SOURCE}"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="report what would be copied, write nothing",
    )
    parser.add_argument(
        "--config",
        default=_DEFAULT_CONFIG,
        help=f"the coordinator's manager.toml (default: {_DEFAULT_CONFIG})",
    )
    args = parser.parse_args()

    source = pathlib.Path(args.source)
    rows = _read_source(source)
    print(f"  source: {source}  ({len(rows)} node(s))")
    for row in rows:
        print(
            f"    {row['name']:10} {row['manager_url']:32} active={bool(row['is_active'])}"
        )

    if not rows:
        print("\n  nothing to migrate")
        return 0

    return _migrate(rows, dry_run=args.dry_run, config=pathlib.Path(args.config))


if __name__ == "__main__":
    sys.exit(main())
