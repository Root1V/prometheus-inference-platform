"""Shared fixtures for Prometheus Auth Service tests.

Implements: memory/specs/005-auth-service.md — AC-1 through AC-21
"""

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from httpx import ASGITransport, AsyncClient

from prometheus_auth.config import Settings
from prometheus_auth.main import create_app


# ── RSA key pair (session-scoped — generated once) ────────────────────────────


@pytest.fixture(scope="session")
def rsa_private_key_obj():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(scope="session")
def rsa_key_pem_files(rsa_private_key_obj, tmp_path_factory):
    """Write RSA keys to temp PEM files; return (private_path, public_path)."""
    base = tmp_path_factory.mktemp("keys")
    priv_path = base / "private.pem"
    pub_path = base / "public.pem"

    priv_path.write_bytes(
        rsa_private_key_obj.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    pub_path.write_bytes(
        rsa_private_key_obj.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
    return str(priv_path), str(pub_path)


# ── Settings ──────────────────────────────────────────────────────────────────


@pytest.fixture
def settings(rsa_key_pem_files):
    priv, pub = rsa_key_pem_files
    return Settings(
        auth_private_key_file=priv,
        auth_public_key_file=pub,
        auth_active_kid="test-key",
        auth_jwt_issuer="https://prometheus.test/auth",
        auth_admin_api_key="test-admin-secret",
        auth_db_url="sqlite+aiosqlite:///:memory:",
        auth_revocation_redis_url=None,
        auth_rate_limit_rpm=1000,  # disable effective rate limiting in tests
        share_token_encryption_key="a" * 64,  # 32 bytes of 0xAA — valid test key (AC-15/16)
    )


# ── App + HTTP client ─────────────────────────────────────────────────────────


@pytest.fixture
async def client(settings):
    """Create a test client with lifespan startup manually applied.

    httpx.ASGITransport does not trigger FastAPI lifespan events, so we
    bootstrap app.state and the DB engine directly before yielding.
    """
    from slowapi import Limiter
    from slowapi.util import get_remote_address

    from prometheus_auth.crypto import build_jwks, load_private_key, load_public_key
    from prometheus_auth.db import create_tables, init_db_engine

    app = create_app(settings=settings)

    # Mirror the lifespan startup
    engine = init_db_engine(settings.auth_db_url)
    await create_tables(engine)
    private_key = load_private_key(settings.auth_private_key_file)
    public_key = load_public_key(settings.auth_public_key_file)
    jwks_doc = build_jwks(settings.auth_active_kid, public_key)

    app.state.settings = settings
    app.state.private_key = private_key
    app.state.public_key = public_key
    app.state.jwks_document = jwks_doc
    limiter = Limiter(
        key_func=get_remote_address,
        default_limits=[f"{settings.auth_rate_limit_rpm}/minute"],
    )
    app.state.limiter = limiter

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c

    await engine.dispose()


# ── Admin helper ──────────────────────────────────────────────────────────────

ADMIN_HEADERS = {"X-Admin-Key": "test-admin-secret"}


async def register_client(client, role="app", scopes=None, name="test-client"):
    """Helper to register a client via the admin API."""
    payload = {
        "client_name": name,
        "role": role,
        "allowed_scopes": scopes or ["inference:read"],
    }
    resp = await client.post("/admin/clients", json=payload, headers=ADMIN_HEADERS)
    assert resp.status_code == 200, resp.text
    return resp.json()
