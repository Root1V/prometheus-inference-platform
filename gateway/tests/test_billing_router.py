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
        {
            "model_id": "small-model",
            "cost_usd": None,
            "tokens": 150,
            "request_count": 1,
            "unpriced_requests": 1,
        }
    ]
    # PRM-119: and the period says so rather than reporting a confident zero.
    assert body["subtotal_usd"] is None
    assert body["total_usd"] is None
    assert body["unpriced_requests"] == 1


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


# ── RM-60 follow-up: admin-configurable model pricing (replaces pricing.yaml) ──


async def test_get_pricing_empty_when_nothing_configured(app, admin_read_headers):
    await db.create_tables(db.get_engine())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/admin/api/billing/pricing", headers=admin_read_headers)

    assert r.status_code == 200
    body = r.json()
    assert body["object"] == "list"
    assert body["data"] == {}
    # PRM-125 appended defaults_by_modality; the point of this test is that no
    # model is priced, not that the response has exactly two keys.
    assert set(body) == {"object", "data", "defaults_by_modality"}


async def test_put_pricing_requires_admin_write(app, admin_read_headers):
    await db.create_tables(db.get_engine())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.put(
            "/admin/api/billing/pricing/small-model",
            json={"prompt_price_per_1m": 1.0, "completion_price_per_1m": 2.0},
            headers=admin_read_headers,
        )
    assert r.status_code == 403


async def test_put_pricing_persists_and_applies_live(app, admin_write_headers):
    """The new price must be usable by the very next record_usage() call —
    no restart — and survive by being written to ModelPriceConfig too.
    """
    from prometheus_gateway import pricing

    await db.create_tables(db.get_engine())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.put(
            "/admin/api/billing/pricing/small-model",
            json={"prompt_price_per_1m": 1.0, "completion_price_per_1m": 2.0},
            headers=admin_write_headers,
        )
    assert r.status_code == 200
    assert r.json() == {
        "model_id": "small-model",
        "prompt_price_per_1m": 1.0,
        "completion_price_per_1m": 2.0,
        "image_price": None,
        "source": "db",
    }

    # Applied immediately in-memory, no restart.
    assert pricing.get_pricing_table().estimate_cost_usd("small-model", 1_000_000, 0) == 1.0

    # Persisted to the DB.
    rows = await db.list_model_price_configs()
    assert len(rows) == 1
    assert rows[0].model_id == "small-model"
    assert rows[0].prompt_price_per_1m == 1.0


async def test_put_pricing_rejects_partial_token_price(app, admin_write_headers):
    await db.create_tables(db.get_engine())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.put(
            "/admin/api/billing/pricing/small-model",
            json={"prompt_price_per_1m": 1.0},
            headers=admin_write_headers,
        )
    assert r.status_code == 400


async def test_put_pricing_image_only_is_allowed(app, admin_write_headers):
    await db.create_tables(db.get_engine())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.put(
            "/admin/api/billing/pricing/image-model",
            json={"image_price": 0.02},
            headers=admin_write_headers,
        )
    assert r.status_code == 200
    assert r.json()["image_price"] == 0.02


async def test_delete_pricing_replaces_the_override_rather_than_removing_it(
    app, admin_write_headers
):
    """This asserted the opposite until PRM-124, and the change was deliberate.

    DELETE used to mean "remove the override", leaving the model unpriced. That
    was right while unpriced was a normal state. PRM-120 made "every catalogued
    model has a price" an invariant, so the same call now swaps the operator's
    figure for the modality's base price: the override is gone, which is what
    was asked for, and the model is not left billing nothing, which was never
    what the reset button looked like it promised.
    """
    from prometheus_gateway import pricing

    await db.create_tables(db.get_engine())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        await c.put(
            "/admin/api/billing/pricing/small-model",
            json={"prompt_price_per_1m": 1.0, "completion_price_per_1m": 2.0},
            headers=admin_write_headers,
        )
        del_r = await c.delete(
            "/admin/api/billing/pricing/small-model", headers=admin_write_headers
        )
    assert del_r.status_code == 204

    rows = await db.list_model_price_configs()
    assert [r.model_id for r in rows] == ["small-model"]
    assert rows[0].is_default is True, "the operator's figure is gone"
    assert rows[0].prompt_price_per_1m == pricing.default_price_for("text").prompt_price_per_1m
    assert pricing.get_pricing_table().estimate_cost_usd("small-model", 1_000_000, 0) is not None


