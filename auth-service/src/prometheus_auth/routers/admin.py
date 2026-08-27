# See memory/specs/005-auth-service.md — /admin/clients endpoints
# Implements: AC-6 (create), AC-7 (revoke), AC-8 (auth), AC-11, AC-12, AC-13, AC-14, AC-15
# Implements: memory/specs/018-observability-telemetry.md — AC-2, AC-12, AC-13
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import bcrypt
import httpx
import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import APIKeyHeader
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import CredentialShareToken, Node, NodeType, Principal, PrincipalRole, get_session_factory
from ..schemas import (
    CreateNodeRequest,
    CreatePrincipalRequest,
    CreatePrincipalResponse,
    GenerateShareLinkRequest,
    NodeListItem,
    PrincipalListItem,
    ReactivateResponse,
    ResetPasswordResponse,
    RevokeShareLinkResponse,
    RotateSecretResponse,
    ShareLinkResponse,
    UpdateNodeRequest,
    UpdatePrincipalRequest,
    invalid_scopes,
)
from ..share_crypto import encrypt_secret
from ..telemetry import get_logger, get_tracer

logger = get_logger(__name__)
_tracer = get_tracer("auth-service.admin")

router = APIRouter(prefix="/admin", tags=["admin"])

_admin_key_header = APIKeyHeader(name="X-Admin-Key", auto_error=False)


def _hash_secret(plain: str) -> str:
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt(rounds=12)).decode()


def _verify_secret(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode(), hashed.encode())


# ── Dependencies ──────────────────────────────────────────────────────────────


async def _get_db() -> AsyncSession:  # type: ignore[misc]
    async with get_session_factory()() as session:
        yield session


async def _require_admin(
    request: Request,
    api_key: str | None = Depends(_admin_key_header),
) -> None:
    """AC-8 / AC-14: validate X-Admin-Key header."""
    settings = request.app.state.settings
    if not api_key or not secrets.compare_digest(api_key, settings.auth_admin_api_key):
        raise HTTPException(status_code=403, detail="Invalid or missing admin key.")


# ── Register client ───────────────────────────────────────────────────────────


@router.post(
    "/clients", response_model=CreatePrincipalResponse, dependencies=[Depends(_require_admin)]
)
async def create_client(
    body: CreatePrincipalRequest,
    request: Request,
    db: AsyncSession = Depends(_get_db),
) -> Any:
    """Register a new principal (oauth2 client or password user).

    Implements: memory/specs/005-auth-service.md — AC-11.
    Implements: memory/specs/022-opentelemetry-sdk-instrumentation.md — G-12, AC-16
    Implements: docs/roadmap.md — RM-11
    """
    from opentelemetry.trace import SpanKind

    with _tracer.start_as_current_span("client.create", kind=SpanKind.INTERNAL) as span:
        # Validate scopes
        invalid = invalid_scopes(body.allowed_scopes)
        if invalid:
            span.set_attribute("http.status_code", 422)
            raise HTTPException(
                status_code=422, detail=f"Unknown scope(s): {', '.join(sorted(invalid))}"
            )

        settings = request.app.state.settings
        role_value = body.role if isinstance(body.role, str) else body.role.value
        ttl = settings.ttl_for_role(role_value)

        plain_secret: str | None = None
        secret_hash: str | None = None
        password_hash: str | None = None
        if body.auth_method == "oauth2":
            plain_secret = f"pmt_live_{secrets.token_hex(24)}"
            secret_hash = _hash_secret(plain_secret)
        else:
            password_hash = _hash_secret(body.password)  # type: ignore[arg-type]

        principal = Principal(
            client_id=str(uuid.uuid4()),
            client_name=body.client_name,
            auth_method=body.auth_method,
            client_secret_hash=secret_hash,
            email=body.email,
            password_hash=password_hash,
            role=PrincipalRole(role_value),
            allowed_scopes=" ".join(sorted(body.allowed_scopes)),
            token_ttl_seconds=ttl,
            label=body.label,
        )
        db.add(principal)
        await db.commit()
        await db.refresh(principal)

        logger.info(
            "auth.client_created",
            client_id=principal.client_id,
            role=role_value,
            auth_method=body.auth_method,
        )

        span.set_attribute("client_id", principal.client_id)
        span.set_attribute("scopes", " ".join(sorted(body.allowed_scopes)))
        span.set_attribute("http.status_code", 201)
        # AC-11: secret/password returned once only — hash stored, plaintext discarded
        return CreatePrincipalResponse(
            client_id=principal.client_id,
            client_name=principal.client_name,
            role=role_value,
            allowed_scopes=principal.scopes,
            token_ttl_seconds=ttl,
            auth_method=body.auth_method,
            email=body.email,
            client_secret=plain_secret if body.auth_method == "oauth2" else body.password,
        )


