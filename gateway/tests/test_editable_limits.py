"""PRM-232: the eleven limits become editable.

Implements: docs/roadmap.md — PRM-232.

PRM-224 through PRM-228 added eleven dimensions to Settings and could not
persist any of them, because `main.py` reads `RATE_LIMIT_FIELDS` off a
`RateLimitConfig` row with `getattr` and the table had six columns. PRM-182
wrote that constraint down after nearly shipping the AttributeError it causes
— on exactly those deployments that have ever saved limits from the dashboard,
which is the worst place for it to appear.

This is the migration, and the tests that matter are the two that would have
caught the bug PRM-182 feared: the field list against the real table, and a PUT
of all seventeen landing on a row that a restart can read back.
"""

from __future__ import annotations

import fakeredis.aioredis as fakeredis
import pytest
from httpx import ASGITransport, AsyncClient

from prometheus_gateway import db, rate_limits
from prometheus_gateway.main import create_app
from prometheus_gateway.models.registry import ModelRegistry
from tests.conftest import dashboard_settings, make_token


@pytest.fixture
def fake_redis():
    return fakeredis.FakeRedis()


@pytest.fixture
def registry():
    r = ModelRegistry.__new__(ModelRegistry)
    r._models = {}
    return r


@pytest.fixture
def settings(rsa_keys, tmp_path):
    key_file = tmp_path / "public.pem"
    key_file.write_text(rsa_keys["public"])
    return dashboard_settings(key_file)


@pytest.fixture
def app(settings, registry, fake_redis):
    return create_app(settings=settings, registry=registry, redis_client=fake_redis)


@pytest.fixture
def write_headers(rsa_keys):
    return {
        "Authorization": "Bearer "
        + make_token(
            rsa_keys["private"],
            scope="admin:read admin:write",
            sub="admin-user",
            azp="admin-client",
        )
    }


# Every dimension the page shows, with a distinct value so a field landing in
# the wrong column is visible rather than coincidentally right.
ALL_SEVENTEEN = {
    "rate_limit_rpm_platform": 5_000,
    "rate_limit_tpm_platform": 2_000_000,
    "rate_limit_rpm_client": 400,
    "rate_limit_tpm_client": 300_000,
    "rate_limit_tpm_input": 200_000,
    "rate_limit_tpm_output": 100_000,
    "rate_limit_rpd": 50_000,
    "rate_limit_tpd": 20_000_000,
    "rate_limit_ipm": 25,
    "rate_limit_rpm": 120,
    "rate_limit_tpm": 90_000,
    "rate_limit_rpm_chat_completions": 30,
    "rate_limit_tpm_chat_completions": 80_000,
    "rate_limit_rpm_predict": 600,
    "rate_limit_tpm_predict": 70_000,
    "rate_limit_rpm_admin": 600,
    "rate_limit_tpm_admin": 60_000,
}


def test_the_field_list_covers_every_dimension_and_every_name_is_a_column():
    """The bug PRM-182 named, now impossible in both directions.

    A name in the list that the table lacks is an AttributeError at startup; a
    dimension the page shows that the list lacks is a control that silently
    does not save. Before this change the second was true of eleven.
    """
    columns = {c.name for c in db.RateLimitConfig.__table__.columns}
    assert set(rate_limits.RATE_LIMIT_FIELDS) <= columns
    assert set(ALL_SEVENTEEN) == set(rate_limits.RATE_LIMIT_FIELDS)


async def test_all_seventeen_apply_live_and_persist(app, write_headers):
    """One PUT, seventeen dimensions, and both halves checked: the Settings
    object the middleware re-reads per request, and the row a restart reads."""
    await db.create_tables(db.get_engine())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.put("/admin/api/limits", json=ALL_SEVENTEEN, headers=write_headers)

    assert resp.status_code == 200, resp.text
    assert resp.json()["is_overridden"] is True

    settings = app.state.settings
    row = await db.get_rate_limit_config()
    assert row is not None
    for field, value in ALL_SEVENTEEN.items():
        assert getattr(settings, field) == value, f"{field} not applied to live Settings"
        assert getattr(row, field) == value, f"{field} not persisted"


async def test_a_saved_row_survives_a_restart_for_all_seventeen(
    app, write_headers, settings, registry, fake_redis
):
    """The startup path PRM-182 was afraid of, exercised rather than reasoned
    about: a second app reads the saved row through the same `getattr` loop."""
    await db.create_tables(db.get_engine())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        await c.put("/admin/api/limits", json=ALL_SEVENTEEN, headers=write_headers)

    row = await db.get_rate_limit_config()
    assert row is not None
    restarted = dashboard_settings(settings.jwt_public_key_file)
    rate_limits.apply_limits(restarted, {f: getattr(row, f) for f in rate_limits.RATE_LIMIT_FIELDS})
    for field, value in ALL_SEVENTEEN.items():
        assert getattr(restarted, field) == value