async def test_get_pricing_reflects_yaml_and_db_sources(
    tmp_path, rsa_keys, registry, fake_redis, admin_read_headers, admin_write_headers
):
    """A model priced only in pricing.yaml shows source="file"; one overridden
    (or newly added) via the admin API shows source="db".
    """
    key_file = tmp_path / "public.pem"
    key_file.write_text(rsa_keys["public"])
    pricing_file = tmp_path / "pricing.yaml"
    pricing_file.write_text(
        "models:\n  - id: yaml-only-model\n    prompt_price_per_1m: 1.0\n"
        "    completion_price_per_1m: 1.0\n"
    )
    settings = Settings(
        jwt_issuer="https://auth.test",
        jwt_audience="prometheus-gateway",
        jwt_public_key_file=str(key_file),
        jwt_revocation_redis_url=None,
        rate_limit_strict=False,
        admin_dashboard_enabled=True,
        auth_service_admin_url="http://auth.test",
        auth_service_admin_api_key="secret",
        pricing_file=str(pricing_file),
    )
    app = create_app(settings=settings, registry=registry, redis_client=fake_redis)
    await db.create_tables(db.get_engine())

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        await c.put(
            "/admin/api/billing/pricing/db-model",
            json={"prompt_price_per_1m": 5.0, "completion_price_per_1m": 5.0},
            headers=admin_write_headers,
        )
        r = await c.get("/admin/api/billing/pricing", headers=admin_read_headers)

    body = r.json()["data"]
    assert body["yaml-only-model"]["source"] == "file"
    assert body["db-model"]["source"] == "db"


# ── PRM-119: an invoice must not state a zero it cannot stand behind ────────


async def test_a_period_with_no_priced_usage_is_unknown_not_zero(app, admin_read_headers):
    """Reported from the dashboard, with a screenshot: six requests, every row
    showing "—", and a period total reading USD 0.00.

    The rows were right — those models genuinely have no configured price, and
    "no price" is not "free". The total was wrong: `sum(cost or 0.0)` turned
    six unknowns into a confident zero, which is the one thing every other
    layer of this system refuses to do. A client reading that total concludes
    they owe nothing; the truth is we cannot say what they owe.
    """
    await db.create_tables(db.get_engine())
    today = date.today()
    for _ in range(4):
        await db.record_usage("client-a", "unpriced-chat", 10, 8, day=today)
    for _ in range(2):
        await db.record_usage(
            "client-a", "unpriced-rerank", 167, 0, request_kind="rerank", day=today
        )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get(
            "/admin/api/billing/clients/client-a/summary",
            params={"period": today.strftime("%Y-%m")},
            headers=admin_read_headers,
        )

    body = r.json()
    assert body["request_count"] == 6
    assert body["subtotal_usd"] is None, "six unknowns are not zero dollars"
    assert body["tax_amount_usd"] is None, "tax on an unknown is not zero"
    assert body["total_usd"] is None
    assert body["total_in_preferred_currency"] is None, (
        "converting an unknown would invent a figure in the client's own currency"
    )
    assert body["unpriced_requests"] == 6


async def test_a_partly_priced_period_says_how_much_it_misses(app, admin_write_headers):
    """The more dangerous case, because it looks complete. A subtotal that
    covers three of five requests is a lower bound, and nothing in the figure
    itself says so."""
    await db.create_tables(db.get_engine())
    today = date.today()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        await c.put(
            "/admin/api/billing/pricing/priced-model",
            json={"prompt_price_per_1m": 1000.0, "completion_price_per_1m": 1000.0},
            headers=admin_write_headers,
        )
        await db.record_usage("client-b", "priced-model", 1000, 0, day=today)
        await db.record_usage("client-b", "unpriced-model", 500, 0, day=today)

        r = await c.get(
            "/admin/api/billing/clients/client-b/summary",
            params={"period": today.strftime("%Y-%m")},
            headers=admin_write_headers,
        )

    body = r.json()
    assert body["request_count"] == 2
    assert body["subtotal_usd"] == pytest.approx(1000 * 1000 / 1_000_000)
    assert body["unpriced_requests"] == 1, "the figure covers one of two requests and must say so"


