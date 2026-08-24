# See memory/specs/005-auth-service.md
from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_ENV_FILE = Path(__file__).parent.parent.parent / ".env"

# Per-role default TTLs (seconds) — see memory/specs/005-auth-service.md § Client Roles
ROLE_DEFAULT_TTL: dict[str, int] = {
    "admin": 10800,  # 3 hours
    "cognitive": 3600,  # 1 hour
    "agent": 600,  # 10 minutes
    "app": 300,  # 5 minutes
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(_ENV_FILE),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── RSA keys ────────────────────────────────────────────────────────────
    auth_private_key_file: str
    auth_public_key_file: str
    auth_active_kid: str = "default"

    # ── JWT ─────────────────────────────────────────────────────────────────
    auth_jwt_issuer: str
    auth_jwt_audience: str = "prometheus-gateway"

    # ── Admin ────────────────────────────────────────────────────────────────
    # AC-19: required at startup — missing value fails with clear error
    auth_admin_api_key: str

    # ── Database ─────────────────────────────────────────────────────────────
    auth_db_url: str = "sqlite+aiosqlite:///./auth.db"

    # ── Redis revocation ─────────────────────────────────────────────────────
    auth_revocation_redis_url: str | None = None

    # ── Rate limiting ────────────────────────────────────────────────────────
    auth_rate_limit_rpm: int = 10

    # ── Per-role TTLs (overridable via env) ──────────────────────────────────
    auth_ttl_admin_seconds: int = 10800
    auth_ttl_cognitive_seconds: int = 3600
    auth_ttl_agent_seconds: int = 600
    auth_ttl_app_seconds: int = 300

    # ── Observability — memory/specs/018-observability-telemetry.md ────────────────
    log_level: str = "INFO"
    log_file_path: str | None = None
    log_max_bytes: int = 10_485_760  # 10 MB
    log_backup_count: int = 5

    # ── TLS (memory/specs/017) ──────────────────────────────────────────────────────
    # Optional — both must be set to activate TLS; absent = HTTP (dev fallback)
    auth_tls_cert_file: str = ""
    auth_tls_key_file: str = ""

    # ── Credential share links (memory/specs/016) ───────────────────────────────────
    # AC-15: required; AC-16: must be exactly 64 hex chars (32 bytes)
    share_token_encryption_key: str = ""
    # AC-4: must not exceed 86400 s (24 h); default 3600 s (1 h)
    share_token_ttl_seconds: int = 3600

    @model_validator(mode="after")
    def _validate_required(self) -> "Settings":
        # AUTH_ADMIN_API_KEY is validated by pydantic (no default) — this adds
        # a human-readable error for the most common misconfiguration.
        if not self.auth_admin_api_key:
            raise ValueError(
                "AUTH_ADMIN_API_KEY is required but not set. "
                "Generate one with: openssl rand -hex 32"
            )
        # AC-15: SHARE_TOKEN_ENCRYPTION_KEY must be present
        if not self.share_token_encryption_key:
            raise ValueError(
                "SHARE_TOKEN_ENCRYPTION_KEY is required but not set. "
                "Generate one with: openssl rand -hex 32"
            )
        # AC-16: must be exactly 64 hex chars (= 32 bytes)
        if len(self.share_token_encryption_key) != 64:
            raise ValueError(
                "SHARE_TOKEN_ENCRYPTION_KEY must be exactly 64 hex characters (32 bytes). "
                f"Got {len(self.share_token_encryption_key)} characters."
            )
        try:
            bytes.fromhex(self.share_token_encryption_key)
        except ValueError:
            raise ValueError("SHARE_TOKEN_ENCRYPTION_KEY must be a valid hex string.")
        # AC-4: TTL must not exceed 24 h
        if self.share_token_ttl_seconds > 86400:
            raise ValueError(
                f"SHARE_TOKEN_TTL_SECONDS must not exceed 86400 (24 hours). "
                f"Got {self.share_token_ttl_seconds}."
            )
        return self

    def ttl_for_role(self, role: str) -> int:
        """Return configured TTL in seconds for the given role."""
        mapping = {
            "admin": self.auth_ttl_admin_seconds,
            "cognitive": self.auth_ttl_cognitive_seconds,
            "agent": self.auth_ttl_agent_seconds,
            "app": self.auth_ttl_app_seconds,
        }
        return mapping.get(role, self.auth_ttl_app_seconds)