# ── List clients ──────────────────────────────────────────────────────────────


@router.get(
    "/clients", response_model=list[PrincipalListItem], dependencies=[Depends(_require_admin)]
)
async def list_clients(db: AsyncSession = Depends(_get_db)) -> Any:
    """List all principals. Implements: memory/specs/005-auth-service.md — AC-12.
    Implements: memory/specs/022-opentelemetry-sdk-instrumentation.md — G-13, AC-17
    """
    from opentelemetry.trace import SpanKind

    with _tracer.start_as_current_span("client.list", kind=SpanKind.INTERNAL) as span:
        result = await db.execute(select(Principal).order_by(Principal.created_at.desc()))
        principals = result.scalars().all()
        span.set_attribute("http.status_code", 200)
        span.set_attribute("client_count", len(principals))
        return [
            PrincipalListItem(
                client_id=p.client_id,
                client_name=p.client_name,
                label=p.label,
                role=p.role.value if hasattr(p.role, "value") else p.role,
                allowed_scopes=p.scopes,
                token_ttl_seconds=p.token_ttl_seconds,
                is_active=p.is_active,
                created_at=p.created_at,
                updated_at=p.updated_at,
                auth_method=p.auth_method,
                email=p.email,
            )
            for p in principals
        ]


# ── Deactivate (revoke) client ────────────────────────────────────────────────


@router.delete("/clients/{client_id}", status_code=204, dependencies=[Depends(_require_admin)])
async def deactivate_client(
    client_id: str,
    request: Request,
    db: AsyncSession = Depends(_get_db),
    permanent: bool = False,
) -> None:
    """Deactivate or hard-delete a client.

    Implements: memory/specs/005-auth-service.md — AC-7, AC-15
    Implements: memory/specs/015-auth-service-dashboard.md — AC-10, AC-11
    Implements: memory/specs/022-opentelemetry-sdk-instrumentation.md — G-14, AC-18
    `?permanent=true` hard-deletes the row; default is soft deactivate.
    """
    from opentelemetry.trace import SpanKind

    with _tracer.start_as_current_span("client.deactivate", kind=SpanKind.INTERNAL) as span:
        span.set_attribute("target_client_id", client_id)
        result = await db.execute(select(Principal).where(Principal.client_id == client_id))
        client = result.scalar_one_or_none()
        if client is None:
            span.set_attribute("http.status_code", 404)
            raise HTTPException(status_code=404, detail="Client not found.")

        settings = request.app.state.settings

        if permanent:
            # AC-10: hard delete — remove row and write Redis revocation key
            ttl = client.token_ttl_seconds
            await db.delete(client)
            await db.commit()
            if settings.auth_revocation_redis_url:
                try:
                    redis_client = aioredis.from_url(settings.auth_revocation_redis_url)
                    await redis_client.set(f"revoked:client:{client_id}", "1", ex=ttl)
                    await redis_client.aclose()
                except Exception as exc:
                    logger.error(
                        "admin.delete.redis_error",
                        client_id=client_id,
                        error=str(exc),
                    )
            logger.info("admin.client.hard_deleted", client_id=client_id)
            span.set_attribute("http.status_code", 204)
            return

        # AC-11: soft deactivate (original behaviour unchanged)
        client.is_active = False
        client.revoked_at = datetime.now(timezone.utc)
        client.updated_at = datetime.now(timezone.utc)
        await db.commit()

        # AC-15: write revocation key to Redis so existing tokens are rejected immediately
        if settings.auth_revocation_redis_url:
            try:
                redis_client = aioredis.from_url(settings.auth_revocation_redis_url)
                await redis_client.set(
                    f"revoked:client:{client_id}",
                    "1",
                    ex=client.token_ttl_seconds,
                )
                await redis_client.aclose()
            except Exception as exc:
                logger.error("admin.revoke.redis_error", client_id=client_id, error=str(exc))

        logger.info("auth.client_rotated", client_id=client_id, action="deactivated")
        span.set_attribute("http.status_code", 204)


