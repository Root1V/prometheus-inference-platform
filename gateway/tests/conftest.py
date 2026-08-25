"""Shared fixtures for Prometheus Gateway tests."""

import base64
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI, Request
from httpx import ASGITransport, AsyncClient
from jose import jwt

from prometheus_gateway.auth.jwks import _reset_cache_for_testing
from prometheus_gateway.auth.middleware import JWTAuthMiddleware
from prometheus_gateway.config import Settings
from prometheus_gateway.models.registry import ModelRegistry


# ── RSA key generation ─────────────────────────────────────────────────────


@pytest.fixture(scope="session")
def rsa_keys():
    """Generate a session-scoped RSA 2048 key pair (PEM strings)."""
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    public_pem = (
        private_key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode()
    )
    return {"private": private_pem, "public": public_pem}


@pytest.fixture(scope="session")
def alt_rsa_keys():
    """A second RSA key pair — used to sign tokens with the 'wrong' key (AC-3)."""
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    public_pem = (
        private_key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode()
    )
    return {"private": private_pem, "public": public_pem}


# ── Settings ───────────────────────────────────────────────────────────────


@pytest.fixture
def settings(rsa_keys, tmp_path):
    """Settings backed by a static RS256 public key file."""
    key_file = tmp_path / "public.pem"
    key_file.write_text(rsa_keys["public"])
    return Settings(
        jwt_issuer="https://auth.test",
        jwt_audience="prometheus-gateway",
        jwt_public_key_file=str(key_file),
        jwt_clock_skew_seconds=30,
        jwt_revocation_redis_url=None,  # disable Redis in unit tests
        rate_limit_strict=False,  # no Redis in unit tests — fail-open
    )


# ── Token factory ──────────────────────────────────────────────────────────


def make_token(
    private_key: str,
    *,
    sub: str = "user-123",
    azp: str = "client-abc",
    scope: str = "inference:read",
    iss: str = "https://auth.test",
    aud: str = "prometheus-gateway",
    exp_delta: int = 3600,
    jti: str | None = None,
    algorithm: str = "RS256",
    extra_claims: dict | None = None,
) -> str:
    now = datetime.now(tz=timezone.utc)
    payload: dict = {
        "sub": sub,
        "azp": azp,
        "scope": scope,
        "iss": iss,
        "aud": aud,
        "iat": now,
        "exp": now + timedelta(seconds=exp_delta),
        "jti": jti or str(uuid.uuid4()),
    }
    if extra_claims:
        payload.update(extra_claims)
    return jwt.encode(payload, private_key, algorithm=algorithm)


# ── Test app ───────────────────────────────────────────────────────────────


def build_test_app(settings: Settings, redis_client=None) -> FastAPI:
    """Build a minimal FastAPI app wired with JWTAuthMiddleware."""
    app = FastAPI()
    app.add_middleware(JWTAuthMiddleware, settings=settings, redis_client=redis_client)

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.get("/protected")
    async def protected(request: Request):
        claims = request.state.claims
        return {"user_id": claims.user_id, "client_id": claims.client_id, "scope": claims.scope}

    return app


@pytest.fixture
def test_app(settings):
    return build_test_app(settings)


# ── Multi-model registry fixture ───────────────────────────────────────────
# Implements: memory/specs/006-multi-model-gateway.md — test fixture pattern


@pytest.fixture
def multi_model_registry(tmp_path):
    """In-memory registry with two active models and one inactive.

    Implements: memory/specs/006-multi-model-gateway.md — AC-1, AC-2, AC-3, AC-4, AC-9
    """
    yaml_content = """models:
  - id: llama3-8b-q4
    path: /dev/null
    context_length: 8192
    family: llama3
    quantization: Q4_0
    backend_url: "http://127.0.0.1:18080"
  - id: small-model
    path: /dev/null
    context_length: 4096
    family: llama3
    quantization: Q4_0
    backend_url: "http://127.0.0.1:18081"
  - id: inactive-model
    path: /dev/null
    context_length: 4096
    family: llama3
    quantization: Q4_0
  - id: invalid-backend-model
    path: /dev/null
    context_length: 4096
    family: llama3
    quantization: Q4_0
    backend_url: "http://192.168.1.10:8080"
"""
    registry_file = tmp_path / "registry.yaml"
    registry_file.write_text(yaml_content)
    return ModelRegistry(registry_file)


@pytest.fixture
def multi_model_app(settings, multi_model_registry):
    """Full gateway app wired with multi-model registry."""
    from prometheus_gateway.main import create_app

    return create_app(settings=settings, registry=multi_model_registry)


# ── RM-09: modality registry fixture (vision + embedding models) ────────────


@pytest.fixture
def modality_registry(tmp_path):
    """Registry with a text, a vision, and an embedding model — all active.

    Implements: docs/roadmap.md — RM-09 (VLM + embeddings)
    """
    yaml_content = """models:
  - id: llama3-8b-q4
    path: /dev/null
    context_length: 8192
    family: llama3
    quantization: Q4_0
    backend_url: "http://127.0.0.1:18080"
    modality: text
  - id: vlm-model
    path: /dev/null
    context_length: 8192
    family: qwen2-vl
    quantization: Q4_0
    backend_url: "http://127.0.0.1:18082"
    modality: vision
  - id: embed-model
    path: /dev/null
    context_length: 512
    family: nomic-embed
    quantization: F16
    backend_url: "http://127.0.0.1:18083"
    modality: embedding
"""
    registry_file = tmp_path / "registry.yaml"
    registry_file.write_text(yaml_content)
    return ModelRegistry(registry_file)


@pytest.fixture
def modality_app(settings, modality_registry):
    """Full gateway app wired with the RM-09 modality registry."""
    from prometheus_gateway.main import create_app

    return create_app(settings=settings, registry=modality_registry)


@pytest.fixture
async def client(test_app):
    async with AsyncClient(transport=ASGITransport(app=test_app), base_url="http://test") as c:
        yield c


# ── JWKS helpers ───────────────────────────────────────────────────────────


def public_pem_to_jwk(public_pem: str, kid: str = "key-1") -> dict:
    """Convert an RSA PEM public key to a JWK dict (for JWKS endpoint mocking)."""
    key = serialization.load_pem_public_key(public_pem.encode())
    nums = key.public_numbers()  # type: ignore[union-attr]

    def to_b64url(n: int) -> str:
        length = (n.bit_length() + 7) // 8
        return base64.urlsafe_b64encode(n.to_bytes(length, "big")).rstrip(b"=").decode()

    return {
        "kty": "RSA",
        "use": "sig",
        "alg": "RS256",
        "kid": kid,
        "n": to_b64url(nums.n),
        "e": to_b64url(nums.e),
    }


# ── Auto-cleanup ───────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def clear_jwks_cache():
    """Ensure JWKS cache is clean before and after each test."""
    _reset_cache_for_testing()
    yield
    _reset_cache_for_testing()
