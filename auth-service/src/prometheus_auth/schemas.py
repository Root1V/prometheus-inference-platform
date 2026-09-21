# See memory/specs/005-auth-service.md — Pydantic schemas
from __future__ import annotations

import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from .db import NodeType, PrincipalRole

# ── Platform scopes (fixed enum) ─────────────────────────────────────────────
# Implements: memory/specs/005-auth-service.md — Q3 (resolved)
VALID_SCOPES: frozenset[str] = frozenset(
    {
        "inference:read",
        "inference:stream",
        "admin:read",  # gateway admin endpoints (/v1/backends, /v1/usage)
        "admin:write",  # RM-10 — gateway admin dashboard: register/start/stop/restart via /admin/api
        "admin:models",
        "admin:usage",
        "backend-registry:read",  # Manager API — memory/specs/008-llama-server-manager.md — AC-13
        "backend-registry:write",  # RM-10 — Manager API register/deregister/start/stop/restart
        "ui:chat",  # Web Chat UI access — memory/specs/013-web-chat-ui-proxy.md — AC-6
    }
)

# ── Per-model scopes (RM-07) ──────────────────────────────────────────────────
# Fine-grained model access, additive to inference:read/inference:stream above.
# "model:<id>" grants access to one model_id (the same identifiers used in
# runtime/manager registry.yaml). Deny-by-default: a client with NO model:*
# scope cannot call ANY model — see docs/roadmap.md RM-07 and
# docs/roadmap.md RM-07 for the enforcement details and migration note.
# Not part of VALID_SCOPES (that set is a fixed enum) — matched by pattern
# instead, since the set of model ids is open-ended and lives in the
# manager's registry, not in auth-service. Case-insensitive with dots allowed
# — real model ids commonly carry a version number (e.g. "qwen2.5-7b-q4") or a
# mixed-case variant tag (e.g. "qwen3vl-32B-Q4").
_MODEL_SCOPE_RE = re.compile(r"^model:[a-zA-Z0-9][a-zA-Z0-9._-]*$")


def is_valid_scope(scope: str) -> bool:
    """True if *scope* is a known platform scope or a well-formed `model:<id>` grant."""
    return scope in VALID_SCOPES or bool(_MODEL_SCOPE_RE.match(scope))


def invalid_scopes(scopes: list[str] | set[str]) -> set[str]:
    """Return the subset of *scopes* that fail `is_valid_scope`."""
    return {s for s in scopes if not is_valid_scope(s)}


# ── Admin request / response schemas ─────────────────────────────────────────

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class CreatePrincipalRequest(BaseModel):
    """Implements: docs/roadmap.md — RM-11 (auth_method: oauth2 | password)."""

    client_name: str = Field(..., min_length=1, max_length=255)
    role: PrincipalRole
    allowed_scopes: list[str] = Field(..., min_length=1)
    # See memory/specs/015-auth-service-dashboard.md — AC-1
    label: str | None = Field(None, max_length=255)
    auth_method: Literal["oauth2", "password"] = "oauth2"
    email: str | None = Field(None, max_length=255)
    password: str | None = Field(None, min_length=8)

    model_config = {"use_enum_values": True}

    @model_validator(mode="after")
    def _validate_auth_method(self) -> "CreatePrincipalRequest":
        if self.auth_method == "oauth2":
            if self.email is not None or self.password is not None:
                raise ValueError("email/password must not be set when auth_method is oauth2")
        else:
            if not self.email or not _EMAIL_RE.match(self.email):
                raise ValueError("a valid email is required when auth_method is password")
            if not self.password:
                raise ValueError("password is required when auth_method is password")
        return self


class CreatePrincipalResponse(BaseModel):
    client_id: str
    client_name: str
    role: str
    allowed_scopes: list[str]
    token_ttl_seconds: int
    auth_method: str
    email: str | None = None
    # Shown once only: client_secret for oauth2, echoes the caller-supplied
    # password for password principals (never re-derivable afterwards).
    client_secret: str | None = None