# ── Rotate client secret ──────────────────────────────────────────────────────


@router.post(
    "/clients/{client_id}/rotate-secret",
    response_model=RotateSecretResponse,
    dependencies=[Depends(_require_admin)],
)
async def rotate_secret(
    client_id: str,
    db: AsyncSession = Depends(_get_db),
) -> Any:
    """Rotate client secret. Implements: memory/specs/005-auth-service.md — AC-13.
    Implements: memory/specs/022-opentelemetry-sdk-instrumentation.md — G-16, AC-20
    """
    from opentelemetry.trace import SpanKind

    with _tracer.start_as_current_span("client.rotate_secret", kind=SpanKind.INTERNAL) as span:
        span.set_attribute("target_client_id", client_id)
        result = await db.execute(select(Principal).where(Principal.client_id == client_id))
        client = result.scalar_one_or_none()
        if client is None:
            span.set_attribute("http.status_code", 404)
            raise HTTPException(status_code=404, detail="Client not found.")
        if client.auth_method != "oauth2":
            span.set_attribute("http.status_code", 409)
            raise HTTPException(
                status_code=409, detail="Cannot rotate an OAuth2 secret for a password principal."
            )
        if not client.is_active:
            span.set_attribute("http.status_code", 409)
            raise HTTPException(
                status_code=409, detail="Cannot rotate secret for a deactivated client."
            )

        plain_secret = f"pmt_live_{secrets.token_hex(24)}"
        client.client_secret_hash = _hash_secret(plain_secret)
        client.updated_at = datetime.now(timezone.utc)
        await db.commit()

        logger.info("auth.client_rotated", client_id=client_id, action="secret_rotated")
        # The new secret value is NEVER stored in any span attribute (AC-20)
        span.set_attribute("http.status_code", 200)
        return RotateSecretResponse(client_id=client_id, client_secret=plain_secret)


# ── Reset password (password principals only) ────────────────────────────────


@router.post(
    "/clients/{client_id}/reset-password",
    response_model=ResetPasswordResponse,
    dependencies=[Depends(_require_admin)],
)
async def reset_password(
    client_id: str,
    db: AsyncSession = Depends(_get_db),
) -> Any:
    """Generate a new password for a password-auth principal.

    Implements: docs/roadmap.md — RM-11 (mirrors rotate-secret for oauth2 principals).
    """
    from opentelemetry.trace import SpanKind

    with _tracer.start_as_current_span("client.reset_password", kind=SpanKind.INTERNAL) as span:
        span.set_attribute("target_client_id", client_id)
        result = await db.execute(select(Principal).where(Principal.client_id == client_id))
        principal = result.scalar_one_or_none()
        if principal is None:
            span.set_attribute("http.status_code", 404)
            raise HTTPException(status_code=404, detail="Principal not found.")
        if principal.auth_method != "password":
            span.set_attribute("http.status_code", 409)
            raise HTTPException(
                status_code=409, detail="Cannot reset a password for an OAuth2 client."
            )
        if not principal.is_active:
            span.set_attribute("http.status_code", 409)
            raise HTTPException(
                status_code=409, detail="Cannot reset password for a deactivated principal."
            )

        new_password = secrets.token_urlsafe(16)
        principal.password_hash = _hash_secret(new_password)
        principal.updated_at = datetime.now(timezone.utc)
        await db.commit()

        logger.info("auth.client_rotated", client_id=client_id, action="password_reset")
        # The new password value is NEVER stored in any span attribute (AC-20 parity)
        span.set_attribute("http.status_code", 200)
        return ResetPasswordResponse(client_id=client_id, password=new_password)


