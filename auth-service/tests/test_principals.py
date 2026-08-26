"""Tests for RM-11 — unified principals (oauth2 + password auth methods).

Implements: docs/roadmap.md — RM-11
"""

import uuid

import pytest

from .conftest import ADMIN_HEADERS, register_client


async def _register_password_user(
    client, name="test-user", email=None, password="s3cret-password", scopes=None
):
    payload = {
        "client_name": name,
        "role": "app",
        "allowed_scopes": scopes or ["inference:read"],
        "auth_method": "password",
        "email": email or f"{uuid.uuid4().hex}@example.com",
        "password": password,
    }
    resp = await client.post("/admin/clients", json=payload, headers=ADMIN_HEADERS)
    assert resp.status_code == 200, resp.text
    return resp.json()


# ── Create: auth_method validation ────────────────────────────────────────────


async def test_principals_create_password_user(client):
    """Creating a password principal returns the email, no client_secret_hash."""
    created = await _register_password_user(client, name="pw-create")
    assert created["auth_method"] == "password"
    assert created["email"] is not None
    assert created["client_secret"] == "s3cret-password"


async def test_principals_create_oauth2_rejects_email_password(client):
    """oauth2 (default) auth_method must not carry email/password."""
    resp = await client.post(
        "/admin/clients",
        json={
            "client_name": "bad-oauth2",
            "role": "app",
            "allowed_scopes": ["inference:read"],
            "auth_method": "oauth2",
            "email": "x@example.com",
            "password": "whatever1",
        },
        headers=ADMIN_HEADERS,
    )
    assert resp.status_code == 422


async def test_principals_create_password_requires_email_and_password(client):
    """password auth_method requires both email and password."""
    resp = await client.post(
        "/admin/clients",
        json={
            "client_name": "bad-password",
            "role": "app",
            "allowed_scopes": ["inference:read"],
            "auth_method": "password",
        },
        headers=ADMIN_HEADERS,
    )
    assert resp.status_code == 422


# ── Password grant ─────────────────────────────────────────────────────────────


