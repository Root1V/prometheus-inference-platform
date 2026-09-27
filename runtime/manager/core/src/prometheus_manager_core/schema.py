"""Versioned schema migrations for manager-core's SQLite databases — PRM-155.

Implements: docs/roadmap.md — PRM-155.

## Why not Alembic

The gateway and auth-service both use Alembic, and copying that here would be the
wrong answer. manager-core talks to SQLite through the stdlib `sqlite3` module and
has **no SQLAlchemy dependency at all** — deliberately, for a component that runs
on every node. Alembic would add SQLAlchemy plus Alembic to it, and without
declarative models there is no `--autogenerate`, which is most of what Alembic
buys. What would arrive is the machinery and not the benefit.

So the same *property* is built with the tools this module already has: schema
changes that are numbered, ordered, applied exactly once, recorded, and able to
express anything SQLite can do — including `DROP COLUMN`, which SQLite has
supported since 3.35 and which the mechanism this replaces could not express at
all.

## What it replaces, and why that was not migration

`registry.db` and `fleet.db` were kept in shape by `CREATE TABLE IF NOT EXISTS`
plus `ALTER TABLE` guarded by a `PRAGMA table_info` check. That is schema
*inference*: every startup re-examines the database and works out what to do.
Three consequences, and the third is the one that matters.

1. **No record of what was applied.** `registry.db` had no version table, so
   nothing could say which changes a given file had seen.
2. **The guards accumulate for ever**, because each is the only evidence its
   change exists.
3. **A crash mid-change leaves no trace of how far it got.** There is no
   transaction boundary around "make the change and record it", so an interrupted
   ALTER is indistinguishable from one that never ran — and on a schema where the
   next startup re-infers, indistinguishable is the same as invisible.

Here each migration and its version bump commit **together**, so a database is
always at a version it actually reached.

## Adoption

A database with tables and no `schema_version` is already at the baseline — the
old mechanism put it there — so it is stamped at the baseline rather than having
the baseline's statements run against tables that already exist. Same reasoning as
the gateway's RM-68 and auth-service's PRM-153: without it the first real
migration would refuse or damage a database holding live rows.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime

from .telemetry import get_logger

logger = get_logger(__name__)

# The version every database created by the pre-migration mechanism is already at.
BASELINE_VERSION = 1

_VERSION_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS schema_version (
    -- One row per chain. Each database has its own file, so in practice one row;
    -- the column exists so a mistake is a constraint violation rather than a
    -- silent overwrite of another chain's version.
    chain      TEXT PRIMARY KEY,
    version    INTEGER NOT NULL,
    applied_at TEXT NOT NULL
);
"""


@dataclass(frozen=True)
class Migration:
    """One numbered, ordered schema change.

    `statements` is plain SQL because that is what this module speaks. A
    migration that needs to move data can be expressed the same way — SQLite's
    `INSERT ... SELECT` covers it — and anything beyond that is a sign the change
    should be split.
    """

    version: int
    name: str
    statements: tuple[str, ...]


def _current_version(conn: sqlite3.Connection, chain: str) -> int | None:
    row = conn.execute("SELECT version FROM schema_version WHERE chain = ?", (chain,)).fetchone()
    return int(row[0]) if row else None


def _record(conn: sqlite3.Connection, chain: str, version: int) -> None:
    conn.execute(
        "INSERT INTO schema_version (chain, version, applied_at) VALUES (?, ?, ?) "
        "ON CONFLICT(chain) DO UPDATE SET version = excluded.version, "
        "applied_at = excluded.applied_at",
        (chain, version, datetime.now(UTC).isoformat()),
    )


def apply(
    conn: sqlite3.Connection,
    chain: str,
    migrations: tuple[Migration, ...],
    *,
    had_existing_tables: bool,
) -> int:
    """Bring one database to the latest version. Returns the version it is at.

    An unstamped database is stamped at the baseline, whether it was created a
    moment ago or has been in use for months. That is not the same as Alembic's
    rule and the difference is worth stating: there, the baseline *revision holds
    the DDL*, so a new database runs it and an existing one is stamped to avoid
    running it twice. Here the schema script runs unconditionally with
    `IF NOT EXISTS`, so both paths arrive at the baseline shape before this is
    called, and stamping is all that is left to do either way.

    `had_existing_tables` therefore says nothing about *what* to do — it is
    recorded because an operator wants to know, once, that a database already in
    use came under version control.

    Every migration runs inside a transaction with its own version bump, so the
    two cannot disagree. That is the failure the mechanism this replaces could not
    even detect.
    """
    conn.executescript(_VERSION_TABLE_SQL)
    conn.commit()

    version = _current_version(conn, chain)
    if version is None:
        version = BASELINE_VERSION
        _record(conn, chain, version)
        conn.commit()
        logger.info("schema.initialised", chain=chain, version=version, adopted=had_existing_tables)

    pending = sorted((m for m in migrations if m.version > version), key=lambda m: m.version)
    for migration in pending:
        try:
            # An explicit BEGIN, and it is load-bearing. Python's sqlite3 opens a
            # transaction implicitly for INSERT/UPDATE/DELETE and **not for DDL**,
            # so an `ALTER TABLE` runs in autocommit and cannot be rolled back.
            # Without this line a migration whose second statement fails leaves
            # the first one applied while the version stays put — which is
            # precisely the half-applied state this mechanism exists to make
            # impossible. Found by the test for it.
            conn.execute("BEGIN")
            for statement in migration.statements:
                conn.execute(statement)
            _record(conn, chain, migration.version)
            conn.execute("COMMIT")
        except Exception:
            # Leave the database at the last version it actually reached rather
            # than half-way into this one.
            conn.rollback()
            logger.error(
                "schema.migration_failed",
                chain=chain,
                version=migration.version,
                name=migration.name,
                at_version=version,
            )
            raise
        version = migration.version
        logger.info("schema.migrated", chain=chain, version=migration.version, name=migration.name)

    return version


def validate(migrations: tuple[Migration, ...]) -> None:
    """Refuse a chain that is not strictly increasing from the baseline.

    A duplicated or out-of-order version would apply the wrong statements to the
    wrong databases depending on how far each had got, which is the kind of bug
    that only shows up on somebody else's machine.
    """
    versions = [m.version for m in migrations]
    if versions != sorted(set(versions)):
        raise ValueError(f"migration versions must be unique and ascending, got {versions}")
    if versions and versions[0] <= BASELINE_VERSION:
        raise ValueError(
            f"migration versions must start above the baseline ({BASELINE_VERSION}), "
            f"got {versions[0]}"
        )
