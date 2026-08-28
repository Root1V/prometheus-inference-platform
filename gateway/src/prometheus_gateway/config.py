from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Resolve .env relative to this file's location (gateway/src/prometheus_gateway/)
# so the app finds it regardless of the working directory.
_ENV_FILE = Path(__file__).parent.parent.parent / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(_ENV_FILE),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── JWT ──────────────────────────────────────────────────────────────────
    # [MEDIUM fix] No default — misconfigured deployments must fail at startup,
    # not silently accept tokens from an attacker-controlled issuer.
    jwt_issuer: str
    jwt_audience: str = "prometheus-gateway"

    # Exactly one of these is required
    jwt_public_key_file: str | None = None
    jwt_jwks_url: str | None = None

    jwt_clock_skew_seconds: int = 30

    # Token revocation (optional — omit to disable)
    jwt_revocation_redis_url: str | None = None
    jwt_revocation_strict: bool = True  # fail-closed when Redis is unavailable

    # ── Rate Limiting — memory/specs/007-rate-limiting-and-throughput.md ────────────
    # AC-1, AC-2, AC-3, AC-4, AC-9, AC-13
    rate_limit_redis_url: str | None = None  # defaults to jwt_revocation_redis_url if unset
    rate_limit_rpm: int = 60  # global requests-per-minute per client_id / user_id
    rate_limit_tpm: int = 40_000  # global tokens-per-minute per client_id / user_id
    rate_limit_strict: bool = True  # fail-closed when Redis unavailable for rate limiting
    # Per-endpoint overrides (AC-13)
    rate_limit_rpm_chat_completions: int | None = None
    rate_limit_tpm_chat_completions: int | None = None

    # ── Circuit Breaker — memory/specs/007-rate-limiting-and-throughput.md ──────────
    # AC-14, AC-15, AC-16
    circuit_breaker_failure_threshold: int = 5
    circuit_breaker_recovery_timeout: int = 30  # seconds
    circuit_breaker_success_threshold: int = 2

    # ── Backend Retry — memory/specs/007-rate-limiting-and-throughput.md ────────────
    # AC-17
    backend_retry_max: int = 2
    backend_retry_backoff_base_ms: int = 200

    # ── Backend ──────────────────────────────────────────────────────────────
    # NOTE: LLAMA_CPP_URL is deprecated. Backend URLs are now configured per-model
    # via the backend_url field in runtime/models/registry.yaml.
    # Implements: memory/specs/006-multi-model-gateway.md — AC-10

    # Path to runtime/models/registry.yaml — relative to repo root or absolute
    # Implements: memory/specs/001-gateway-core.md — AC-5
    model_registry_path: str | None = None

    # ── Manager integration — memory/specs/008-llama-server-manager.md — AC-23 ─────
    # When admin_dashboard_enabled=True, the gateway polls the Manager REST API for
    # the backend registry instead of reading registry.yaml directly. Node topology
    # itself (RM-20) lives in auth-service's node registry, not here — see
    # auth_service_admin_url below and admin/nodes_client.py's fetch_nodes(). Each
    # node's own manager-api must be configured with PMGR_PROXY_HOST set to its own
    # network-reachable hostname/IP (not left as loopback) so its /v1/backends
    # response reports a backend_url the gateway can actually route to.
    manager_poll_interval_s: int = 30
    # OAuth2 client credentials for authenticating against the Manager REST API.
    # The gateway obtains and auto-renews a token with scope: backend-registry:read.
    # Register once: POST /admin/clients {"allowed_scopes": ["backend-registry:read"]}
    # Shared across all nodes — every node's manager-api validates against the same
    # central auth-service, so one service-account token works for all.
    manager_client_id: str | None = None
    manager_client_secret: str | None = None
    # Deprecated: static JWT — use manager_client_id + manager_client_secret instead.
    manager_jwt: str | None = None

    # ── Web Chat UI — memory/specs/013-web-chat-ui-proxy.md ─────────────────────────
    # AC-1: feature flag — when False all /ui/* routes return 404
    ui_enabled: bool = False
    # AC-4, AC-9: session cookie configuration
    ui_session_cookie_name: str = "prometheus_session"
    ui_session_cookie_max_age: int = 0  # 0 = follow JWT exp
    # AC-11: login rate limiting per source IP
    ui_login_rate_limit_rpm: int = 10
    # Required when ui_enabled=True — auth-service token endpoint
    auth_service_token_url: str | None = None
    # TLS verification for internal calls to the auth-service.
    # Set to False when using self-signed certificates (dev/Podman stack).
    auth_service_tls_verify: bool = True
    # AC-14, AC-15: TLS termination (both must be set together or both absent)
    gateway_tls_cert_file: str | None = None
    gateway_tls_key_file: str | None = None

    # ── Admin dashboard — docs/roadmap.md RM-10 ───────────────────────────────
    # Feature flag — when False, /admin/* routes return 404 (same pattern as
    # ui_enabled). The dashboard SPA calls /admin/api/*, which proxies to the
    # Manager REST API using the same manager_client_id/secret credentials as
    # ManagerRegistrySync — that service account needs backend-registry:write
    # in addition to its existing backend-registry:read grant.
    admin_dashboard_enabled: bool = False

    # ── Users section — docs/roadmap.md RM-11 ─────────────────────────────────
    # /admin/api/users/* proxies to auth-service's /admin/clients/* using the
    # same static X-Admin-Key auth-service itself requires — distinct from the
    # OAuth2 client_credentials manager_client uses, since auth-service's admin
    # API predates and doesn't use the platform's own token scheme.
    auth_service_admin_url: str | None = None
    auth_service_admin_api_key: str | None = None

    @model_validator(mode="after")
    def validate_admin_dashboard_requirements(self) -> "Settings":
        """RM-20: node topology now lives in auth-service's registry, fetched via
        this same admin credential — so it's required whenever the dashboard
        (and therefore manager-node integration) is enabled, not just for Users.
        """
        if self.admin_dashboard_enabled and not (
            self.auth_service_admin_url and self.auth_service_admin_api_key
        ):
            raise ValueError(
                "AUTH_SERVICE_ADMIN_URL and AUTH_SERVICE_ADMIN_API_KEY are required "
                "when ADMIN_DASHBOARD_ENABLED=true."
            )
        return self

    @model_validator(mode="after")
    def require_key_source(self) -> "Settings":
        if not self.jwt_public_key_file and not self.jwt_jwks_url:
            raise ValueError("Either JWT_PUBLIC_KEY_FILE or JWT_JWKS_URL must be configured.")
        return self

    @model_validator(mode="after")
    def validate_tls_pair(self) -> "Settings":
        """Both TLS files must be set together or both absent — AC-15."""
        cert = bool(self.gateway_tls_cert_file)
        key = bool(self.gateway_tls_key_file)
        if cert != key:
            missing = "GATEWAY_TLS_KEY_FILE" if cert else "GATEWAY_TLS_CERT_FILE"
            raise ValueError(
                f"{missing} must be set when the other TLS variable is configured. "
                "Both GATEWAY_TLS_CERT_FILE and GATEWAY_TLS_KEY_FILE must be present together."
            )
        return self

    # ── Usage tracking — docs/roadmap.md RM-32 ─────────────────────────────────
    # Replaces the old Redis daily-TTL usage counters with real persisted history
    # and a per-model dimension. SQLite by default, same pattern as auth-service's
    # AUTH_DB_URL — swap for a Postgres URL in production if desired.
    gateway_db_url: str = "sqlite+aiosqlite:///./gateway.db"

    # ── Observability links — docs/roadmap.md RM-31 ─────────────────────────────
    # Grafana's URL (e.g. http://localhost:3000) — Tempo has no separately exposed
    # UI in podman-compose.yml, trace search lives inside Grafana's Explore view
    # against the Tempo datasource. Unset means "not deployed" — the Overview
    # page's links row is omitted entirely rather than guessing a fragile URL.
    grafana_url: str | None = None

    # ── Pricing — docs/roadmap.md RM-33 ─────────────────────────────────────────
    # Optional per-model USD pricing (see gateway/pricing.yaml.example). Path is
    # relative to repo root or absolute; unset means "no pricing configured" —
    # GET /v1/usage's estimated_cost_usd is null for every model, not $0.
    pricing_file: str | None = None

    # ── Observability — memory/specs/018-observability-telemetry.md ────────────────
    log_level: str = "INFO"
    log_file_path: str | None = None
    log_max_bytes: int = 10_485_760  # 10 MB
    log_backup_count: int = 5
    # AC-29: prompt/response summaries are opt-in (privacy-by-default)
    log_include_prompt_summary: bool = False

    @model_validator(mode="after")
    def validate_ui_requirements(self) -> "Settings":
        """auth_service_token_url is required when ui_enabled=True."""
        if self.ui_enabled and not self.auth_service_token_url:
            raise ValueError("AUTH_SERVICE_TOKEN_URL is required when UI_ENABLED=true.")
        return self

    @property
    def effective_rate_limit_redis_url(self) -> str | None:
        """Return the Redis URL to use for rate limiting.

        Falls back to jwt_revocation_redis_url so simple deployments only need one URL.
        Implements: memory/specs/007-rate-limiting-and-throughput.md — Data Model
        """
        return self.rate_limit_redis_url or self.jwt_revocation_redis_url
