"""Versioned schema migrations — PRM-155.

manager-core kept two SQLite databases in shape with `CREATE TABLE IF NOT EXISTS`
plus `ALTER TABLE` guarded by a `PRAGMA table_info` check. That is schema
*inference*, not migration: `registry.db` had no version table at all, so nothing
could say what a given file had seen, the guards accumulate for ever, and a change
interrupted half-way left no trace of how far it got.

These tests pin the three properties that fixes: changes are recorded, applied
exactly once, and can express anything SQLite can — including the `DROP COLUMN`
the old mechanism could not.
"""

from __future__ import annotations

import sqlite3

import pytest

from prometheus_manager_core import schema
from prometheus_manager_core.schema import BASELINE_VERSION, Migration


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    return conn


def _baseline(conn: sqlite3.Connection) -> None:
    conn.executescript("CREATE TABLE t (id INTEGER PRIMARY KEY, keep TEXT, dead TEXT);")
    conn.commit()


def _columns(conn: sqlite3.Connection, table: str = "t") -> set[str]:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def _version(conn: sqlite3.Connection, chain: str = "c") -> int | None:
    row = conn.execute("SELECT version FROM schema_version WHERE chain = ?", (chain,)).fetchone()
    return int(row[0]) if row else None


# ── The version is recorded, which it never was before ───────────────────────


def test_an_unstamped_database_is_stamped_at_the_baseline():
    conn = _db()
    _baseline(conn)
    assert schema.apply(conn, "c", (), had_existing_tables=True) == BASELINE_VERSION
    assert _version(conn) == BASELINE_VERSION


def test_a_new_database_is_also_stamped_at_the_baseline():
    """Not Alembic's rule, and the difference is deliberate: there the baseline
    revision holds the DDL, so a new database runs it. Here the schema script runs
    unconditionally, so both paths arrive at the baseline before this is called."""
    conn = _db()
    _baseline(conn)
    assert schema.apply(conn, "c", (), had_existing_tables=False) == BASELINE_VERSION


def test_applying_migrations_advances_and_records_the_version():
    conn = _db()
    _baseline(conn)
    migrations = (
        Migration(2, "add one", ("ALTER TABLE t ADD COLUMN added_a TEXT",)),
        Migration(3, "add another", ("ALTER TABLE t ADD COLUMN added_b TEXT",)),
    )
    assert schema.apply(conn, "c", migrations, had_existing_tables=True) == 3
    assert _version(conn) == 3
    assert {"added_a", "added_b"} <= _columns(conn)


def test_a_migration_already_applied_is_not_applied_again():
    """The property the old mechanism got by re-checking PRAGMA every startup, now
    got by remembering."""
    conn = _db()
    _baseline(conn)
    migrations = (Migration(2, "add", ("ALTER TABLE t ADD COLUMN added TEXT",)),)
    schema.apply(conn, "c", migrations, had_existing_tables=True)
    # Re-running would raise "duplicate column name" if it ran twice.
    assert schema.apply(conn, "c", migrations, had_existing_tables=True) == 2


def test_only_migrations_above_the_current_version_run():
    conn = _db()
    _baseline(conn)
    schema.apply(
        conn,
        "c",
        (Migration(2, "first", ("ALTER TABLE t ADD COLUMN a TEXT",)),),
        had_existing_tables=True,
    )
    # Version 2 is already applied; only 3 should run, and 2's statement would
    # fail if it did.
    schema.apply(
        conn,
        "c",
        (
            Migration(2, "first", ("ALTER TABLE t ADD COLUMN a TEXT",)),
            Migration(3, "second", ("ALTER TABLE t ADD COLUMN b TEXT",)),
        ),
        had_existing_tables=True,
    )
    assert _version(conn) == 3


# ── What the old mechanism could not express ─────────────────────────────────


def test_a_migration_can_drop_a_column():
    """The whole point. `ALTER TABLE ... ADD COLUMN` guarded by PRAGMA cannot
    remove anything, which is why a dead column or table had nowhere to go."""
    conn = _db()
    _baseline(conn)
    assert "dead" in _columns(conn)

    schema.apply(
        conn,
        "c",
        (Migration(2, "drop the dead column", ("ALTER TABLE t DROP COLUMN dead",)),),
        had_existing_tables=True,
    )
    assert "dead" not in _columns(conn)
    assert "keep" in _columns(conn), "the wrong column was dropped"


