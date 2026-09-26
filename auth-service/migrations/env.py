"""Alembic environment for the auth-service database.

Implements: docs/roadmap.md — PRM-153.

Copied deliberately from the gateway's `migrations/env.py` rather than
generalised into a shared package. The two are ~60 lines each, they differ only
in the model metadata and the environment variable they read, and a shared
"migration framework" for two services would be an abstraction with one more
consumer than it deserves. What matters is that they behave the same way, and
that is asserted by tests rather than by shared code.

Runs in two modes, sharing one configuration:

* **CLI** (`alembic upgrade head`) — builds its own engine from `AUTH_DB_URL`.
* **In-process** (`db.create_tables()` at startup) — reuses the connection the
  caller already opened, passed via `config.attributes["connection"]`.

The URL comes from the environment rather than `alembic.ini` so the CLI and the
application can never disagree about which database they are migrating.
"""

from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from prometheus_auth.db import Base
from sqlalchemy import Connection, engine_from_config, pool

config = context.config

if config.config_file_name is not None and not config.attributes.get("connection"):
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# Same default as Settings.auth_db_url — keep the two in sync.
_DEFAULT_DB_URL = "sqlite+aiosqlite:///./auth.db"


def _database_url() -> str:
    return os.environ.get("AUTH_DB_URL", _DEFAULT_DB_URL)


def _sync_url(url: str) -> str:
    """Alembic drives migrations synchronously; drop the async driver.

    The service talks to SQLite through aiosqlite and to Postgres through
    asyncpg, and neither is needed to run DDL — using the sync driver keeps this
    file free of an event loop it would otherwise have to own from the CLI.
    """
    return url.replace("+aiosqlite", "").replace("+asyncpg", "+psycopg")


def _configure(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        # SQLite cannot ALTER a column in place; batch mode rewrites the table
        # instead. Harmless on Postgres, essential here — and it is what makes a
        # non-additive change expressible at all, which the ALTER TABLE list this
        # replaces could not do.
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
        # In-process: the caller owns the transaction, so the schema change and
        # the version bump commit together.
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