# ── Update (PATCH) client ──────────────────────────────────────────────────


@router.patch(
    "/clients/{client_id}", response_model=PrincipalListItem, dependencies=[Depends(_require_admin)]
)
async def update_client(
    client_id: str,
    body: UpdatePrincipalRequest,
    db: AsyncSession = Depends(_get_db),
) -> Any:
    """Partially update a client's name, label, scopes, or TTL.

    Implements: memory/specs/015-auth-service-dashboard.md — AC-3, AC-4, AC-5, AC-6
    Implements: memory/specs/022-opentelemetry-sdk-instrumentation.md — G-15, AC-19
    Only fields present in the request body (via model_fields_set) are updated.
    """
    from opentelemetry.trace import SpanKind

    with _tracer.start_as_current_span("client.update", kind=SpanKind.INTERNAL) as span:
        span.set_attribute("target_client_id", client_id)
        result = await db.execute(select(Principal).where(Principal.client_id == client_id))
        client = result.scalar_one_or_none()
        if client is None:
            span.set_attribute("http.status_code", 404)
            raise HTTPException(status_code=404, detail="Client not found.")

        updated_fields: list[str] = []

        if body.allowed_scopes is not None:
            invalid = invalid_scopes(body.allowed_scopes)
            if invalid:
                span.set_attribute("http.status_code", 422)
                raise HTTPException(
                    status_code=422, detail=f"Unknown scope(s): {', '.join(sorted(invalid))}"
                )
            client.allowed_scopes = " ".join(sorted(body.allowed_scopes))
            updated_fields.append("allowed_scopes")

        if body.client_name is not None:
            client.client_name = body.client_name
            updated_fields.append("client_name")

        # label: use model_fields_set to allow explicit null (clear label)
        if "label" in body.model_fields_set:
            client.label = body.label
            updated_fields.append("label")

        if body.token_ttl_seconds is not None:
            client.token_ttl_seconds = body.token_ttl_seconds
            updated_fields.append("token_ttl_seconds")

        client.updated_at = datetime.now(timezone.utc)
        await db.commit()
        await db.refresh(client)

        logger.info("admin.client.updated", client_id=client_id)
        # Record field names only — new values are never stored in span attributes (AC-19)
        span.set_attribute("updated_fields", ",".join(updated_fields))
        span.set_attribute("http.status_code", 200)
        return PrincipalListItem(
            client_id=client.client_id,
            client_name=client.client_name,
            label=client.label,
            role=client.role.value if hasattr(client.role, "value") else client.role,
            allowed_scopes=client.scopes,
            token_ttl_seconds=client.token_ttl_seconds,
            is_active=client.is_active,
            created_at=client.created_at,
            updated_at=client.updated_at,
            auth_method=client.auth_method,
            email=client.email,
        )


# ── Reactivate client ────────────────────────────────────────────────────────


