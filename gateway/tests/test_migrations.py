"""Alembic migration tests — docs/roadmap.md RM-68.

The one that matters most is `test_migrations_match_the_models`: without it
nothing stops a model change from shipping without its migration, and the
mismatch would only surface on a real deployment, against real data.
"""

from __future__ import annotations

import sqlite3

import pytest
import sqlalchemy
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext

from prometheus_gateway import db

pytestmark = pytest.mark.asyncio


def _tables(path: str) -> set[str]:
    conn = sqlite3.connect(path)
    try:
        return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        conn.close()


def _stamped_revision(path: str) -> str | None:
    conn = sqlite3.connect(path)
    try:
        row = conn.execute("SELECT version_num FROM alembic_version").fetchone()
        return None if row is None else str(row[0])
    finally:
        conn.close()


async def test_fresh_database_is_migrated_to_head(tmp_path):
    path = f"{tmp_path}/fresh.db"
    db.init_db_engine(f"sqlite+aiosqlite:///{path}")
    await db.create_tables(db.get_engine())

    tables = _tables(path)
    assert "usage_events" in tables
    assert "alembic_version" in tables
    assert _stamped_revision(path) is not None


async def test_pre_alembic_database_is_adopted_without_losing_data(tmp_path):
    """The case that made RM-68 necessary: a database created before migrations
    existed already holds the baseline schema, so replaying the baseline
    revision over it would fail. It must be stamped instead — with its rows
    untouched.
    """
    path = f"{tmp_path}/legacy.db"

    # Build the *baseline* schema, then strip alembic_version — which is
    # exactly what a database created before migrations existed looks like.
    # Deliberately not create_all(): that builds today's models, so the
    # simulation would already contain columns later migrations add, and the
    # test would pass for the wrong reason.
    db.init_db_engine(f"sqlite+aiosqlite:///{path}")
    await db.create_tables(db.get_engine())
    stripped = sqlite3.connect(path)
    stripped.execute("DROP TABLE alembic_version")
    for column in ("instance_id",):  # columns added after the baseline
        stripped.execute(f"ALTER TABLE usage_events DROP COLUMN {column}")
    stripped.commit()
    stripped.close()

    conn = sqlite3.connect(path)
    conn.execute(
        "INSERT INTO model_price_config (model_id, prompt_price_per_1m, updated_at) "
        "VALUES ('legacy-model', 1.5, '2026-01-01 00:00:00')"
    )
    conn.commit()
    conn.close()
    assert "alembic_version" not in _tables(path)

    db.init_db_engine(f"sqlite+aiosqlite:///{path}")
    await db.create_tables(db.get_engine())

    assert _stamped_revision(path) is not None
    surviving = (
        sqlite3.connect(path)
        .execute(
            "SELECT prompt_price_per_1m FROM model_price_config WHERE model_id = 'legacy-model'"
        )
        .fetchone()
    )
    assert surviving == (1.5,)


async def test_running_migrations_twice_is_a_no_op(tmp_path):
    path = f"{tmp_path}/twice.db"
    db.init_db_engine(f"sqlite+aiosqlite:///{path}")
    await db.create_tables(db.get_engine())
    first = _stamped_revision(path)

    await db.create_tables(db.get_engine())
    assert _stamped_revision(path) == first


async def test_migrations_match_the_models(tmp_path):
    """Guards against a model edit shipping without its migration.

    Migrating an empty database and then diffing the result against the
    SQLAlchemy metadata must produce no changes. If this fails, run:

        uv run --project gateway alembic revision --autogenerate -m "..."
    """
    path = f"{tmp_path}/drift.db"
    db.init_db_engine(f"sqlite+aiosqlite:///{path}")
    await db.create_tables(db.get_engine())

    sync_engine = sqlalchemy.create_engine(f"sqlite:///{path}")
    with sync_engine.connect() as conn:
        context = MigrationContext.configure(conn, opts={"compare_type": True})
        diff = compare_metadata(context, db.Base.metadata)
    sync_engine.dispose()

    assert diff == [], f"Models and migrations disagree: {diff}"
