"""Tests for RM-60 — /admin/api/billing/* (admin/billing_router.py)."""

from __future__ import annotations

from datetime import date

import fakeredis.aioredis as fakeredis
import pytest
from httpx import ASGITransport, AsyncClient

from prometheus_gateway import db
from prometheus_gateway.config import Settings
from prometheus_gateway.main import create_app
from prometheus_gateway.models.registry import ModelRegistry
from tests.conftest import make_token


@pytest.fixture
def fake_redis():
    return fakeredis.FakeRedis()


@pytest.fixture
def registry(tmp_path):
    yaml_content = """models:
  - id: small-model
    path: /dev/null
    context_length: 4096
    family: llama3
    quantization: Q4_0
    backend_url: "http://127.0.0.1:18081"
"""
    f = tmp_path / "registry.yaml"
    f.write_text(yaml_content)
    return ModelRegistry(f)


@pytest.fixture
def settings(rsa_keys, tmp_path):
    key_file = tmp_path / "public.pem"
    key_file.write_text(rsa_keys["public"])
    admin_static = tmp_path / "admin-static"
    admin_static.mkdir()
    return Settings(
        jwt_issuer="https://auth.test",
        jwt_audience="prometheus-gateway",
        jwt_public_key_file=str(key_file),
        jwt_revocation_redis_url=None,
        rate_limit_strict=False,
        admin_dashboard_enabled=True,
        auth_service_admin_url="http://auth.test",
        auth_service_admin_api_key="secret",
    )


@pytest.fixture
def app(settings, registry, fake_redis):
    return create_app(settings=settings, registry=registry, redis_client=fake_redis)


