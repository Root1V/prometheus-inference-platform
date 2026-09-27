# See memory/specs/005-auth-service.md — Data Model
# See memory/specs/016-credential-share-link.md — CredentialShareToken
import enum
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    inspect,
    text,
)
from sqlalchemy.ext.asyncio import AsyncAttrs, AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker


class PrincipalRole(str, enum.Enum):
    admin = "admin"  # TTL: 3h   — internal tooling
    cognitive = "cognitive"  # TTL: 1h   — long-running pipelines
    agent = "agent"  # TTL: 10m  — autonomous agents
    app = "app"  # TTL: 5m   — interactive applications


class Base(AsyncAttrs, DeclarativeBase):
    pass


class Principal(Base):
    """Persistent principal registry — machine clients (OAuth2) and human users (password).

    Implements: memory/specs/005-auth-service.md — Data Model / oauth_clients table
    Implements: docs/roadmap.md — RM-11 (unified principals, dual auth_method)
    """

    __tablename__ = "principals"

    client_id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    client_name: Mapped[str] = mapped_column(String(255), nullable=False)
    # "oauth2" (client_id/client_secret) or "password" (email/password)
    auth_method: Mapped[str] = mapped_column(String(16), nullable=False, default="oauth2")
    client_secret_hash: Mapped[str | None] = mapped_column(String(60), nullable=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True, unique=True)
    password_hash: Mapped[str | None] = mapped_column(String(60), nullable=True)
    role: Mapped[PrincipalRole] = mapped_column(Enum(PrincipalRole), nullable=False)
    allowed_scopes: Mapped[str] = mapped_column(Text, nullable=False)  # space-separated
    token_ttl_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # See memory/specs/015-auth-service-dashboard.md — AC-1: free-text owner/component tag
    label: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    # See memory/specs/015-auth-service-dashboard.md — AC-27: updated_at set on every mutation
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )

    @property
    def scopes(self) -> list[str]:
        return [s for s in self.allowed_scopes.split() if s]


