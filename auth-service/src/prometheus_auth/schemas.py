# See memory/specs/005-auth-service.md — Pydantic schemas
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from .db import ClientRole

# ── Platform scopes (fixed enum) ─────────────────────────────────────────────
# Implements: memory/specs/005-auth-service.md — Q3 (resolved)
VALID_SCOPES: frozenset[str] = frozenset(
    {
        "inference:read",
        "inference:stream",
        "admin:read",  # gateway admin endpoints (/v1/backends, /v1/usage)
        "admin:models",
        "admin:usage",
        "backend-registry:read",  # Manager API — memory/specs/008-llama-server-manager.md — AC-13
        "ui:chat",  # Web Chat UI access — memory/specs/013-web-chat-ui-proxy.md — AC-6
        "ops:dashboard",  # Grafana ops dashboard — memory/specs/021-ops-observability-stack.md — AC-6
    }
)


# ── Admin request / response schemas ─────────────────────────────────────────


class CreateClientRequest(BaseModel):
    client_name: str = Field(..., min_length=1, max_length=255)
    role: ClientRole
    allowed_scopes: list[str] = Field(..., min_length=1)
    # See memory/specs/015-auth-service-dashboard.md — AC-1
    label: str | None = Field(None, max_length=255)

    model_config = {"use_enum_values": True}


class CreateClientResponse(BaseModel):
    client_id: str
    client_secret: str  # pmt_live_ prefixed — shown once only
    client_name: str
    role: str
    allowed_scopes: list[str]
    token_ttl_seconds: int


class ClientListItem(BaseModel):
    client_id: str
    client_name: str
    # See memory/specs/015-auth-service-dashboard.md — AC-2
    label: str | None = None
    role: str
    allowed_scopes: list[str]
    token_ttl_seconds: int
    is_active: bool
    created_at: datetime
    # See memory/specs/015-auth-service-dashboard.md — AC-27
    updated_at: datetime | None = None


# ── Spec-015: update / reactivate schemas ────────────────────────────────────


class UpdateClientRequest(BaseModel):
    """Partial update — only supplied fields are changed. See memory/specs/015-auth-service-dashboard.md — AC-3."""

    client_name: str | None = Field(None, min_length=1, max_length=255)
    label: str | None = Field(None, max_length=255)
    allowed_scopes: list[str] | None = None
    token_ttl_seconds: int | None = Field(None, ge=60, le=86400)


class ReactivateResponse(BaseModel):
    client_id: str
    is_active: bool


class RotateSecretResponse(BaseModel):
    client_id: str
    client_secret: str  # new secret — shown once only


# ── OAuth2 token schemas ──────────────────────────────────────────────────────


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    scope: str


# ── Error helpers (RFC 6749 §5.2) ────────────────────────────────────────────


class OAuth2Error(BaseModel):
    error: str
    error_description: str