def test_a_migration_can_move_data_while_changing_shape():
    conn = _db()
    _baseline(conn)
    conn.execute("INSERT INTO t (keep, dead) VALUES ('a', 'old')")
    conn.commit()

    schema.apply(
        conn,
        "c",
        (
            Migration(
                2,
                "carry the value over then drop",
                (
                    "ALTER TABLE t ADD COLUMN moved TEXT",
                    "UPDATE t SET moved = dead",
                    "ALTER TABLE t DROP COLUMN dead",
                ),
            ),
        ),
        had_existing_tables=True,
    )
    assert conn.execute("SELECT moved FROM t").fetchone()[0] == "old"
    assert "dead" not in _columns(conn)


# ── A failure leaves a version the database actually reached ──────────────────


def test_a_failed_migration_rolls_back_and_leaves_the_previous_version():
    """The failure the old mechanism could not even detect: no transaction around
    "make the change and record it", so an interrupted ALTER was invisible."""
    conn = _db()
    _baseline(conn)
    schema.apply(conn, "c", (), had_existing_tables=True)

    broken = (
        Migration(
            2,
            "half valid",
            ("ALTER TABLE t ADD COLUMN fine TEXT", "ALTER TABLE nonexistent ADD COLUMN x TEXT"),
        ),
    )
    with pytest.raises(sqlite3.OperationalError):
        schema.apply(conn, "c", broken, had_existing_tables=True)

    assert _version(conn) == BASELINE_VERSION, "the version advanced past a migration that failed"
    assert "fine" not in _columns(conn), "the first statement was not rolled back"


def test_a_later_run_retries_the_failed_migration():
    conn = _db()
    _baseline(conn)
    with pytest.raises(sqlite3.OperationalError):
        schema.apply(
            conn,
            "c",
            (Migration(2, "broken", ("ALTER TABLE nope ADD COLUMN x TEXT",)),),
            had_existing_tables=True,
        )
    # Fixed and re-run: still pending, so it applies.
    assert (
        schema.apply(
            conn,
            "c",
            (Migration(2, "fixed", ("ALTER TABLE t ADD COLUMN x TEXT",)),),
            had_existing_tables=True,
        )
        == 2
    )


# ── A chain that would misapply is refused before it runs ────────────────────


def test_duplicate_versions_are_refused():
    with pytest.raises(ValueError, match="unique and ascending"):
        schema.validate((Migration(2, "a", ()), Migration(2, "b", ())))


def test_out_of_order_versions_are_refused():
    with pytest.raises(ValueError, match="unique and ascending"):
        schema.validate((Migration(3, "a", ()), Migration(2, "b", ())))


def test_a_version_at_or_below_the_baseline_is_refused():
    """It would never run: an adopted database is already stamped at the baseline,
    so such a migration would silently apply to new databases only."""
    with pytest.raises(ValueError, match="above the baseline"):
        schema.validate((Migration(BASELINE_VERSION, "collides", ()),))


def test_an_empty_chain_is_valid():
    schema.validate(())


# ── Both real chains are declared and valid ──────────────────────────────────


def test_the_registry_and_fleet_chains_are_valid():
    """They are empty today, and the guard has to pass on empty as well as on
    whatever they grow into — otherwise the first real migration is also the first
    time this is exercised."""
    from prometheus_manager_core import fleet, registry

    schema.validate(registry._MIGRATIONS)
    schema.validate(fleet._MIGRATIONS)


def test_the_two_chains_are_recorded_separately(tmp_path):
    """Each database has its own file, so one row each — but the chain name is
    what makes a mistake a constraint violation rather than one database silently
    overwriting the other's version."""
    from prometheus_manager_core.fleet import FleetRegistry
    from prometheus_manager_core.registry import Registry

    fleet_reg = FleetRegistry(tmp_path / "fleet.db")
    model_reg = Registry(tmp_path / "registry.db")
    try:
        for path, chain in (
            (tmp_path / "fleet.db", "fleet"),
            (tmp_path / "registry.db", "registry"),
        ):
            conn = sqlite3.connect(path)
            try:
                rows = list(conn.execute("SELECT chain, version FROM schema_version"))
            finally:
                conn.close()
            assert rows == [(chain, BASELINE_VERSION)]
    finally:
        fleet_reg.close()
        del model_reg
