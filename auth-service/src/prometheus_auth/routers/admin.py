# See memory/specs/005-auth-service.md — /admin/clients endpoints
# Implements: AC-6 (create), AC-7 (revoke), AC-8 (auth), AC-11, AC-12, AC-13, AC-14, AC-15
# Implements: memory/specs/018-observability-telemetry.md — AC-2, AC-12, AC-13
import secrets
import uuid
from datetime import datetime, timezone
from typing import Any

import bcrypt
import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import APIKeyHeader
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import OAuthClient, ClientRole, get_session_factory
from ..schemas import (
    ClientListItem,
    CreateClientRequest,
    CreateClientResponse,
    ReactivateResponse,
    RotateSecretResponse,
    UpdateClientRequest,
    invalid_scopes,
)
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
    "/clients", response_model=CreateClientResponse, dependencies=[Depends(_require_admin)]
)
async def create_client(
    body: CreateClientRequest,
    request: Request,
    db: AsyncSession = Depends(_get_db),
) -> Any:
    """Register a new OAuth2 client.  Implements: memory/specs/005-auth-service.md — AC-11.
    Implements: memory/specs/022-opentelemetry-sdk-instrumentation.md — G-12, AC-16
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

        plain_secret = f"pmt_live_{secrets.token_hex(24)}"
        hashed = _hash_secret(plain_secret)

        client = OAuthClient(
            client_id=str(uuid.uuid4()),
            client_name=body.client_name,
            client_secret_hash=hashed,
            role=ClientRole(role_value),
            allowed_scopes=" ".join(sorted(body.allowed_scopes)),
            token_ttl_seconds=ttl,
            label=body.label,
        )
        db.add(client)
        await db.commit()
        await db.refresh(client)

        logger.info("auth.client_created", client_id=client.client_id, role=role_value)

        span.set_attribute("client_id", client.client_id)
        span.set_attribute("scopes", " ".join(sorted(body.allowed_scopes)))
        span.set_attribute("http.status_code", 201)
        # AC-11: client_secret returned once only — hash stored, plaintext discarded
        return CreateClientResponse(
            client_id=client.client_id,
            client_secret=plain_secret,
            client_name=client.client_name,
            role=role_value,
            allowed_scopes=client.scopes,
            token_ttl_seconds=ttl,
        )


# ── List clients ──────────────────────────────────────────────────────────────


@router.get("/clients", response_model=list[ClientListItem], dependencies=[Depends(_require_admin)])
async def list_clients(db: AsyncSession = Depends(_get_db)) -> Any:
    """List all clients. Implements: memory/specs/005-auth-service.md — AC-12.
    Implements: memory/specs/022-opentelemetry-sdk-instrumentation.md — G-13, AC-17
    """
    from opentelemetry.trace import SpanKind

    with _tracer.start_as_current_span("client.list", kind=SpanKind.INTERNAL) as span:
        result = await db.execute(select(OAuthClient).order_by(OAuthClient.created_at.desc()))
        clients = result.scalars().all()
        span.set_attribute("http.status_code", 200)
        span.set_attribute("client_count", len(clients))
        return [
            ClientListItem(
                client_id=c.client_id,
                client_name=c.client_name,
                label=c.label,
                role=c.role.value if hasattr(c.role, "value") else c.role,
                allowed_scopes=c.scopes,
                token_ttl_seconds=c.token_ttl_seconds,
                is_active=c.is_active,
                created_at=c.created_at,
                updated_at=c.updated_at,
            )
            for c in clients
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
        result = await db.execute(select(OAuthClient).where(OAuthClient.client_id == client_id))
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
        result = await db.execute(select(OAuthClient).where(OAuthClient.client_id == client_id))
        client = result.scalar_one_or_none()
        if client is None:
            span.set_attribute("http.status_code", 404)
            raise HTTPException(status_code=404, detail="Client not found.")
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


# ── Update (PATCH) client ──────────────────────────────────────────────────


@router.patch(
    "/clients/{client_id}", response_model=ClientListItem, dependencies=[Depends(_require_admin)]
)
async def update_client(
    client_id: str,
    body: UpdateClientRequest,
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
        result = await db.execute(select(OAuthClient).where(OAuthClient.client_id == client_id))
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
        return ClientListItem(
            client_id=client.client_id,
            client_name=client.client_name,
            label=client.label,
            role=client.role.value if hasattr(client.role, "value") else client.role,
            allowed_scopes=client.scopes,
            token_ttl_seconds=client.token_ttl_seconds,
            is_active=client.is_active,
            created_at=client.created_at,
            updated_at=client.updated_at,
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
        result = await db.execute(select(OAuthClient).where(OAuthClient.client_id == client_id))
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