@router.post(
    "/clients/{client_id}/reactivate",
    response_model=ReactivateResponse,
    dependencies=[Depends(_require_admin)],
)
async def reactivate_client(
    client_id: str,
    request: Request,
    db: AsyncSession = Depends(_get_db),
) -> Any:
    """Reverse a deactivation.  Implements: memory/specs/015-auth-service-dashboard.md — AC-7, AC-8, AC-9.
    Implements: memory/specs/022-opentelemetry-sdk-instrumentation.md — G-17, AC-21
    """
    from opentelemetry.trace import SpanKind

    with _tracer.start_as_current_span("client.reactivate", kind=SpanKind.INTERNAL) as span:
        span.set_attribute("target_client_id", client_id)
        result = await db.execute(select(Principal).where(Principal.client_id == client_id))
        client = result.scalar_one_or_none()
        if client is None:
            span.set_attribute("http.status_code", 404)
            raise HTTPException(status_code=404, detail="Client not found.")
        if client.is_active:
            span.set_attribute("http.status_code", 409)
            raise HTTPException(status_code=409, detail="Client is already active.")

        client.is_active = True
        client.revoked_at = None
        client.updated_at = datetime.now(timezone.utc)
        await db.commit()

        # AC-9: remove Redis revocation key so tokens are accepted again
        settings = request.app.state.settings
        if settings.auth_revocation_redis_url:
            try:
                redis_client = aioredis.from_url(settings.auth_revocation_redis_url)
                await redis_client.delete(f"revoked:client:{client_id}")
                await redis_client.aclose()
            except Exception as exc:
                logger.error("admin.reactivate.redis_error", client_id=client_id, error=str(exc))

        logger.info("admin.client.reactivated", client_id=client_id)
        span.set_attribute("http.status_code", 200)
        return ReactivateResponse(client_id=client_id, is_active=True)


# ── Credential share links (RM-11 — ported from the retired admin_ui.py) ─────
#
# The JSON/SPA flow already holds the plaintext secret in memory from the
# create/rotate-secret/reset-password response, so — unlike the old
# server-rendered dashboard — there's no need for a signed flash-cookie/
# share_intent round trip: the caller just sends the secret it already has.


@router.post(
    "/clients/{client_id}/share",
    response_model=ShareLinkResponse,
    dependencies=[Depends(_require_admin)],
)
async def generate_share_link(
    client_id: str,
    body: GenerateShareLinkRequest,
    request: Request,
    db: AsyncSession = Depends(_get_db),
) -> Any:
    """Create a single-use credential share URL.

    Implements: memory/specs/016-credential-share-link.md — AC-1..AC-7, AC-26, AC-28
    Implements: docs/roadmap.md — RM-11
    """
    from opentelemetry.trace import SpanKind

    with _tracer.start_as_current_span("client.share.create", kind=SpanKind.INTERNAL) as span:
        span.set_attribute("client_id", client_id)
        result = await db.execute(select(Principal).where(Principal.client_id == client_id))
        principal = result.scalar_one_or_none()
        if principal is None:
            span.set_attribute("http.status_code", 404)
            raise HTTPException(status_code=404, detail="Principal not found.")

        settings = request.app.state.settings
        now = datetime.now(timezone.utc)

        # AC-28: revoke any existing active token for this principal first
        active_result = await db.execute(
            select(CredentialShareToken).where(
                CredentialShareToken.client_id == client_id,
                CredentialShareToken.used_at.is_(None),
                CredentialShareToken.revoked_at.is_(None),
            )
        )
        for old_token in active_result.scalars().all():
            old_expires = old_token.expires_at
            if old_expires.tzinfo is None:
                old_expires = old_expires.replace(tzinfo=timezone.utc)
            if old_expires > now:
                old_token.revoked_at = now
                old_token.revoked_by = "admin:superseded"
                old_token.secret_plaintext_enc = None

        raw_token = secrets.token_urlsafe(32)
        ttl_s = settings.share_token_ttl_seconds
        enc = encrypt_secret(settings.share_token_encryption_key, body.secret)
        expires_at = now + timedelta(seconds=ttl_s)

        share = CredentialShareToken(
            id=str(uuid.uuid4()),
            token=raw_token,
            client_id=client_id,
            client_name=principal.client_name,
            client_id_value=client_id,
            secret_plaintext_enc=enc,
            expires_at=expires_at,
        )
        db.add(share)
        await db.commit()

        logger.info(
            "auth.share_token_created",
            token_id=share.id,
            token_prefix=raw_token[:8] + "…",
            client_id=client_id,
            expires_at=expires_at.isoformat(),
        )

        base_url = str(request.base_url).rstrip("/")
        span.set_attribute("http.status_code", 200)
        return ShareLinkResponse(
            share_url=f"{base_url}/share/{raw_token}",
            expires_at=expires_at,
        )