# ── PRM-124: reset means back to the base price, not back to nothing ────────


async def test_reset_restores_the_base_price_instead_of_emptying_the_row(app, admin_write_headers):
    """Reported from the pricing table: pressing the reset arrow put the row
    back to a dash.

    That was correct behaviour until PRM-120 made "every model has a price" an
    invariant. Since then, "remove the override" leaves the one state the table
    is no longer supposed to have — and an unpriced model is not just a blank
    cell, it is a model that bills nothing and is never budget-checked.

    `small-model` is text, and the registry fixture serves it, so the modality
    is resolved without reaching for the catalog.
    """
    await db.create_tables(db.get_engine())
    from prometheus_gateway import pricing

    base = pricing.default_price_for("text")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        await c.put(
            "/admin/api/billing/pricing/small-model",
            json={"prompt_price_per_1m": 99.0, "completion_price_per_1m": 99.0},
            headers=admin_write_headers,
        )
        r = await c.delete("/admin/api/billing/pricing/small-model", headers=admin_write_headers)
        assert r.status_code == 204

        listing = await c.get("/admin/api/billing/pricing", headers=admin_write_headers)

    entry = listing.json()["data"]["small-model"]
    assert entry["prompt_price_per_1m"] == base.prompt_price_per_1m
    assert entry["completion_price_per_1m"] == base.completion_price_per_1m
    assert entry["source"] == "default", "and it stops claiming to be somebody's decision"

    # The live table too, or the next request bills at nothing until a restart.
    assert pricing.get_pricing_table().get_price("small-model") is not None


async def test_reset_leaves_a_model_unpriced_only_when_its_modality_is_unknown(
    app, admin_write_headers
):
    """A model the gateway does not serve and no node admits to having. Better
    unpriced than priced as whatever modality we felt like guessing."""
    await db.create_tables(db.get_engine())

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        await c.put(
            "/admin/api/billing/pricing/ghost-model",
            json={"prompt_price_per_1m": 5.0, "completion_price_per_1m": 5.0},
            headers=admin_write_headers,
        )
        await c.delete("/admin/api/billing/pricing/ghost-model", headers=admin_write_headers)
        listing = await c.get("/admin/api/billing/pricing", headers=admin_write_headers)

    assert "ghost-model" not in listing.json()["data"]


async def test_the_listing_carries_the_base_prices_for_the_reset_button(app, admin_read_headers):
    """PRM-125. Both toolbar buttons propose a price and neither writes one —
    Save is what commits. The calculator already worked that way; reset did not,
    and its write cost a real figure somebody had set (0.437 became 0.02 on this
    deployment, unnoticed, because nothing asked first).

    Filling the inputs needs the base prices client-side, and they ship with the
    listing rather than from a second endpoint so the two cannot disagree about
    what a row should be reset to.
    """
    from prometheus_gateway import pricing

    await db.create_tables(db.get_engine())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get("/admin/api/billing/pricing", headers=admin_read_headers)

    defaults = r.json()["defaults_by_modality"]
    for modality in ("text", "vision", "embedding", "rerank", "image"):
        assert modality in defaults, f"{modality} has a base price but the UI cannot see it"
        assert defaults[modality] == {
            "prompt_price_per_1m": pricing.default_price_for(modality).prompt_price_per_1m,
            "completion_price_per_1m": pricing.default_price_for(modality).completion_price_per_1m,
            "image_price": pricing.default_price_for(modality).image_price,
        }


async def test_reading_the_prices_never_writes_one(app, admin_read_headers):
    """The listing is a GET and admin:read only — it must not be the thing that
    seeds anything, or a page load would become a billing change."""
    await db.create_tables(db.get_engine())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        await c.get("/admin/api/billing/pricing", headers=admin_read_headers)
        await c.get("/admin/api/billing/pricing", headers=admin_read_headers)

    assert await db.list_model_price_configs() == []
