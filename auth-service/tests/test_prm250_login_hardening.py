"""PRM-250: the sign-in endpoint stops answering two questions it was asked.

Implements: docs/roadmap.md — PRM-250.

Both holes were measured against the running deployment, not inferred:

  * a wrong password for a real account answered in 179ms and for an unknown
    address in 4.5ms, because bcrypt only ran when the account existed. The
    wording was identical either way, so the refusal gave nothing away and the
    clock gave away everything;
  * fifteen wrong passwords in a row were all answered at full speed, by a
    service configured for ten a minute — slowapi's `default_limits` bind
    through `@limiter.limit` or `SlowAPIMiddleware`, and this service had
    neither, so the limiter it built was never consulted.
"""

from __future__ import annotations

import time

import pytest

from .conftest import ADMIN_HEADERS


async def _make_password_principal(client, email: str, password: str) -> None:
    response = await client.post(
        "/admin/clients",
        headers=ADMIN_HEADERS,
        json={
            "client_name": f"probe-{email.split('@')[0]}",
            "role": "app",
            "allowed_scopes": ["inference:read"],
            "auth_method": "password",
            "email": email,
            "password": password,
        },
    )
    assert response.status_code == 200, response.text


async def _time_refusal(client, email: str, password: str) -> float:
    started = time.perf_counter()
    response = await client.post(
        "/oauth2/token",
        data={
            "grant_type": "password",
            "username": email,
            "password": password,
            "scope": "inference:read",
        },
    )
    assert response.status_code == 401, response.text
    return time.perf_counter() - started


async def test_an_unknown_account_costs_the_same_as_a_wrong_password(client):
    """The clock must not answer what the message refuses to.

    Before this, the gap was 179ms against 4.5ms — a fortyfold tell, enough to
    enumerate every operator address at about a hundred guesses a second. The
    bound is deliberately loose: the point is that both paths pay bcrypt, not
    that they agree to the microsecond, and the residual difference is a
    database lookup.
    """
    await _make_password_principal(client, "real@example.com", "CorrectHorse!2026")

    real = await _time_refusal(client, "real@example.com", "wrong-password")
    unknown = await _time_refusal(client, "no-such-account@example.com", "wrong-password")

    ratio = max(real, unknown) / min(real, unknown)
    assert ratio < 3, (
        f"an unknown account answered in {unknown * 1000:.1f}ms and a real one in "
        f"{real * 1000:.1f}ms (ratio {ratio:.1f}x) — the timing says which "
        "addresses exist even though the message does not"
    )


async def test_failed_sign_ins_run_out_of_budget(client, settings):
    """A refusal that costs nothing can be repeated forever."""
    await _make_password_principal(client, "budget@example.com", "CorrectHorse!2026")

    budget = settings.auth_login_max_failures_per_identity
    for attempt in range(budget):
        response = await client.post(
            "/oauth2/token",
            data={
                "grant_type": "password",
                "username": "budget@example.com",
                "password": "wrong",
                "scope": "inference:read",
            },
        )
        assert response.status_code == 401, f"attempt {attempt + 1}: {response.text}"

    refused = await client.post(
        "/oauth2/token",
        data={
            "grant_type": "password",
            "username": "budget@example.com",
            "password": "wrong",
            "scope": "inference:read",
        },
    )
    assert refused.status_code == 429, (
        f"attempt {budget + 1} was still served — the budget is not enforced"
    )
    assert int(refused.headers["Retry-After"]) > 0


async def test_the_refusal_does_not_say_whether_the_account_exists(client):
    """429 is reached by failure count alone, so it reveals nothing either."""
    await _make_password_principal(client, "quiet@example.com", "CorrectHorse!2026")

    real = await client.post(
        "/oauth2/token",
        data={"grant_type": "password", "username": "quiet@example.com", "password": "no"},
    )
    unknown = await client.post(
        "/oauth2/token",
        data={"grant_type": "password", "username": "ghost@example.com", "password": "no"},
    )
    assert real.json() == unknown.json()


async def test_signing_in_clears_what_the_typos_spent(client):
    """Four typos then the right password must not leave a half-spent budget."""
    await _make_password_principal(client, "typo@example.com", "CorrectHorse!2026")

    for _ in range(settings_budget := 3):
        await client.post(
            "/oauth2/token",
            data={"grant_type": "password", "username": "typo@example.com", "password": "nope"},
        )
    assert settings_budget == 3

    ok = await client.post(
        "/oauth2/token",
        data={
            "grant_type": "password",
            "username": "typo@example.com",
            "password": "CorrectHorse!2026",
            "scope": "inference:read",
        },
    )
    assert ok.status_code == 200, ok.text

    # The budget is back: a fresh run of failures gets the full allowance.
    again = await client.post(
        "/oauth2/token",
        data={"grant_type": "password", "username": "typo@example.com", "password": "nope"},
    )
    assert again.status_code == 401, "the successful sign-in did not clear the failures"


# ── PRM-251: whose budget an attempt spends ──────────────────────────────────


