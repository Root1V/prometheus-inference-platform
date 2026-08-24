# See memory/specs/005-auth-service.md — Token Structure + JWKS endpoint
import uuid
from base64 import urlsafe_b64encode
from datetime import datetime, timezone
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey, RSAPublicKey
from jose import jwt


def load_private_key(path: str) -> RSAPrivateKey:
    with open(path, "rb") as fh:
        return serialization.load_pem_private_key(fh.read(), password=None)  # type: ignore[return-value]


def load_public_key(path: str) -> RSAPublicKey:
    with open(path, "rb") as fh:
        return serialization.load_pem_public_key(fh.read())  # type: ignore[return-value]


def public_key_to_pem(pub: RSAPublicKey) -> str:
    return pub.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()


def build_jwks(kid: str, public_key: RSAPublicKey) -> dict[str, Any]:
    """Build a JWKS document containing the given RSA public key.

    Implements: memory/specs/005-auth-service.md — AC-17
    """
    pub_numbers = (
        public_key.public_key().public_numbers()
        if hasattr(public_key, "public_key")
        else public_key.public_numbers()
    )

    def _b64(n: int) -> str:
        length = (n.bit_length() + 7) // 8
        return urlsafe_b64encode(n.to_bytes(length, "big")).rstrip(b"=").decode()

    return {
        "keys": [
            {
                "kty": "RSA",
                "use": "sig",
                "alg": "RS256",
                "kid": kid,
                "n": _b64(pub_numbers.n),
                "e": _b64(pub_numbers.e),
            }
        ]
    }


def issue_token(
    *,
    private_key: RSAPrivateKey,
    kid: str,
    issuer: str,
    audience: str,
    client_id: str,
    client_name: str,
    role: str,
    scope: str,
    ttl_seconds: int,
) -> tuple[str, int]:
    """Issue a signed RS256 JWT.

    Returns (token_string, expires_in_seconds).
    Implements: memory/specs/005-auth-service.md — AC-1, AC-6 through AC-9
    """
    now = int(datetime.now(timezone.utc).timestamp())
    payload = {
        "iss": issuer,
        "sub": client_id,
        "azp": client_id,  # authorized party — required by gateway rate limiter (RFC 7519 §4.1)
        "aud": audience,
        "iat": now,
        "exp": now + ttl_seconds,
        "jti": str(uuid.uuid4()),
        "scope": scope,
        "role": role,
        "client_name": client_name,
    }
    # Serialize private key to PEM for python-jose
    pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    )
    token = jwt.encode(payload, pem, algorithm="RS256", headers={"kid": kid})
    return token, ttl_seconds
