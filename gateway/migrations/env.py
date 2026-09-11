"""Alembic environment for the gateway database.

Implements: docs/roadmap.md — RM-68

Runs in two modes, deliberately sharing one configuration:

* **CLI** (`alembic upgrade head`) — builds its own engine from `GATEWAY_DB_URL`.
* **In-process** (`db.create_tables()` at startup) — reuses the connection the
  caller already opened, passed via `config.attributes["connection"]`.

The URL is read from the environment rather than `alembic.ini` so the CLI and
the application can never disagree about which database they are migrating.
"""

from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import Connection, engine_from_config, pool

from prometheus_gateway.db import Base

config = context.config

if config.config_file_name is not None and not config.attributes.get("connection"):
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# Same default as Settings.gateway_db_url — keep the two in sync.
_DEFAULT_DB_URL = "sqlite+aiosqlite:///./gateway.db"


def _database_url() -> str:
    return os.environ.get("GATEWAY_DB_URL", _DEFAULT_DB_URL)


def _sync_url(url: str) -> str:
    """Alembic drives migrations synchronously; drop the async driver.

    The application talks to SQLite through aiosqlite and to Postgres through
    asyncpg, but neither driver is needed to run DDL — and using the sync
    driver here keeps env.py free of an event loop it would otherwise have to
    own when invoked from the CLI.
    """
    return url.replace("+aiosqlite", "").replace("+asyncpg", "+psycopg")


def _configure(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        # SQLite cannot ALTER a column in place; batch mode rewrites the table
        # instead. Harmless on Postgres, essential here.
        render_as_batch=connection.dialect.name == "sqlite",
        compare_type=True,
    )


def run_migrations_offline() -> None:
    context.configure(
        url=_sync_url(_database_url()),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connection: Connection | None = config.attributes.get("connection")
    if connection is not None:
        # In-process: the caller owns the transaction.
        _configure(connection)
        context.run_migrations()
        return

    engine = engine_from_config(
        {"sqlalchemy.url": _sync_url(_database_url())},
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with engine.connect() as conn:
        _configure(conn)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
