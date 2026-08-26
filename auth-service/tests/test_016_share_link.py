"""Tests for spec-016 — Credential Share Link.

Maps to memory/specs/016-credential-share-link.md Acceptance Criteria. Generation and
revocation (previously only reachable through the retired admin_ui.py Jinja dashboard)
now go through the JSON /admin/clients/{id}/share and /admin/clients/share/{id}/revoke
endpoints (docs/roadmap.md — RM-11), auth'd via X-Admin-Key like the rest of admin.py —
no more CSRF tokens, cookie sessions, or flash-cookie secret hand-off. The public
redemption endpoint (GET /share/{token}, routers/share.py) is unchanged by RM-11, so its
tests are unchanged too.
"""

import base64
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient

from .conftest import ADMIN_HEADERS, register_client

_TEST_KEY = "a" * 64  # 32-byte key used in conftest


# ── AC-1, AC-2: share token created, default TTL = 3600 s ────────────────────


@pytest.mark.asyncio
async def test_016_AC1_AC2_share_token_created_default_ttl(client: AsyncClient) -> None:
    """AC-1: POST /admin/clients/{id}/share creates a token and returns the share URL.
    AC-2: expires_at - created_at == 3600 s by default.
    """
    created = await register_client(client, name="share-ac1-test")
    client_id = created["client_id"]
    secret = created["client_secret"]

    r = await client.post(
        f"/admin/clients/{client_id}/share",
        json={"secret": secret},
        headers=ADMIN_HEADERS,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert "/share/" in body["share_url"]

    from prometheus_auth.db import CredentialShareToken, get_session_factory
    from sqlalchemy import select

    async with get_session_factory()() as db:
        result = await db.execute(
            select(CredentialShareToken).where(CredentialShareToken.client_id == client_id)
        )
        share = result.scalar_one()

    exp, cre = share.expires_at, share.created_at
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    if cre.tzinfo is None:
        cre = cre.replace(tzinfo=timezone.utc)
    diff = (exp - cre).total_seconds()
    assert 3595 <= diff <= 3605, f"Expected TTL ~3600s, got {diff}s"


# ── AC-3: custom TTL respected ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_016_AC3_custom_ttl(rsa_key_pem_files: tuple[str, str]) -> None:
    """AC-3: SHARE_TOKEN_TTL_SECONDS=7200 produces expires_at - created_at == 7200 s."""
    from httpx import ASGITransport
    from slowapi import Limiter
    from slowapi.util import get_remote_address

    from prometheus_auth.config import Settings
    from prometheus_auth.crypto import build_jwks, load_private_key, load_public_key
    from prometheus_auth.db import CredentialShareToken, create_tables, init_db_engine
    from prometheus_auth.main import create_app
    from sqlalchemy import select

    priv, pub = rsa_key_pem_files
    settings = Settings(
        auth_private_key_file=priv,
        auth_public_key_file=pub,
        auth_active_kid="test-key",
        auth_jwt_issuer="https://prometheus.test/auth",
        auth_admin_api_key="test-admin-secret",
        auth_db_url="sqlite+aiosqlite:///:memory:",
        auth_revocation_redis_url=None,
        auth_rate_limit_rpm=1000,
        share_token_encryption_key=_TEST_KEY,
        share_token_ttl_seconds=7200,
    )
    app = create_app(settings=settings)
    engine = init_db_engine(settings.auth_db_url)
    await create_tables(engine)
    priv_key = load_private_key(settings.auth_private_key_file)
    pub_key = load_public_key(settings.auth_public_key_file)
    app.state.settings = settings
    app.state.private_key = priv_key
    app.state.public_key = pub_key
    app.state.jwks_document = build_jwks(settings.auth_active_kid, pub_key)
    app.state.limiter = Limiter(
        key_func=get_remote_address, default_limits=[f"{settings.auth_rate_limit_rpm}/minute"]
    )

    from prometheus_auth.db import get_session_factory

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        created = await register_client(c, name="ttl7200-test")
        r = await c.post(
            f"/admin/clients/{created['client_id']}/share",
            json={"secret": created["client_secret"]},
            headers=ADMIN_HEADERS,
        )
        assert r.status_code == 200, r.text

    async with get_session_factory()() as db:
        result = await db.execute(
            select(CredentialShareToken).where(
                CredentialShareToken.client_id == created["client_id"]
            )
        )
        share = result.scalar_one()

    exp, cre = share.expires_at, share.created_at
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    if cre.tzinfo is None:
        cre = cre.replace(tzinfo=timezone.utc)
    diff = (exp - cre).total_seconds()
    assert 7195 <= diff <= 7205, f"Expected TTL ~7200s, got {diff}s"
    await engine.dispose()


# ── AC-4: TTL > 86400 rejected at startup ─────────────────────────────────────


def test_016_AC4_ttl_too_large(rsa_key_pem_files: tuple[str, str]) -> None:
    """AC-4: SHARE_TOKEN_TTL_SECONDS > 86400 raises ValueError at settings construction."""
    priv, pub = rsa_key_pem_files
    with pytest.raises(ValueError, match="86400"):
        from prometheus_auth.config import Settings

        Settings(
            auth_private_key_file=priv,
            auth_public_key_file=pub,
            auth_active_kid="k",
            auth_jwt_issuer="https://t.test",
            auth_admin_api_key="key",
            auth_db_url="sqlite+aiosqlite:///:memory:",
            share_token_encryption_key=_TEST_KEY,
            share_token_ttl_seconds=90000,
        )


# ── AC-6 (adapted): missing/invalid admin key → 403, not a crash ─────────────


@pytest.mark.asyncio
async def test_016_AC6_admin_key_required(client: AsyncClient) -> None:
    """Generating/revoking a share link without a valid X-Admin-Key is rejected."""
    r = await client.post("/admin/clients/some-id/share", json={"secret": "x"})
    assert r.status_code == 403
    r2 = await client.post(
        "/admin/clients/some-id/share", json={"secret": "x"}, headers={"X-Admin-Key": "wrong"}
    )
    assert r2.status_code == 403


# ── AC-7: unknown principal → 404 ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_016_AC7_unknown_client(client: AsyncClient) -> None:
    """AC-7: Unknown client_id → 404 (no crash)."""
    r = await client.post(
        "/admin/clients/nonexistent-id/share",
        json={"secret": "x"},
        headers=ADMIN_HEADERS,
    )
    assert r.status_code == 404


# ── AC-8, AC-9: one-time credential view ─────────────────────────────────────


@pytest.mark.asyncio
async def test_016_AC8_AC9_one_time_view(client: AsyncClient) -> None:
    """AC-8: Active token returns 200 with credentials; AC-9: secret cleared after view."""
    from sqlalchemy import select

    from prometheus_auth.db import CredentialShareToken, get_session_factory
    from prometheus_auth.share_crypto import encrypt_secret

    token_value = f"testtoken_{uuid.uuid4().hex}"
    plain_secret = "pmt_live_viewtest"

    async with get_session_factory()() as db:
        now = datetime.now(timezone.utc)
        share = CredentialShareToken(
            id=str(uuid.uuid4()),
            token=token_value,
            client_id="view-test-client",
            client_name="View Test",
            client_id_value="view-test-client",
            secret_plaintext_enc=encrypt_secret(_TEST_KEY, plain_secret),
            expires_at=now + timedelta(hours=1),
        )
        db.add(share)
        await db.commit()
        share_id = share.id

    r = await client.get(f"/share/{token_value}")
    assert r.status_code == 200
    assert plain_secret in r.text  # AC-8: secret visible
    assert "view-test-client" in r.text  # AC-8: client_id visible
    assert "Cache-Control" in r.headers  # AC-8
    assert "no-store" in r.headers["Cache-Control"]

    # AC-9: secret cleared, used_at set
    async with get_session_factory()() as db:
        result = await db.execute(
            select(CredentialShareToken).where(CredentialShareToken.id == share_id)
        )
        updated = result.scalar_one()
        assert updated.used_at is not None
        assert updated.used_by_ip is not None
        assert updated.secret_plaintext_enc is None


# ── AC-10: used token → 410 ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_016_AC10_used_token_gone(client: AsyncClient) -> None:
    """AC-10: Already-used token returns 410 with no credential hint."""
    from prometheus_auth.db import CredentialShareToken, get_session_factory

    token_value = f"used_{uuid.uuid4().hex}"
    async with get_session_factory()() as db:
        now = datetime.now(timezone.utc)
        share = CredentialShareToken(
            id=str(uuid.uuid4()),
            token=token_value,
            client_id="used-test-client",
            client_name="Used",
            client_id_value="used-test-client",
            secret_plaintext_enc=None,
            expires_at=now + timedelta(hours=1),
            used_at=now - timedelta(minutes=5),
            used_by_ip="1.2.3.4",
        )
        db.add(share)
        await db.commit()

    r = await client.get(f"/share/{token_value}")
    assert r.status_code == 410
    assert "pmt_live" not in r.text


# ── AC-11: expired token → 410 ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_016_AC11_expired_token_gone(client: AsyncClient) -> None:
    """AC-11: Expired token returns 410."""
    from prometheus_auth.db import CredentialShareToken, get_session_factory
    from prometheus_auth.share_crypto import encrypt_secret

    token_value = f"expired_{uuid.uuid4().hex}"
    async with get_session_factory()() as db:
        now = datetime.now(timezone.utc)
        share = CredentialShareToken(
            id=str(uuid.uuid4()),
            token=token_value,
            client_id="exp-test-client",
            client_name="Expired",
            client_id_value="exp-test-client",
            secret_plaintext_enc=encrypt_secret(_TEST_KEY, "pmt_live_expired"),
            expires_at=now - timedelta(seconds=1),
        )
        db.add(share)
        await db.commit()

    r = await client.get(f"/share/{token_value}")
    assert r.status_code == 410


# ── AC-12: non-existent token → 404 ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_016_AC12_nonexistent_token(client: AsyncClient) -> None:
    """AC-12: Non-existent token returns 404."""
    r = await client.get("/share/this-token-does-not-exist-at-all")
    assert r.status_code == 404


# ── AC-13: security headers ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_016_AC13_security_headers(client: AsyncClient) -> None:
    """AC-13: Response includes X-Robots-Tag: noindex and Referrer-Policy: no-referrer."""
    from prometheus_auth.db import CredentialShareToken, get_session_factory
    from prometheus_auth.share_crypto import encrypt_secret

    token_value = f"headers_{uuid.uuid4().hex}"
    async with get_session_factory()() as db:
        now = datetime.now(timezone.utc)
        share = CredentialShareToken(
            id=str(uuid.uuid4()),
            token=token_value,
            client_id="hdr-test-client",
            client_name="Headers",
            client_id_value="hdr-test-client",
            secret_plaintext_enc=encrypt_secret(_TEST_KEY, "pmt_live_headers"),
            expires_at=now + timedelta(hours=1),
        )
        db.add(share)
        await db.commit()

    r = await client.get(f"/share/{token_value}")
    assert "X-Robots-Tag" in r.headers
    assert "noindex" in r.headers["X-Robots-Tag"]
    assert "Referrer-Policy" in r.headers
    assert "no-referrer" in r.headers["Referrer-Policy"]


# ── AC-14: one-time warning and copy buttons ──────────────────────────────────


@pytest.mark.asyncio
async def test_016_AC14_onetime_warning_copy_buttons(client: AsyncClient) -> None:
    """AC-14: View page shows one-time warning and copy buttons."""
    from prometheus_auth.db import CredentialShareToken, get_session_factory
    from prometheus_auth.share_crypto import encrypt_secret

    token_value = f"warn_{uuid.uuid4().hex}"
    async with get_session_factory()() as db:
        now = datetime.now(timezone.utc)
        share = CredentialShareToken(
            id=str(uuid.uuid4()),
            token=token_value,
            client_id="warn-test-client",
            client_name="Warning Test",
            client_id_value="warn-test-client",
            secret_plaintext_enc=encrypt_secret(_TEST_KEY, "pmt_live_warning"),
            expires_at=now + timedelta(hours=1),
        )
        db.add(share)
        await db.commit()

    r = await client.get(f"/share/{token_value}")
    assert r.status_code == 200
    assert "Save now" in r.text  # one-time warning
    assert "Copy" in r.text  # copy buttons present
    assert "Security checklist" in r.text  # security guidance


# ── AC-15: missing encryption key → startup error ────────────────────────────


def test_016_AC15_missing_encryption_key(rsa_key_pem_files: tuple[str, str]) -> None:
    """AC-15: SHARE_TOKEN_ENCRYPTION_KEY not set → ValueError at startup."""
    priv, pub = rsa_key_pem_files
    with pytest.raises(ValueError, match="SHARE_TOKEN_ENCRYPTION_KEY"):
        from prometheus_auth.config import Settings

        Settings(
            auth_private_key_file=priv,
            auth_public_key_file=pub,
            auth_active_kid="k",
            auth_jwt_issuer="https://t.test",
            auth_admin_api_key="key",
            auth_db_url="sqlite+aiosqlite:///:memory:",
            share_token_encryption_key="",
        )


# ── AC-16: short encryption key → startup error ───────────────────────────────


def test_016_AC16_short_encryption_key(rsa_key_pem_files: tuple[str, str]) -> None:
    """AC-16: SHARE_TOKEN_ENCRYPTION_KEY shorter than 64 hex chars → ValueError."""
    priv, pub = rsa_key_pem_files
    with pytest.raises(ValueError, match="64"):
        from prometheus_auth.config import Settings

        Settings(
            auth_private_key_file=priv,
            auth_public_key_file=pub,
            auth_active_kid="k",
            auth_jwt_issuer="https://t.test",
            auth_admin_api_key="key",
            auth_db_url="sqlite+aiosqlite:///:memory:",
            share_token_encryption_key="abcd1234",  # only 8 chars
        )


# ── AC-17: tampered ciphertext → ValueError ────────────────────────────────────


def test_016_AC17_tampered_ciphertext() -> None:
    """AC-17: Decrypting a tampered ciphertext raises ValueError (GCM tag mismatch)."""
    from prometheus_auth.share_crypto import decrypt_secret, encrypt_secret

    enc = encrypt_secret(_TEST_KEY, "my-secret")
    raw = base64.b64decode(enc)
    tampered = raw[:12] + bytes([raw[12] ^ 0xFF]) + raw[13:]
    tampered_b64 = base64.b64encode(tampered).decode()

    with pytest.raises(ValueError):
        decrypt_secret(_TEST_KEY, tampered_b64)


# ── AC-18: NULL plaintext when token was used ────────────────────────────────


@pytest.mark.asyncio
async def test_016_AC18_null_plaintext_returns_gone(client: AsyncClient) -> None:
    """AC-18: secret_plaintext_enc is NULL → endpoint returns 410, no unhandled exception."""
    from prometheus_auth.db import CredentialShareToken, get_session_factory

    token_value = f"null_{uuid.uuid4().hex}"
    async with get_session_factory()() as db:
        now = datetime.now(timezone.utc)
        share = CredentialShareToken(
            id=str(uuid.uuid4()),
            token=token_value,
            client_id="null-test-client",
            client_name="Null",
            client_id_value="null-test-client",
            secret_plaintext_enc=None,  # already consumed
            expires_at=now + timedelta(hours=1),
            used_at=now - timedelta(minutes=1),
        )
        db.add(share)
        await db.commit()

    r = await client.get(f"/share/{token_value}")
    assert r.status_code == 410


# ── AC-19: revoke active token ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_016_AC19_revoke_active_token(client: AsyncClient) -> None:
    """AC-19: Revoking an active token sets revoked_at and clears secret."""
    from sqlalchemy import select

    from prometheus_auth.db import CredentialShareToken, get_session_factory
    from prometheus_auth.share_crypto import encrypt_secret

    token_value = f"revoke_{uuid.uuid4().hex}"
    async with get_session_factory()() as db:
        now = datetime.now(timezone.utc)
        share = CredentialShareToken(
            id=str(uuid.uuid4()),
            token=token_value,
            client_id="revoke-test-client",
            client_name="Revoke",
            client_id_value="revoke-test-client",
            secret_plaintext_enc=encrypt_secret(_TEST_KEY, "pmt_live_revoke"),
            expires_at=now + timedelta(hours=1),
        )
        db.add(share)
        await db.commit()
        share_id = share.id

    r = await client.post(f"/admin/clients/share/{share_id}/revoke", headers=ADMIN_HEADERS)
    assert r.status_code == 200, r.text
    assert r.json() == {"token_id": share_id, "revoked": True}

    async with get_session_factory()() as db:
        result = await db.execute(
            select(CredentialShareToken).where(CredentialShareToken.id == share_id)
        )
        updated = result.scalar_one()
        assert updated.revoked_at is not None
        assert updated.revoked_by == "admin"
        assert updated.secret_plaintext_enc is None


# ── AC-20: revoked token → 410 ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_016_AC20_revoked_token_gone(client: AsyncClient) -> None:
    """AC-20: Viewing a revoked token returns 410."""
    from prometheus_auth.db import CredentialShareToken, get_session_factory

    token_value = f"revoked_{uuid.uuid4().hex}"
    async with get_session_factory()() as db:
        now = datetime.now(timezone.utc)
        share = CredentialShareToken(
            id=str(uuid.uuid4()),
            token=token_value,
            client_id="rev-test-client",
            client_name="Revoked",
            client_id_value="rev-test-client",
            secret_plaintext_enc=None,
            expires_at=now + timedelta(hours=1),
            revoked_at=now - timedelta(minutes=1),
            revoked_by="admin",
        )
        db.add(share)
        await db.commit()

    r = await client.get(f"/share/{token_value}")
    assert r.status_code == 410


# ── AC-21: revoke already-used → 409 ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_016_AC21_revoke_used_token_conflict(client: AsyncClient) -> None:
    """AC-21: Trying to revoke an already-used token returns 409."""
    from prometheus_auth.db import CredentialShareToken, get_session_factory

    token_value = f"already_used_{uuid.uuid4().hex}"
    async with get_session_factory()() as db:
        now = datetime.now(timezone.utc)
        share = CredentialShareToken(
            id=str(uuid.uuid4()),
            token=token_value,
            client_id="used-revoke-client",
            client_name="UsedRevoke",
            client_id_value="used-revoke-client",
            secret_plaintext_enc=None,
            expires_at=now + timedelta(hours=1),
            used_at=now - timedelta(minutes=2),
        )
        db.add(share)
        await db.commit()
        share_id = share.id

    r = await client.post(f"/admin/clients/share/{share_id}/revoke", headers=ADMIN_HEADERS)
    assert r.status_code == 409


# ── AC-25: share_token_used log event ────────────────────────────────────────


@pytest.mark.asyncio
async def test_016_AC25_used_log_event(client: AsyncClient, capsys: pytest.CaptureFixture) -> None:
    """AC-25: Consuming a token emits share_token_used log with token_id and token_prefix."""
    import json

    from prometheus_auth.db import CredentialShareToken, get_session_factory
    from prometheus_auth.share_crypto import encrypt_secret

    token_value = f"logtest_{uuid.uuid4().hex}"
    async with get_session_factory()() as db:
        now = datetime.now(timezone.utc)
        share = CredentialShareToken(
            id=str(uuid.uuid4()),
            token=token_value,
            client_id="log-test-client",
            client_name="Log",
            client_id_value="log-test-client",
            secret_plaintext_enc=encrypt_secret(_TEST_KEY, "pmt_live_log"),
            expires_at=now + timedelta(hours=1),
        )
        db.add(share)
        await db.commit()

    await client.get(f"/share/{token_value}")

    captured = capsys.readouterr()
    log_events = []
    for line in captured.out.splitlines():
        try:
            log_events.append(json.loads(line))
        except (json.JSONDecodeError, ValueError):
            pass

    assert any(
        "share_token_used" in e.get("event", "") or "auth.share_token_used" in e.get("event", "")
        for e in log_events
    ), "Expected share_token_used log event"
    assert not any(token_value in line for line in captured.out.splitlines())


# ── AC-26: share_token_created log event ─────────────────────────────────────


@pytest.mark.asyncio
async def test_016_AC26_created_log_event(
    client: AsyncClient, capsys: pytest.CaptureFixture
) -> None:
    """AC-26: Generating a token emits share_token_created log without exposing full token."""
    import json

    created = await register_client(client, name="logcreate-test")

    await client.post(
        f"/admin/clients/{created['client_id']}/share",
        json={"secret": created["client_secret"]},
        headers=ADMIN_HEADERS,
    )

    captured = capsys.readouterr()
    log_events = []
    for line in captured.out.splitlines():
        try:
            log_events.append(json.loads(line))
        except (json.JSONDecodeError, ValueError):
            pass
    assert any(
        "share_token_created" in e.get("event", "")
        or "auth.share_token_created" in e.get("event", "")
        for e in log_events
    ), "Expected share_token_created log event"


# ── AC-27: credential_share_tokens table exists after startup ────────────────


@pytest.mark.asyncio
async def test_016_AC27_table_created_on_startup(client: AsyncClient) -> None:
    """AC-27: credential_share_tokens table exists after create_tables()."""
    from sqlalchemy import text

    from prometheus_auth.db import get_engine

    async with get_engine().connect() as conn:
        result = await conn.execute(
            text(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='credential_share_tokens'"
            )
        )
        rows = result.fetchall()
    assert len(rows) == 1, "credential_share_tokens table not found in DB"


# ── AC-28: creating new share link auto-revokes existing active one ───────────


@pytest.mark.asyncio
async def test_016_AC28_new_share_revokes_old(client: AsyncClient) -> None:
    """AC-28: Generating a new share link auto-revokes any existing active token."""
    from sqlalchemy import select

    from prometheus_auth.db import CredentialShareToken, get_session_factory
    from prometheus_auth.share_crypto import encrypt_secret

    created = await register_client(client, name="ac28-test")
    client_id = created["client_id"]

    async with get_session_factory()() as db:
        now = datetime.now(timezone.utc)
        old_token_value = f"old_{uuid.uuid4().hex}"
        old_share = CredentialShareToken(
            id=str(uuid.uuid4()),
            token=old_token_value,
            client_id=client_id,
            client_name="ac28-test",
            client_id_value=client_id,
            secret_plaintext_enc=encrypt_secret(_TEST_KEY, "old-secret"),
            expires_at=now + timedelta(hours=1),
        )
        db.add(old_share)
        await db.commit()
        old_share_id = old_share.id

    r = await client.post(
        f"/admin/clients/{client_id}/share",
        json={"secret": created["client_secret"]},
        headers=ADMIN_HEADERS,
    )
    assert r.status_code == 200, r.text

    async with get_session_factory()() as db:
        result = await db.execute(
            select(CredentialShareToken).where(CredentialShareToken.id == old_share_id)
        )
        old = result.scalar_one()
        assert old.revoked_at is not None, "Old active token was not revoked"
        assert old.secret_plaintext_enc is None

    async with get_session_factory()() as db:
        result = await db.execute(
            select(CredentialShareToken).where(
                CredentialShareToken.client_id == client_id,
                CredentialShareToken.token != old_token_value,
            )
        )
        new_share = result.scalar_one_or_none()
        assert new_share is not None
        assert new_share.revoked_at is None
        assert new_share.secret_plaintext_enc is not None