async def test_principals_password_grant_success(client):
    """POST /oauth2/token with grant_type=password issues a JWT."""
    created = await _register_password_user(client, name="pw-grant")
    resp = await client.post(
        "/oauth2/token",
        data={
            "grant_type": "password",
            "username": created["email"],
            "password": "s3cret-password",
            "scope": "inference:read",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["token_type"] == "bearer"
    assert body["scope"] == "inference:read"


async def test_principals_password_grant_unknown_email(client):
    """Unknown email on the password grant → invalid_client."""
    resp = await client.post(
        "/oauth2/token",
        data={"grant_type": "password", "username": "nobody@example.com", "password": "x"},
    )
    assert resp.status_code == 401
    assert resp.json()["error"] == "invalid_client"


async def test_principals_password_grant_wrong_password(client):
    """Wrong password → invalid_client."""
    created = await _register_password_user(client, name="pw-wrong")
    resp = await client.post(
        "/oauth2/token",
        data={
            "grant_type": "password",
            "username": created["email"],
            "password": "not-the-password",
        },
    )
    assert resp.status_code == 401
    assert resp.json()["error"] == "invalid_client"


async def test_principals_password_grant_rejects_oauth2_principal(client):
    """An oauth2 principal's email (none set) can't authenticate via the password grant."""
    oauth2_client = await register_client(client, name="oauth2-only")
    resp = await client.post(
        "/oauth2/token",
        data={
            "grant_type": "password",
            "username": oauth2_client["client_id"],  # not an email, won't match
            "password": "irrelevant",
        },
    )
    assert resp.status_code == 401
    assert resp.json()["error"] == "invalid_client"


async def test_principals_client_credentials_rejects_password_principal(client):
    """A password principal has no client_secret_hash — client_credentials grant fails."""
    created = await _register_password_user(client, name="pw-via-cc")
    resp = await client.post(
        "/oauth2/token",
        data={
            "grant_type": "client_credentials",
            "client_id": created["client_id"],
            "client_secret": "anything",
        },
    )
    assert resp.status_code == 401
    assert resp.json()["error"] == "invalid_client"


async def test_principals_password_grant_deactivated(client):
    """A deactivated password principal cannot obtain a token."""
    created = await _register_password_user(client, name="pw-deactivated")
    await client.delete(f"/admin/clients/{created['client_id']}", headers=ADMIN_HEADERS)
    resp = await client.post(
        "/oauth2/token",
        data={
            "grant_type": "password",
            "username": created["email"],
            "password": "s3cret-password",
        },
    )
    assert resp.status_code == 401
    assert resp.json()["error"] == "unauthorized_client"


async def test_principals_unsupported_grant_type(client):
    resp = await client.post("/oauth2/token", data={"grant_type": "refresh_token"})
    assert resp.status_code == 400
    assert resp.json()["error"] == "unsupported_grant_type"


# ── Reset password ─────────────────────────────────────────────────────────────


async def test_principals_reset_password(client):
    """Resetting a password principal's password invalidates the old one."""
    created = await _register_password_user(client, name="pw-reset")
    resp = await client.post(
        f"/admin/clients/{created['client_id']}/reset-password", headers=ADMIN_HEADERS
    )
    assert resp.status_code == 200
    new_password = resp.json()["password"]
    assert new_password != "s3cret-password"

    old = await client.post(
        "/oauth2/token",
        data={
            "grant_type": "password",
            "username": created["email"],
            "password": "s3cret-password",
        },
    )
    assert old.status_code == 401

    new = await client.post(
        "/oauth2/token",
        data={"grant_type": "password", "username": created["email"], "password": new_password},
    )
    assert new.status_code == 200


async def test_principals_reset_password_rejects_oauth2_client(client):
    oauth2_client = await register_client(client, name="oauth2-reset")
    resp = await client.post(
        f"/admin/clients/{oauth2_client['client_id']}/reset-password", headers=ADMIN_HEADERS
    )
    assert resp.status_code == 409


async def test_principals_rotate_secret_rejects_password_principal(client):
    created = await _register_password_user(client, name="pw-rotate")
    resp = await client.post(
        f"/admin/clients/{created['client_id']}/rotate-secret", headers=ADMIN_HEADERS
    )
    assert resp.status_code == 409


# ── Update / reactivate (PATCH, POST reactivate) ──────────────────────────────


async def test_principals_update_client(client):
    created = await register_client(client, name="update-me")
    resp = await client.patch(
        f"/admin/clients/{created['client_id']}",
        json={"client_name": "renamed", "label": "owner-team"},
        headers=ADMIN_HEADERS,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["client_name"] == "renamed"
    assert body["label"] == "owner-team"
    assert body["auth_method"] == "oauth2"


async def test_principals_reactivate_client(client):
    created = await register_client(client, name="reactivate-me")
    await client.delete(f"/admin/clients/{created['client_id']}", headers=ADMIN_HEADERS)
    resp = await client.post(
        f"/admin/clients/{created['client_id']}/reactivate", headers=ADMIN_HEADERS
    )
    assert resp.status_code == 200
    assert resp.json()["is_active"] is True


# ── Migration: oauth_clients → principals ────────────────────────────────────


@pytest.mark.asyncio
async def test_principals_migrates_old_oauth_clients_table(rsa_key_pem_files):
    """create_tables() copies pre-existing oauth_clients rows into principals."""
    from sqlalchemy import text

    from prometheus_auth.config import Settings
    from prometheus_auth.crypto import load_private_key, load_public_key
    from prometheus_auth.db import create_tables, init_db_engine

    priv, pub = rsa_key_pem_files
    settings = Settings(
        auth_private_key_file=priv,
        auth_public_key_file=pub,
        auth_active_kid="test-key",
        auth_jwt_issuer="https://prometheus.test/auth",
        auth_admin_api_key="test-admin-secret",
        auth_db_url="sqlite+aiosqlite:///:memory:",
        share_token_encryption_key="a" * 64,
    )
    load_private_key(settings.auth_private_key_file)
    load_public_key(settings.auth_public_key_file)
    engine = init_db_engine(settings.auth_db_url)

    # Seed the OLD schema by hand, before create_tables() has ever run.
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "CREATE TABLE oauth_clients ("
                " client_id VARCHAR(36) PRIMARY KEY,"
                " client_name VARCHAR(255) NOT NULL,"
                " client_secret_hash VARCHAR(60) NOT NULL,"
                " role VARCHAR(9) NOT NULL,"
                " allowed_scopes TEXT NOT NULL,"
                " token_ttl_seconds INTEGER NOT NULL,"
                " created_at DATETIME NOT NULL,"
                " is_active BOOLEAN NOT NULL,"
                " revoked_at DATETIME,"
                " label TEXT,"
                " updated_at DATETIME"
                ")"
            )
        )
        await conn.execute(
            text(
                "INSERT INTO oauth_clients (client_id, client_name, client_secret_hash, role, "
                "allowed_scopes, token_ttl_seconds, created_at, is_active) VALUES "
                "('old-1', 'legacy-client', 'somehash', 'app', 'inference:read', 300, "
                "'2026-01-01 00:00:00', 1)"
            )
        )

    await create_tables(engine)

    async with engine.begin() as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT client_id, auth_method, client_secret_hash, email, password_hash "
                    "FROM principals WHERE client_id = 'old-1'"
                )
            )
        ).fetchall()
        old_table_exists = (
            await conn.execute(
                text("SELECT name FROM sqlite_master WHERE type='table' AND name='oauth_clients'")
            )
        ).fetchall()

    assert len(rows) == 1
    row = rows[0]
    assert row.auth_method == "oauth2"
    assert row.client_secret_hash == "somehash"
    assert row.email is None
    assert row.password_hash is None
    assert old_table_exists == []  # oauth_clients dropped after migration

    await engine.dispose()