@pytest.fixture
async def settings_with_proxy_client(rsa_key_pem_files):
    """A service told to believe the test transport's own address.

    The ASGI transport presents as 127.0.0.1, which is what the gateway looks
    like on a real deployment — it proxies sign-ins from the same host.
    """
    from httpx import ASGITransport, AsyncClient
    from slowapi import Limiter
    from slowapi.util import get_remote_address

    from prometheus_auth.config import Settings
    from prometheus_auth.crypto import build_jwks, load_private_key, load_public_key
    from prometheus_auth.db import create_tables, init_db_engine
    from prometheus_auth.main import create_app

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
        share_token_encryption_key="a" * 64,
        auth_trusted_proxy_ips="127.0.0.1",
    )
    app = create_app(settings=settings)
    engine = init_db_engine(settings.auth_db_url)
    await create_tables(engine)
    app.state.settings = settings
    app.state.private_key = load_private_key(settings.auth_private_key_file)
    app.state.public_key = load_public_key(settings.auth_public_key_file)
    app.state.jwks_document = build_jwks(
        settings.auth_active_kid, load_public_key(settings.auth_public_key_file)
    )
    app.state.limiter = Limiter(
        key_func=get_remote_address,
        default_limits=[f"{settings.auth_rate_limit_rpm}/minute"],
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c, settings

    await engine.dispose()


async def _fail_from(client, identity: str, forwarded: str | None = None):
    headers = {"X-Forwarded-For": forwarded} if forwarded is not None else {}
    return await client.post(
        "/oauth2/token",
        headers=headers,
        data={"grant_type": "password", "username": identity, "password": "wrong"},
    )


async def test_an_untrusted_peer_cannot_buy_a_fresh_budget(client, settings):
    """The header is ignored unless the peer is one this service was told about.

    Believing it unconditionally would turn the address budget into a bypass:
    a different address on every request is an unlimited budget. The test
    client is not a configured proxy, so its header must count for nothing and
    all of these attempts must land in the same bucket.
    """
    from prometheus_auth.config import Settings

    # Read off the class, not the fixture: this is the value a fresh
    # deployment ships with, and it has to be "trust nobody".
    assert Settings.model_fields["auth_trusted_proxy_ips"].default == "", (
        "a deployment that believes X-Forwarded-For out of the box turns the "
        "address budget into a bypass"
    )
    assert settings.auth_trusted_proxy_ips == ""

    budget = settings.auth_login_max_failures_per_address
    for attempt in range(budget):
        response = await _fail_from(
            client, f"spoof-{attempt}@example.com", forwarded=f"10.0.0.{attempt}"
        )
        assert response.status_code == 401, f"attempt {attempt + 1}: {response.text}"

    refused = await _fail_from(client, "spoof-last@example.com", forwarded="10.0.0.254")
    assert refused.status_code == 429, (
        "a new X-Forwarded-For bought a fresh address budget from an untrusted peer"
    )


async def test_a_trusted_proxy_separates_one_caller_from_another(settings_with_proxy_client):
    """Two people behind the gateway must not share a failed-attempt budget.

    Without this, a stranger failing against the public login spends the budget
    the platform's own operators draw from — and the refusal is issued before
    the password is looked at, so their correct password is refused too.
    """
    client, settings = settings_with_proxy_client
    budget = settings.auth_login_max_failures_per_address

    for attempt in range(budget):
        response = await _fail_from(client, f"a-{attempt}@example.com", forwarded="203.0.113.9")
        assert response.status_code == 401, f"attempt {attempt + 1}: {response.text}"

    exhausted = await _fail_from(client, "a-last@example.com", forwarded="203.0.113.9")
    assert exhausted.status_code == 429, "the stranger's own budget was never spent"

    # A different caller behind the same proxy still has their full budget.
    other = await _fail_from(client, "b@example.com", forwarded="198.51.100.4")
    assert other.status_code == 401, (
        "one caller exhausting their budget locked out everyone behind the proxy"
    )


# ── PRM-252: the trace carries what the caller is not told ───────────────────


async def test_the_span_names_the_attempt_its_source_and_why_it_failed(client):
    """Asserted, not assumed.

    The refusal is deliberately uninformative to the caller, so the span is
    where an operator finds out which attempt it was, from where, and whether
    the account even exists. Setting the attributes and exporting them are
    different things, and only the second one is any use at 3am.
    """
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    import prometheus_auth.routers.oauth2 as oauth2_module

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    previous_tracer = oauth2_module._tracer
    oauth2_module._tracer = provider.get_tracer("test")
    try:
        await _fail_from(client, "traced@example.com")
    finally:
        oauth2_module._tracer = previous_tracer

    spans = [s for s in exporter.get_finished_spans() if s.name == "token.issuance"]
    assert spans, "the sign-in produced no token.issuance span"
    attributes = dict(spans[-1].attributes or {})
    assert attributes.get("auth.identity") == "traced@example.com"
    assert attributes.get("auth.source_address") == "127.0.0.1"
    assert attributes.get("auth.failure_reason") == "unknown_email"
    assert attributes.get("http.status_code") == 401