@router.post(
    "/clients/share/{token_id}/revoke",
    response_model=RevokeShareLinkResponse,
    dependencies=[Depends(_require_admin)],
)
async def revoke_share_link(
    token_id: str,
    db: AsyncSession = Depends(_get_db),
) -> Any:
    """Revoke an active share token before it is consumed.

    Implements: memory/specs/016-credential-share-link.md — AC-19, AC-20, AC-24
    Implements: docs/roadmap.md — RM-11
    """
    from opentelemetry.trace import SpanKind

    with _tracer.start_as_current_span("client.share.revoke", kind=SpanKind.INTERNAL) as span:
        span.set_attribute("token_id", token_id)
        result = await db.execute(
            select(CredentialShareToken).where(CredentialShareToken.id == token_id)
        )
        share = result.scalar_one_or_none()
        if share is None:
            span.set_attribute("http.status_code", 404)
            raise HTTPException(status_code=404, detail="Share token not found.")
        if share.used_at is not None:
            span.set_attribute("http.status_code", 409)
            raise HTTPException(status_code=409, detail="Share token already used.")

        share.revoked_at = datetime.now(timezone.utc)
        share.revoked_by = "admin"
        share.secret_plaintext_enc = None
        await db.commit()

        logger.info(
            "auth.share_token_revoked",
            token_id=share.id,
            token_prefix=share.token[:8] + "…",
            client_id=share.client_id,
        )
        span.set_attribute("http.status_code", 200)
        return RevokeShareLinkResponse(token_id=token_id, revoked=True)


# ── Node registry (RM-20 — replaces the gateway's static MANAGER_NODES) ───────


async def _check_node_reachable(manager_url: str) -> bool:
    """GET {manager_url}/health — manager-api's liveness probe, no auth required."""
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(f"{manager_url.rstrip('/')}/health")
        return resp.status_code == 200
    except Exception:
        return False


def _node_to_item(node: Node) -> NodeListItem:
    return NodeListItem(
        id=node.id,
        name=node.name,
        manager_url=node.manager_url,
        node_type=node.node_type.value,
        tag=node.tag,
        is_active=node.is_active,
        created_at=node.created_at,
        updated_at=node.updated_at,
    )


@router.post(
    "/nodes", response_model=NodeListItem, status_code=201, dependencies=[Depends(_require_admin)]
)
async def create_node(
    body: CreateNodeRequest,
    db: AsyncSession = Depends(_get_db),
) -> Any:
    """Register a new manager node.

    Implements: docs/roadmap.md — RM-20. A connectivity check against the
    node's manager-api runs immediately — an unreachable node is still
    registered (so the operator doesn't lose the entry they just typed), but
    created inactive rather than rejected outright.
    """
    existing = await db.execute(select(Node).where(Node.name == body.name))
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail=f"Node {body.name!r} already exists.")

    reachable = await _check_node_reachable(body.manager_url)

    node = Node(
        id=str(uuid.uuid4()),
        name=body.name,
        manager_url=body.manager_url,
        node_type=NodeType(body.node_type),
        tag=body.tag,
        is_active=reachable,
    )
    db.add(node)
    await db.commit()
    await db.refresh(node)

    logger.info("auth.node_created", node_id=node.id, name=node.name, is_active=reachable)
    return _node_to_item(node)


@router.get("/nodes", response_model=list[NodeListItem], dependencies=[Depends(_require_admin)])
async def list_nodes(db: AsyncSession = Depends(_get_db)) -> Any:
    """List all registered nodes. Implements: docs/roadmap.md — RM-20."""
    result = await db.execute(select(Node).order_by(Node.created_at.desc()))
    return [_node_to_item(n) for n in result.scalars().all()]


