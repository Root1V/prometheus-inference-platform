"""Alembic in auth-service — PRM-153.

Before this, schema changes here were a frozen list of `ALTER TABLE ... ADD
COLUMN` statements run on every startup with the exception swallowed. That works
for adding a column and cannot express anything else: no drop, no rename, no type
change, no constraint. The concrete consequence is that PRM-134 left a dead
`nodes` table behind and this service had no mechanism to remove it.

The hard part is not running migrations on a fresh database — it is adopting the
databases that already exist, with live principals in them, without destroying or
refusing them. That is what most of this file tests.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from prometheus_auth.db import _BASELINE_REVISION, create_tables, init_db_engine
from sqlalchemy import inspect

pytestmark = pytest.mark.asyncio


def _tables(path: Path) -> set[str]:
    conn = sqlite3.connect(path)
    try:
        return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        conn.close()


def _version(path: Path) -> str | None:
    conn = sqlite3.connect(path)
    try:
        if "alembic_version" not in _tables(path):
            return None
        row = conn.execute("SELECT version_num FROM alembic_version").fetchone()
        return row[0] if row else None
    finally:
        conn.close()


async def _migrate(path: Path) -> None:
    engine = init_db_engine(f"sqlite+aiosqlite:///{path}")
    try:
        await create_tables(engine)
    finally:
        await engine.dispose()


# ── Case 1: a brand-new database ─────────────────────────────────────────────


async def test_a_new_database_is_built_and_stamped(tmp_path: Path):
    path = tmp_path / "new.db"
    await _migrate(path)

    assert {"principals", "credential_share_tokens"} <= _tables(path)
    assert _version(path) is not None, "a migrated database must be under version control"


async def test_the_models_and_the_migrations_agree(tmp_path: Path):
    """The failure this catches: a column added to a model and not to a revision.

    A model change with no migration works on a fresh database — `create_all`
    would build it — and breaks on every existing one. Comparing the migrated
    schema against the metadata is what makes the two impossible to drift apart
    silently.
    """
    from prometheus_auth.db import Base

    path = tmp_path / "compare.db"
    await _migrate(path)

    engine = init_db_engine(f"sqlite+aiosqlite:///{path}")
    try:
        async with engine.connect() as conn:
            actual = await conn.run_sync(
                lambda c: {
                    t: {col["name"] for col in inspect(c).get_columns(t)}
                    for t in inspect(c).get_table_names()
                    if t != "alembic_version"
                }
            )
    finally:
        await engine.dispose()

    for table in Base.metadata.sorted_tables:
        assert table.name in actual, f"{table.name} is in the models and not in the migrations"
        expected = {c.name for c in table.columns}
        missing = expected - actual[table.name]
        assert not missing, (
            f"{table.name} is missing {sorted(missing)} — a model changed without a revision. "
            "New schema changes are revisions; the additive ALTER list is frozen."
        )


# ── Case 2: adopting a database that predates Alembic ────────────────────────


def _legacy_database(path: Path) -> None:
    """A pre-Alembic auth.db with a live principal in it.

    Deliberately missing the columns the frozen ALTER list adds, so this
    exercises the real adoption path rather than a convenient subset.
    """
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE principals (
            client_id VARCHAR(36) NOT NULL PRIMARY KEY,
            client_name VARCHAR(255) NOT NULL,
            client_secret_hash VARCHAR(60),
            role VARCHAR(9) NOT NULL,
            allowed_scopes TEXT NOT NULL,
            token_ttl_seconds INTEGER NOT NULL,
            created_at DATETIME NOT NULL,
            is_active BOOLEAN NOT NULL,
            revoked_at DATETIME
        );
        INSERT INTO principals VALUES
            ('c-1','legacy client','$2b$12$hash','app','inference:read',300,
             '2026-01-01 00:00:00',1,NULL);
        """
    )
    conn.commit()
    conn.close()


async def test_a_pre_alembic_database_is_adopted_not_rebuilt(tmp_path: Path):
    """The case that makes this more than `upgrade head`.

    Such a database is already at the baseline in everything but name, so running
    the baseline against it would fail on "table already exists". It is lifted to
    the baseline shape, stamped, and upgraded — and its rows survive, which is the
    only thing that actually matters.
    """
    path = tmp_path / "legacy.db"
    _legacy_database(path)
    assert _version(path) is None

    await _migrate(path)

    assert _version(path) is not None
    conn = sqlite3.connect(path)
    try:
        assert conn.execute("SELECT count(*) FROM principals").fetchone()[0] == 1, (
            "the live principal was lost while adopting the database"
        )
        name = conn.execute("SELECT client_name FROM principals").fetchone()[0]
        assert name == "legacy client"
        # The columns the frozen ALTER list exists to add.
        columns = {r[1] for r in conn.execute("PRAGMA table_info(principals)")}
        assert {"label", "updated_at", "auth_method", "email", "password_hash"} <= columns
    finally:
        conn.close()


async def test_adoption_stamps_the_baseline_before_upgrading(tmp_path: Path):
    """Stamped at the baseline rather than at head: any revision after the
    baseline still has to run, because a pre-Alembic database has not seen it."""
    path = tmp_path / "stamped.db"
    _legacy_database(path)
    await _migrate(path)
    # With the baseline as the only revision, head *is* the baseline. The
    # assertion is that it was stamped at all rather than left unmanaged.
    assert _version(path) == _BASELINE_REVISION


# ── Case 3: already managed, and idempotent ──────────────────────────────────


async def test_running_twice_changes_nothing(tmp_path: Path):
    path = tmp_path / "twice.db"
    await _migrate(path)
    first = (_tables(path), _version(path))
    await _migrate(path)
    assert (_tables(path), _version(path)) == first


async def test_an_already_managed_database_is_not_re_adopted(tmp_path: Path):
    """The adoption branch must be entered only once — a second pass through it
    would try to stamp a database that already has a version."""
    path = tmp_path / "managed.db"
    _legacy_database(path)
    await _migrate(path)
    version_after_adoption = _version(path)

    await _migrate(path)
    assert _version(path) == version_after_adoption


# ── What the frozen list could not do, and this can ──────────────────────────


async def test_the_additive_list_is_frozen_and_says_so():
    """The list is kept for one job — lifting a pre-Alembic database — and adding
    to it would put schema changes back outside version control."""
    from prometheus_auth import db

    source = Path(db.__file__).read_text()
    assert "this list is frozen" in source.lower() or "list is frozen" in source.lower(), (
        "the note explaining that _ADDITIVE_MIGRATIONS takes no new entries is gone"
    )
    # Every statement in it is an ADD COLUMN. Anything else belongs in a revision.
    for stmt in db._ADDITIVE_MIGRATIONS:
        assert "ADD COLUMN" in stmt, f"not an additive statement: {stmt!r}"