class PrincipalListItem(BaseModel):
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
    auth_method: str
    email: str | None = None


# ── Spec-015: update / reactivate schemas ────────────────────────────────────


class UpdatePrincipalRequest(BaseModel):
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


class ResetPasswordResponse(BaseModel):
    client_id: str
    password: str  # new password — shown once only


class GenerateShareLinkRequest(BaseModel):
    """Implements: docs/roadmap.md — RM-11 (credential share link, ported from admin_ui.py)."""

    secret: str = Field(..., min_length=1)


class ShareLinkResponse(BaseModel):
    share_url: str
    expires_at: datetime


class RevokeShareLinkResponse(BaseModel):
    token_id: str
    revoked: bool


# ── Node registry (RM-20) ──────────────────────────────────────────────────────

# PRM-133: an engine id, bounded. This is a shape check, not a membership one —
# auth-service has no list of real engines and should not grow one (see
# db.py's Node.engines). It exists so the column holds identifiers rather than
# arbitrary text.
_ENGINE_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")


def _validate_engines(value: list[str] | None) -> list[str] | None:
    if value is None:
        return None
    seen: list[str] = []
    for item in value:
        if not _ENGINE_ID_RE.match(item):
            raise ValueError(
                f"Invalid engine id {item!r}: lowercase letters, digits, '_' and '-', "
                "starting with a letter, at most 32 characters."
            )
        if item not in seen:
            seen.append(item)
    return seen


class CreateNodeRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    manager_url: str = Field(..., min_length=1, max_length=512)
    node_type: NodeType
    tag: str | None = Field(None, max_length=255)
    # RM-62 follow-up: the two $/hour components (amortization + electricity)
    # and the price-suggestion margin — each entered by the operator, each
    # falling back to a platform default (db.py's DEFAULT_* constants) when
    # left blank, so a node is never left without a usable total.
    hardware_amortization_usd_per_hour: float | None = Field(None, ge=0)
    electricity_usd_per_hour: float | None = Field(None, ge=0)
    price_margin_multiplier: float | None = Field(None, gt=0)
    # PRM-133: the inference engines installed on this node. Omitted means
    # "not declared", which is not the same as `[]` — see db.py's Node.engines.
    engines: list[str] | None = Field(None, max_length=32)

    _check_engines = field_validator("engines")(_validate_engines)

    model_config = {"use_enum_values": True}


class NodeListItem(BaseModel):
    id: str
    name: str
    manager_url: str
    node_type: str
    tag: str | None = None
    is_active: bool
    hardware_amortization_usd_per_hour: float
    electricity_usd_per_hour: float
    price_margin_multiplier: float
    # Computed = hardware_amortization_usd_per_hour + electricity_usd_per_hour
    # — read-only convenience for callers that just want the total.
    hourly_cost_usd: float
    # PRM-133: null means the node has never declared its engines; [] means it
    # declared that it has none. A caller that collapses the two is the bug.
    engines: list[str] | None = None
    created_at: datetime
    updated_at: datetime | None = None


class UpdateNodeRequest(BaseModel):
    """Partial update — only supplied fields are changed. `name` is immutable."""

    manager_url: str | None = Field(None, min_length=1, max_length=512)
    node_type: NodeType | None = None
    tag: str | None = Field(None, max_length=255)
    hardware_amortization_usd_per_hour: float | None = Field(None, ge=0)
    electricity_usd_per_hour: float | None = Field(None, ge=0)
    price_margin_multiplier: float | None = Field(None, gt=0)
    # PRM-133: sending `[]` clears the list to "declared none"; omitting the
    # field leaves it alone. Both are meaningful, so this one is read through
    # `model_fields_set` like `tag` above rather than an `is not None` check.
    engines: list[str] | None = Field(None, max_length=32)

    _check_engines = field_validator("engines")(_validate_engines)

    model_config = {"use_enum_values": True}


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