@pytest.fixture
def admin_read_headers(rsa_keys):
    token = make_token(
        rsa_keys["private"], scope="admin:read", sub="admin-user", azp="admin-client"
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def admin_write_headers(rsa_keys):
    token = make_token(
        rsa_keys["private"], scope="admin:read admin:write", sub="admin-user", azp="admin-client"
    )
    return {"Authorization": f"Bearer {token}"}


async def test_get_settings_defaults_when_no_row(app, admin_read_headers):
    await db.create_tables(db.get_engine())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/admin/api/billing/clients/client-a/settings", headers=admin_read_headers)

    assert r.status_code == 200
    body = r.json()
    assert body["client_id"] == "client-a"
    assert body["monthly_spend_cap_usd"] is None
    assert body["preferred_currency"] == "USD"


async def test_get_settings_requires_admin_read(app, rsa_keys):
    await db.create_tables(db.get_engine())
    token = make_token(rsa_keys["private"], scope="inference:read", sub="user-x", azp="client-a")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get(
            "/admin/api/billing/clients/client-a/settings",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert r.status_code == 403


async def test_put_settings_requires_admin_write(app, admin_read_headers):
    await db.create_tables(db.get_engine())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.put(
            "/admin/api/billing/clients/client-a/settings",
            json={"monthly_spend_cap_usd": 10.0},
            headers=admin_read_headers,
        )
    assert r.status_code == 403


async def test_put_and_get_settings_roundtrip(app, admin_write_headers):
    await db.create_tables(db.get_engine())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        put_r = await c.put(
            "/admin/api/billing/clients/client-a/settings",
            json={
                "monthly_spend_cap_usd": 25.0,
                "alert_thresholds_percent": "50,90",
                "tax_rate_percent": 18.0,
                "preferred_currency": "PEN",
            },
            headers=admin_write_headers,
        )
        assert put_r.status_code == 200

        get_r = await c.get(
            "/admin/api/billing/clients/client-a/settings", headers=admin_write_headers
        )

    body = get_r.json()
    assert body["monthly_spend_cap_usd"] == 25.0
    assert body["tax_rate_percent"] == 18.0
    assert body["preferred_currency"] == "PEN"


async def test_put_settings_rejects_invalid_currency(app, admin_write_headers):
    await db.create_tables(db.get_engine())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.put(
            "/admin/api/billing/clients/client-a/settings",
            json={"preferred_currency": "XYZ"},
            headers=admin_write_headers,
        )
    assert r.status_code == 400


async def test_currency_rates_roundtrip(app, admin_write_headers):
    await db.create_tables(db.get_engine())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        put_r = await c.put(
            "/admin/api/billing/currency-rates",
            json={"PEN": 3.75, "EUR": 0.92},
            headers=admin_write_headers,
        )
        assert put_r.status_code == 200

        get_r = await c.get("/admin/api/billing/currency-rates", headers=admin_write_headers)

    assert get_r.json() == {"PEN": 3.75, "EUR": 0.92}


async def test_currency_rates_rejects_unsupported_currency(app, admin_write_headers):
    await db.create_tables(db.get_engine())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.put(
            "/admin/api/billing/currency-rates", json={"JPY": 150.0}, headers=admin_write_headers
        )
    assert r.status_code == 400


async def test_billing_summary_reflects_recorded_usage(app, admin_read_headers):
    await db.create_tables(db.get_engine())
    today = date.today()
    period = today.strftime("%Y-%m")
    await db.record_usage("client-a", "small-model", 100, 50, day=today)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get(
            "/admin/api/billing/clients/client-a/summary",
            params={"period": period},
            headers=admin_read_headers,
        )

    assert r.status_code == 200
    body = r.json()
    assert body["period"] == period
    assert body["total_tokens"] == 150
    assert body["request_count"] == 1
    assert body["preferred_currency"] == "USD"
    assert body["by_model"] == [
        {"model_id": "small-model", "cost_usd": None, "tokens": 150, "request_count": 1}
    ]


async def test_billing_summary_invalid_period_returns_400(app, admin_read_headers):
    await db.create_tables(db.get_engine())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get(
            "/admin/api/billing/clients/client-a/summary",
            params={"period": "not-a-period"},
            headers=admin_read_headers,
        )
    assert r.status_code == 400


async def test_billing_history_returns_requested_number_of_periods(app, admin_read_headers):
    await db.create_tables(db.get_engine())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get(
            "/admin/api/billing/clients/client-a/history",
            params={"periods": 3},
            headers=admin_read_headers,
        )

    assert r.status_code == 200
    body = r.json()
    assert len(body["periods"]) == 3


async def test_alerts_empty_when_no_capped_clients(app, admin_read_headers):
    await db.create_tables(db.get_engine())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/admin/api/billing/alerts", headers=admin_read_headers)

    assert r.status_code == 200
    assert r.json() == {"object": "list", "data": []}


async def test_alerts_degrades_gracefully_when_redis_unreachable(
    settings, registry, admin_read_headers
):
    """Regression test — found live: a capped client whose Redis lookup fails
    (e.g. Redis down) used to 500 the whole /admin/api/billing/alerts poll,
    which the dashboard hits every ~15s. Must degrade to skipping that
    client, not crash the endpoint.
    """

    class BrokenRedis:
        async def smembers(self, *a, **kw):
            raise ConnectionError("Redis down")

        async def get(self, *a, **kw):
            raise ConnectionError("Redis down")

    app = create_app(settings=settings, registry=registry, redis_client=BrokenRedis())
    await db.create_tables(db.get_engine())
    await db.upsert_client_billing_settings(
        "client-a",
        monthly_spend_cap_usd=10.0,
        alert_thresholds_percent=None,
        tax_rate_percent=0.0,
        preferred_currency="USD",
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/admin/api/billing/alerts", headers=admin_read_headers)

    assert r.status_code == 200
    assert r.json() == {"object": "list", "data": []}


async def test_put_settings_invalidates_cache_for_hot_path(app, admin_write_headers):
    """A saved cap change must be visible immediately, not stuck behind the
    30s in-process cache used by the hot inference path (budget.py).
    """
    from prometheus_gateway.budget import get_client_billing_settings_cached

    await db.create_tables(db.get_engine())
    # Prime the cache with "no settings" (None).
    assert await get_client_billing_settings_cached("client-a") is None

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        await c.put(
            "/admin/api/billing/clients/client-a/settings",
            json={"monthly_spend_cap_usd": 5.0},
            headers=admin_write_headers,
        )

    fetched = await get_client_billing_settings_cached("client-a")
    assert fetched is not None
    assert fetched.monthly_spend_cap_usd == 5.0