@router.patch(
    "/nodes/{node_id}", response_model=NodeListItem, dependencies=[Depends(_require_admin)]
)
async def update_node(
    node_id: str,
    body: UpdateNodeRequest,
    db: AsyncSession = Depends(_get_db),
) -> Any:
    """Partially update a node's manager_url / node_type / tag.

    Implements: docs/roadmap.md — RM-20. Changing manager_url re-runs the
    connectivity check (a URL change invalidates whatever was last observed).
    """
    result = await db.execute(select(Node).where(Node.id == node_id))
    node = result.scalar_one_or_none()
    if node is None:
        raise HTTPException(status_code=404, detail="Node not found.")

    if body.manager_url is not None and body.manager_url != node.manager_url:
        node.manager_url = body.manager_url
        node.is_active = await _check_node_reachable(body.manager_url)
    if body.node_type is not None:
        node.node_type = NodeType(body.node_type)
    if "tag" in body.model_fields_set:
        node.tag = body.tag

    node.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(node)

    logger.info("auth.node_updated", node_id=node.id)
    return _node_to_item(node)


@router.post(
    "/nodes/{node_id}/check", response_model=NodeListItem, dependencies=[Depends(_require_admin)]
)
async def check_node(
    node_id: str,
    db: AsyncSession = Depends(_get_db),
) -> Any:
    """Re-run the connectivity check and update is_active accordingly.

    Implements: docs/roadmap.md — RM-20. The way a node marked inactive
    (unreachable at creation, or since) comes back once it's actually up.
    """
    result = await db.execute(select(Node).where(Node.id == node_id))
    node = result.scalar_one_or_none()
    if node is None:
        raise HTTPException(status_code=404, detail="Node not found.")

    node.is_active = await _check_node_reachable(node.manager_url)
    node.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(node)

    logger.info("auth.node_checked", node_id=node.id, is_active=node.is_active)
    return _node_to_item(node)


@router.post(
    "/nodes/{node_id}/activate", response_model=NodeListItem, dependencies=[Depends(_require_admin)]
)
async def activate_node(
    node_id: str,
    db: AsyncSession = Depends(_get_db),
) -> Any:
    """Try to bring a node back into rotation — gated on an actual connectivity check.

    Implements: docs/roadmap.md — RM-20. Unlike /deactivate, this can't just flip
    the flag: showing "Active" for a node that still can't be reached would be a
    lie the operator would trust. So this re-probes the node and only marks it
    active if the probe succeeds; otherwise it stays inactive. Functionally the
    same probe as /check — kept as a separate route because "I want this node
    back in service" and "just tell me the current status" are different intents
    worth distinct responses/messaging on the frontend.
    """
    result = await db.execute(select(Node).where(Node.id == node_id))
    node = result.scalar_one_or_none()
    if node is None:
        raise HTTPException(status_code=404, detail="Node not found.")

    node.is_active = await _check_node_reachable(node.manager_url)
    node.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(node)

    logger.info("auth.node_activate_attempted", node_id=node.id, is_active=node.is_active)
    return _node_to_item(node)


@router.post(
    "/nodes/{node_id}/deactivate",
    response_model=NodeListItem,
    dependencies=[Depends(_require_admin)],
)
async def deactivate_node(
    node_id: str,
    db: AsyncSession = Depends(_get_db),
) -> Any:
    """Manually mark a node inactive — an on-demand override, e.g. for maintenance.

    Implements: docs/roadmap.md — RM-20.
    """
    result = await db.execute(select(Node).where(Node.id == node_id))
    node = result.scalar_one_or_none()
    if node is None:
        raise HTTPException(status_code=404, detail="Node not found.")

    node.is_active = False
    node.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(node)

    logger.info("auth.node_deactivated", node_id=node.id)
    return _node_to_item(node)


@router.delete("/nodes/{node_id}", status_code=204, dependencies=[Depends(_require_admin)])
async def delete_node(
    node_id: str,
    db: AsyncSession = Depends(_get_db),
) -> None:
    """Remove a node. Implements: docs/roadmap.md — RM-20."""
    result = await db.execute(select(Node).where(Node.id == node_id))
    node = result.scalar_one_or_none()
    if node is None:
        raise HTTPException(status_code=404, detail="Node not found.")

    await db.delete(node)
    await db.commit()
    logger.info("auth.node_deleted", node_id=node_id)
