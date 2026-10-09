"""PRM-246: the dashboard answers what to do.

Implements: docs/roadmap.md — PRM-246.

What it replaced read `metrics_store`, which is process memory, so after a
restart the platform's headline was `0 active · 5 total` requests and a p95
equal to its p99. Beside that sat counts of things that exist — nodes,
instances, users — which answer "is the fleet where I left it" and not one
question anybody acts on.

The tests here are about the two properties that make the difference: the
figures come from the usage rows and so survive a restart, and what needs
attention is computed rather than left for the reader to spot.
"""

from __future__ import annotations

import datetime as _dt

import fakeredis.aioredis as fakeredis
import pytest
from httpx import ASGITransport, AsyncClient

from prometheus_gateway import db
from prometheus_gateway.main import create_app
from prometheus_gateway.models.registry import ModelRegistry
from tests.conftest import dashboard_settings, make_token


@pytest.fixture
def fake_redis():
    return fakeredis.FakeRedis()


@pytest.fixture
def app(rsa_keys, tmp_path, fake_redis):
    key_file = tmp_path / "public.pem"
    key_file.write_text(rsa_keys["public"])
    registry = ModelRegistry.__new__(ModelRegistry)
    registry._models = {}
    return create_app(
        settings=dashboard_settings(key_file), registry=registry, redis_client=fake_redis
    )


@pytest.fixture
def admin(rsa_keys):
    return {
        "Authorization": "Bearer "
        + make_token(rsa_keys["private"], scope="admin:read", sub="a", azp="a")
    }


def _utc_today() -> _dt.date:
    return _dt.datetime.now(_dt.timezone.utc).date()


async def test_the_figures_come_from_the_usage_rows_not_process_memory(app, admin):
    """The property the old dashboard lacked: a restart does not change them.

    Written straight to the table, with no request having gone through this
    process at all — which is exactly the state after a deploy.
    """
    await db.create_tables(db.get_engine())
    today = _utc_today()
    yesterday = today - _dt.timedelta(days=1)
    for i in range(5):
        await db.record_usage("c1", "m", 10, 5, model_slug="m", request_id=f"t{i}", day=today)
    for i in range(2):
        await db.record_usage("c1", "m", 10, 5, model_slug="m", request_id=f"y{i}", day=yesterday)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        body = (await c.get("/admin/api/overview", headers=admin)).json()

    assert body["signals"]["requests"]["today"] == 5
    assert body["signals"]["requests"]["yesterday"] == 2
    assert body["signals"]["requests"]["percent"] == 150.0
    assert body["signals"]["tokens"]["today"] == 75


async def test_a_first_day_of_traffic_is_not_a_hundred_percent_rise(app, admin):
    """`percent: null` is a state, and the page prints it as "nothing to
    compare with". Zero would read as flat and any number would be invented."""
    await db.create_tables(db.get_engine())
    await db.record_usage("c2", "m", 1, 1, model_slug="m", request_id="only", day=_utc_today())

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        body = (await c.get("/admin/api/overview", headers=admin)).json()

    assert body["signals"]["requests"]["percent"] is None


async def test_an_unpriced_request_is_something_to_act_on(app, admin):
    """A model serving traffic that bills nothing is how a month goes by
    before anyone notices. It is a row in `needs attention`, not a number
    somebody has to spot."""
    await db.create_tables(db.get_engine())
    await db.record_usage(
        "c3", "no-price-model", 10, 5, model_slug="no-price-model", request_id="u1"
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        body = (await c.get("/admin/api/overview", headers=admin)).json()

    kinds = [a["kind"] for a in body["attention"]]
    assert "unpriced" in kinds


async def test_a_consumer_near_its_ceiling_is_something_to_act_on(app, admin, fake_redis):
    """The signal the practice says gateways undersell, and the one this
    platform can answer better than most since PRM-230."""
    import time as _time

    await db.create_tables(db.get_engine())
    bucket = int(_time.time() // 60)
    # The endpoint-layer ceiling is 60 RPM by default; 58 is 96.7% of it.
    await fake_redis.set(f"prometheus:rl:rpm:hot-client:chat_completions:{bucket}", 58)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        body = (await c.get("/admin/api/overview", headers=admin)).json()

    ceilings = [a for a in body["attention"] if a["kind"] == "ceiling"]
    assert ceilings, "a consumer at 96% of its ceiling was not surfaced"
    assert "hot-client" in ceilings[0]["what"]
    assert ceilings[0]["severity"] == "warning"


async def test_a_quiet_platform_says_so_rather_than_showing_an_empty_list(app, admin):
    """An empty attention list and a healthy platform are the same state, and
    the page says which one it is."""
    await db.create_tables(db.get_engine())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        body = (await c.get("/admin/api/overview", headers=admin)).json()

    assert body["attention"] == []
    assert body["series"] == [] or all(r["request_count"] == 0 for r in body["series"])


async def test_the_series_covers_the_window_and_is_ordered(app, admin):
    await db.create_tables(db.get_engine())
    today = _utc_today()
    for offset in (0, 3, 10):
        await db.record_usage(
            "c4",
            "m",
            1,
            1,
            model_slug="m",
            request_id=f"d{offset}",
            day=today - _dt.timedelta(days=offset),
        )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        body = (await c.get("/admin/api/overview", headers=admin)).json()

    days = [row["day"] for row in body["series"]]
    assert days == sorted(days)
    assert len(days) == 3, "only days with traffic are rows; the chart fills the gaps"


async def test_overview_requires_admin_read(app, rsa_keys):
    caller = {
        "Authorization": "Bearer "
        + make_token(rsa_keys["private"], scope="inference:read", sub="x", azp="x")
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        assert (await c.get("/admin/api/overview", headers=caller)).status_code == 403


async def test_the_model_table_shows_the_busiest_not_the_first_alphabetically(app, admin):
    """`query_model_cost_range` returns rows in group-by order, so slicing
    eight off the front gave eight alphabetical models under a heading that
    promises the ones that matter. Measured against the live deployment: the
    model with 4,535 requests was not in the list."""
    await db.create_tables(db.get_engine())
    for i in range(3):
        await db.record_usage("c", "aaa-quiet", 1, 1, model_slug="aaa-quiet", request_id=f"q{i}")
    for i in range(50):
        await db.record_usage("c", "zzz-busy", 1, 1, model_slug="zzz-busy", request_id=f"b{i}")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        body = (await c.get("/admin/api/overview", headers=admin)).json()

    assert body["models"][0]["model_id"] == "zzz-busy"


async def test_a_measured_model_carries_its_percentiles(app, admin):
    """The join that came out empty against the deployment: cost rows are
    keyed by `model_id` and the latency query grouped by `model_slug`."""
    await db.create_tables(db.get_engine())
    for i in range(4):
        await db.record_usage(
            "c",
            "catalog-id",
            1,
            1,
            model_slug="answered-to",
            duration_ms=30 + i,
            request_id=f"m{i}",
        )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        body = (await c.get("/admin/api/overview", headers=admin)).json()

    model = next(m for m in body["models"] if m["model_id"] == "catalog-id")
    assert model["latency"] is not None, "cost and latency were keyed differently again"
    assert model["latency"]["count"] == 4