class CredentialShareToken(Base):
    """Single-use credential delivery token.

    Implements: memory/specs/016-credential-share-link.md — Data Model
    """

    __tablename__ = "credential_share_tokens"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    token: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    client_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("principals.client_id", ondelete="CASCADE"),
        nullable=False,
    )
    client_name: Mapped[str] = mapped_column(String(255), nullable=False)
    client_id_value: Mapped[str] = mapped_column(String(36), nullable=False)
    secret_plaintext_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    used_by_ip: Mapped[str | None] = mapped_column(String(45), nullable=True)
    used_by_ua: Mapped[str | None] = mapped_column(Text, nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_by: Mapped[str | None] = mapped_column(String(64), nullable=True)

    __table_args__ = (Index("ix_share_tokens_client_id", "client_id"),)


# PRM-154: `NodeType`, the three cost defaults and the `Node` model stood here.
# PRM-134 moved the fleet's node list to the coordinator manager-api and left them
# as the rollback path; the revision that drops the table closes that window. The
# cost figures went with the model — they are a node's properties, and the
# coordinator carries its own copies in `prometheus_manager_core.fleet`, where the
# service that owns nodes can keep them.
#
# A node row touched no principal, no token and no scope. This service owns
# security and now owns only security.


# ── Engine and session factory ────────────────────────────────────────────────

_engine: AsyncEngine | None = None
_async_session: sessionmaker | None = None  # type: ignore[type-arg]


def init_db_engine(db_url: str) -> AsyncEngine:
    global _engine, _async_session
    _engine = create_async_engine(db_url, echo=False)
    _async_session = sessionmaker(_engine, class_=AsyncSession, expire_on_commit=False)  # type: ignore[call-overload]
    return _engine


def get_engine() -> AsyncEngine:
    if _engine is None:
        raise RuntimeError("Database engine not initialised. Call init_db_engine() first.")
    return _engine


def get_session_factory() -> sessionmaker:  # type: ignore[type-arg]
    if _async_session is None:
        raise RuntimeError("Database engine not initialised. Call init_db_engine() first.")
    return _async_session


async def _migrate_oauth_clients_to_principals(engine: AsyncEngine) -> None:
    """One-time data migration from the old oauth_clients table (RM-11).

    Base.metadata.create_all only creates tables that don't exist yet — it never
    alters an existing table's constraints. Since `principals` needs
    `client_secret_hash` to be nullable (SQLite can't relax an existing NOT NULL
    column via ADD COLUMN), we let create_all build the new `principals` table
    fresh, then copy any pre-existing oauth_clients rows into it here, then drop
    the old table. Idempotent: after the first successful run, oauth_clients no
    longer exists, so every later call is a no-op.
    """
    async with engine.begin() as conn:
        old_columns = await conn.run_sync(
            lambda c: (
                {col["name"] for col in inspect(c).get_columns("oauth_clients")}
                if inspect(c).has_table("oauth_clients")
                else None
            )
        )
        if old_columns is None:
            return
        # label/updated_at were themselves added after the fact on very old DBs —
        # default to NULL if a given source DB predates them.
        label_src = "label" if "label" in old_columns else "NULL"
        updated_at_src = "updated_at" if "updated_at" in old_columns else "NULL"
        await conn.execute(
            text(
                "INSERT INTO principals "
                "(client_id, client_name, client_secret_hash, role, allowed_scopes, "
                " token_ttl_seconds, created_at, is_active, revoked_at, label, updated_at, "
                " auth_method, email, password_hash) "
                "SELECT client_id, client_name, client_secret_hash, role, allowed_scopes, "
                f" token_ttl_seconds, created_at, is_active, revoked_at, {label_src}, {updated_at_src}, "
                " 'oauth2', NULL, NULL FROM oauth_clients "
                "WHERE client_id NOT IN (SELECT client_id FROM principals)"
            )
        )
        await conn.execute(text("DROP TABLE oauth_clients"))


# PRM-153: the revision describing what every database created before Alembic
# already contains. A pre-Alembic database is lifted to exactly this shape and
# stamped, rather than having the baseline run against tables that already exist.
_BASELINE_REVISION = "f7060a106063"
_ALEMBIC_INI = Path(__file__).resolve().parents[2] / "alembic.ini"


def _migrate_to_head(sync_conn: Any) -> None:
    """Bring one connection's database to the latest revision — PRM-153.

    Three cases, and the middle one is the whole reason this is not just
    `upgrade(head)`:

    * **New database** — no tables. Alembic runs every revision, baseline
      included.
    * **Pre-Alembic database** — has tables and no `alembic_version`. It is
      already at the baseline in everything but name, so running the baseline
      would fail on "table already exists". Instead it is lifted to the baseline
      shape (`create_all` adds any table it never had; the additive ALTERs below
      add the columns `create_all` cannot), stamped, and then upgraded normally.
    * **Already managed** — upgraded to head.

    Without the middle case the first real migration would refuse to touch, or
    destroy, a database holding live principals.

    Same three cases and the same shape as the gateway's `_migrate_to_head`
    (RM-68). Copied rather than shared: two ~40-line functions differing in their
    metadata and their baseline id do not justify a migration framework, and the
    tests assert they behave alike.
    """
    import logging

    # Alembic announces each autogenerate plugin it loads at INFO — noise on every
    # boot that says nothing an operator needs. Registration happens while
    # `alembic` is imported, so this has to come first. Migration events stay.
    logging.getLogger("alembic.runtime.plugins").setLevel(logging.WARNING)

    from alembic import command
    from alembic.config import Config

    config = Config(str(_ALEMBIC_INI))
    # Hands env.py the transaction we are already inside, so the schema change
    # and the version bump commit together.
    config.attributes["connection"] = sync_conn

    tables = set(inspect(sync_conn).get_table_names())
    if tables and "alembic_version" not in tables:
        Base.metadata.create_all(sync_conn)
        _apply_additive_migrations(sync_conn)
        command.stamp(config, _BASELINE_REVISION)

    command.upgrade(config, "head")


def _apply_additive_migrations(sync_conn: Any) -> None:
    """The pre-Alembic mechanism, kept for exactly one job — PRM-153.

    These ALTERs are how columns were added before migrations existed, and they
    are the only way to lift a database that predates them to the baseline shape:
    `create_all` builds missing *tables* and never touches an existing one's
    columns. So they stay, run once during adoption, and are not added to.

    **Nothing new goes in this list.** It cannot express a drop, a rename or a
    type change — which is why auth-service could not remove the dead `nodes`
    table PRM-134 left behind, and why this item exists. New schema changes are
    revisions.
    """
    for stmt in _ADDITIVE_MIGRATIONS:
        try:
            sync_conn.execute(text(stmt))
        except Exception:  # noqa: BLE001 — the column is already there
            pass


async def create_tables(engine: AsyncEngine) -> None:
    """Apply pending migrations. Named for its callers, which predate PRM-153."""
    await _migrate_oauth_clients_to_principals_if_needed(engine)
    async with engine.begin() as conn:
        await conn.run_sync(_migrate_to_head)


async def _migrate_oauth_clients_to_principals_if_needed(engine: AsyncEngine) -> None:
    """RM-11's one-time data move, which has to run before Alembic adopts the DB.

    It needs `principals` to exist and `oauth_clients` to still be there, which is
    a pre-baseline state. Left as it was rather than rewritten as a revision: it
    is idempotent, it has been running for months, and turning a working data
    migration into a schema revision would risk the rows it moves for no gain.
    """
    async with engine.begin() as conn:
        has_old = await conn.run_sync(lambda c: inspect(c).has_table("oauth_clients"))
    if not has_old:
        return
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await _migrate_oauth_clients_to_principals(engine)


# Additive migrations — safe to re-run; an error means the column already exists.
# See _apply_additive_migrations: this list is frozen.
_ADDITIVE_MIGRATIONS = [
    "ALTER TABLE principals ADD COLUMN label TEXT",
    "ALTER TABLE principals ADD COLUMN updated_at DATETIME",
    "ALTER TABLE principals ADD COLUMN auth_method TEXT NOT NULL DEFAULT 'oauth2'",
    "ALTER TABLE principals ADD COLUMN email TEXT",
    "ALTER TABLE principals ADD COLUMN password_hash TEXT",
    # PRM-154: six `ALTER TABLE nodes` entries stood here. They lifted a
    # pre-Alembic database to the baseline shape, and there is no longer a table
    # for them to lift — the revision that drops it runs immediately after this
    # list, on the same adoption path. Removing them earlier than the drop would
    # have been wrong: a database adopted between the two would have arrived at
    # the baseline missing the columns the baseline declares.
]
