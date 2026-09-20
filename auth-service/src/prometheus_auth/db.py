# See memory/specs/005-auth-service.md — Data Model
# See memory/specs/016-credential-share-link.md — CredentialShareToken
import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    Float,
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


class NodeType(str, enum.Enum):
    mac = "mac"
    nvidia = "nvidia"
    other = "other"


# RM-62 follow-up: platform defaults for a node's cost fields, applied when
# the operator leaves them blank at creation — derived from a MacBook Pro M4
# Max (~$8,100 amortized over a 3-year lifespan) + ~70W sustained inference
# load at Lima, Peru's highest residential electricity tier. Editable per
# node any time; these are just the starting point for a new one.
DEFAULT_HARDWARE_AMORTIZATION_USD_PER_HOUR = 0.3082
DEFAULT_ELECTRICITY_USD_PER_HOUR = 0.0146
DEFAULT_PRICE_MARGIN_MULTIPLIER = 1.3


class Node(Base):
    """Inference manager node inventory.

    Implements: docs/roadmap.md — RM-20 (replaces the gateway's static MANAGER_NODES).
    """

    __tablename__ = "nodes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    # The name used in dashboard URLs (e.g. /admin/api/nodes/{name}/models) — unique.
    name: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    manager_url: Mapped[str] = mapped_column(String(512), nullable=False)
    node_type: Mapped[NodeType] = mapped_column(Enum(NodeType), nullable=False)
    tag: Mapped[str | None] = mapped_column(Text, nullable=True)
    # RM-62: superseded by the 3 fields below (kept, unused, per this
    # codebase's additive-only migration convention — see _ADDITIVE_MIGRATIONS).
    hourly_cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    # RM-62 follow-up: the $/hour total is the sum of these two, each entered
    # by the operator (nothing in this codebase can derive them) — split so
    # either can be adjusted independently (e.g. an electricity rate change
    # without re-estimating hardware amortization). Never left unconfigured:
    # a blank value at creation gets the platform default above, so every
    # node always has a real total for the price-suggestion calculator.
    hardware_amortization_usd_per_hour: Mapped[float] = mapped_column(
        Float, nullable=False, default=DEFAULT_HARDWARE_AMORTIZATION_USD_PER_HOUR
    )
    electricity_usd_per_hour: Mapped[float] = mapped_column(
        Float, nullable=False, default=DEFAULT_ELECTRICITY_USD_PER_HOUR
    )
    # Multiplier applied over this node's break-even cost by the Model
    # Pricing table's "suggest price" calculator — per-node since different
    # hardware/markets may warrant a different margin.
    price_margin_multiplier: Mapped[float] = mapped_column(
        Float, nullable=False, default=DEFAULT_PRICE_MARGIN_MULTIPLIER
    )
    # PRM-133: which inference engines are installed on this node, as a JSON
    # array of engine ids ("llama_cpp", "mlx", ...). Three states, and the
    # difference between the last two is the whole point (RM-98):
    #   NULL  — never declared. Predates this column, or the operator skipped
    #           it. The instance form must not read this as "everything", which
    #           is what it did before the column existed.
    #   '[]'  — declared, and the answer is none. A node that can hold models
    #           but cannot launch one.
    #   '[..]'— declared.
    # Deliberately not validated against a list of known engines here:
    # auth-service is the identity and node registry, it does not know what an
    # inference engine is, and a second copy of that list would drift from the
    # manager's own. The UI offers only real ones and intersects what it reads
    # with them, so an unrecognised string can never become a selectable option.
    engines: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    # Set by a connectivity check (GET {manager_url}/health) at creation, on
    # manager_url changes, and via /check and /activate (activate can't just
    # flip this to True — it re-probes and only succeeds if reachable, so the
    # badge never lies about a node being reachable when it isn't). /deactivate
    # is the one true manual override — no probe — for taking a reachable node
    # out of rotation on demand (e.g. maintenance).
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )


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


async def create_tables(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await _migrate_oauth_clients_to_principals(engine)
    # Additive migrations — safe to re-run; errors mean the column already exists
    _ADDITIVE_MIGRATIONS = [
        "ALTER TABLE principals ADD COLUMN label TEXT",
        "ALTER TABLE principals ADD COLUMN updated_at DATETIME",
        "ALTER TABLE principals ADD COLUMN auth_method TEXT NOT NULL DEFAULT 'oauth2'",
        "ALTER TABLE principals ADD COLUMN email TEXT",
        "ALTER TABLE principals ADD COLUMN password_hash TEXT",
        "ALTER TABLE nodes ADD COLUMN is_active BOOLEAN NOT NULL DEFAULT 1",
        "ALTER TABLE nodes ADD COLUMN hourly_cost_usd FLOAT",
        "ALTER TABLE nodes ADD COLUMN hardware_amortization_usd_per_hour FLOAT NOT NULL "
        f"DEFAULT {DEFAULT_HARDWARE_AMORTIZATION_USD_PER_HOUR}",
        "ALTER TABLE nodes ADD COLUMN electricity_usd_per_hour FLOAT NOT NULL "
        f"DEFAULT {DEFAULT_ELECTRICITY_USD_PER_HOUR}",
        "ALTER TABLE nodes ADD COLUMN price_margin_multiplier FLOAT NOT NULL "
        f"DEFAULT {DEFAULT_PRICE_MARGIN_MULTIPLIER}",
        "ALTER TABLE nodes ADD COLUMN engines TEXT",
    ]
    async with engine.begin() as conn:
        for stmt in _ADDITIVE_MIGRATIONS:
            try:
                await conn.execute(text(stmt))
            except Exception:
                pass  # column already present
