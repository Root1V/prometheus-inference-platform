# See memory/specs/005-auth-service.md — Data Model
# See memory/specs/016-credential-share-link.md — CredentialShareToken
import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.ext.asyncio import AsyncAttrs, AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker


class ClientRole(str, enum.Enum):
    admin = "admin"  # TTL: 3h   — internal tooling
    cognitive = "cognitive"  # TTL: 1h   — long-running pipelines
    agent = "agent"  # TTL: 10m  — autonomous agents
    app = "app"  # TTL: 5m   — interactive applications


class Base(AsyncAttrs, DeclarativeBase):
    pass


class OAuthClient(Base):
    """Persistent client registry.

    Implements: memory/specs/005-auth-service.md — Data Model / oauth_clients table
    """

    __tablename__ = "oauth_clients"

    client_id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    client_name: Mapped[str] = mapped_column(String(255), nullable=False)
    client_secret_hash: Mapped[str] = mapped_column(String(60), nullable=False)
    role: Mapped[ClientRole] = mapped_column(Enum(ClientRole), nullable=False)
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
    # TODO(015): For persistent DBs, add an Alembic migration instead of relying on create_all.
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
        ForeignKey("oauth_clients.client_id", ondelete="CASCADE"),
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


async def create_tables(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    # Additive migrations — safe to re-run; errors mean the column already exists
    _ADDITIVE_MIGRATIONS = [
        "ALTER TABLE oauth_clients ADD COLUMN label TEXT",
        "ALTER TABLE oauth_clients ADD COLUMN updated_at DATETIME",
    ]
    async with engine.begin() as conn:
        for stmt in _ADDITIVE_MIGRATIONS:
            try:
                await conn.execute(text(stmt))
            except Exception:
                pass  # column already present