async def test_one_dimension_can_be_edited_without_resending_sixteen(app, write_headers):
    """A field absent from the body is left alone; a field present and null is
    cleared. The form posts everything, but an API caller raising one limit
    should not have to know the other sixteen — and would otherwise clear them
    by omission, which is a dangerous default for a rate limiter."""
    await db.create_tables(db.get_engine())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        await c.put("/admin/api/limits", json=ALL_SEVENTEEN, headers=write_headers)
        resp = await c.put(
            "/admin/api/limits",
            json={"rate_limit_rpm": 120, "rate_limit_tpm": 90_000, "rate_limit_ipm": 99},
            headers=write_headers,
        )

    assert resp.status_code == 200
    row = await db.get_rate_limit_config()
    assert row is not None
    assert row.rate_limit_ipm == 99
    assert row.rate_limit_rpd == ALL_SEVENTEEN["rate_limit_rpd"], "an absent field was cleared"


async def test_a_dimension_can_be_cleared_back_to_its_default(app, write_headers):
    """Explicit null is the other edit, and it has to stay expressible: clearing
    `rate_limit_rpm_client` returns the consumer layer to PRM-227's derived
    ceiling rather than pinning it at whatever was typed once."""
    await db.create_tables(db.get_engine())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        await c.put("/admin/api/limits", json=ALL_SEVENTEEN, headers=write_headers)
        resp = await c.put(
            "/admin/api/limits",
            json={
                "rate_limit_rpm": 120,
                "rate_limit_tpm": 90_000,
                "rate_limit_rpm_client": None,
            },
            headers=write_headers,
        )

    assert resp.status_code == 200
    assert app.state.settings.rate_limit_rpm_client is None
    client_layer = next(layer for layer in resp.json()["layers"] if layer["layer"] == "client")
    rpm_client = next(f for f in client_layer["fields"] if f["field"] == "rate_limit_rpm_client")
    assert rpm_client["source"] == "derived"
    assert rpm_client["value"] == 120 * 6


async def test_reset_restores_every_env_default(app, write_headers, settings):
    """Saving makes the dashboard the source for all seventeen — and Reset is
    what makes that reversible. The snapshot is taken in `create_app` before
    any row can be applied, which is why it still holds the `.env` values for
    dimensions nobody has ever edited."""
    env_rpm = settings.rate_limit_rpm
    await db.create_tables(db.get_engine())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        await c.put("/admin/api/limits", json=ALL_SEVENTEEN, headers=write_headers)
        assert app.state.settings.rate_limit_ipm == 25
        resp = await c.delete("/admin/api/limits", headers=write_headers)

    assert resp.status_code == 200
    assert resp.json()["is_overridden"] is False
    assert app.state.settings.rate_limit_rpm == env_rpm
    assert app.state.settings.rate_limit_ipm is None
    assert set(app.state.rate_limit_env_defaults) == set(rate_limits.RATE_LIMIT_FIELDS)


async def test_every_field_is_now_marked_editable(app, write_headers):
    """The `.env` chip on the cards meant "live and enforced, and this page
    cannot save it". Eleven fields carried it; none should now."""
    await db.create_tables(db.get_engine())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        body = (await c.get("/admin/api/limits", headers=write_headers)).json()

    not_editable = [
        f["field"] for layer in body["layers"] for f in layer["fields"] if not f["editable"]
    ]
    assert not_editable == []


async def test_the_admin_floor_still_guards_the_way_back_in(app, write_headers):
    """Seventeen fields and one of them can still lock the operator out, so the
    guard has to survive the rewrite of the handler that holds it."""
    await db.create_tables(db.get_engine())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.put(
            "/admin/api/limits",
            json={"rate_limit_rpm": 120, "rate_limit_tpm": 90_000, "rate_limit_rpm_admin": 5},
            headers=write_headers,
        )

    assert resp.status_code == 400
    assert "RPM floor" in resp.json()["detail"]


async def test_an_unknown_field_cannot_reach_the_row(app, write_headers):
    """The typo-safety seventeen keyword arguments used to buy. `setattr` over
    a dict would otherwise put a misspelt name on the ORM object and commit a
    row missing the value the operator thought they saved."""
    await db.create_tables(db.get_engine())
    with pytest.raises(ValueError, match="not columns on rate_limit_config"):
        await db.upsert_rate_limit_config({"rate_limit_rpm": 1, "rate_limit_rpmm": 2})
