"""Tests for RM-32 — persisted usage tracking (prometheus_gateway/db.py)."""

from __future__ import annotations

import asyncio
from datetime import date

import pytest

from prometheus_gateway import db, pricing

_DAY = date(2026, 1, 15)
_OTHER_DAY = date(2026, 1, 16)


@pytest.fixture(autouse=True)
async def _fresh_db(tmp_path):
    """Each test gets its own SQLite file, independent of the gateway app fixtures.

    RM-60: record_usage() now prices at write time, so it needs a pricing
    table just like production's create_app() initialises both eagerly —
    an empty table (no pricing_file) is enough for these token-count-only tests.
    """
    db.init_db_engine(f"sqlite+aiosqlite:///{tmp_path}/usage-test.db")
    await db.create_tables(db.get_engine())
    pricing.init_pricing_table(str(tmp_path / "does-not-exist.yaml"))


async def test_record_usage_creates_new_row():
    await db.record_usage("client-a", "model-x", 10, 5, day=_DAY)

    rows = await db.query_usage_day(_DAY)
    assert len(rows) == 1
    assert rows[0].client_id == "client-a"
    assert rows[0].model_id == "model-x"
    assert rows[0].prompt_tokens == 10
    assert rows[0].completion_tokens == 5
    assert rows[0].request_count == 1


async def test_record_usage_increments_existing_row():
    await db.record_usage("client-a", "model-x", 10, 5, day=_DAY)
    await db.record_usage("client-a", "model-x", 3, 2, day=_DAY)

    rows = await db.query_usage_day(_DAY)
    assert len(rows) == 1
    assert rows[0].prompt_tokens == 13
    assert rows[0].completion_tokens == 7
    assert rows[0].request_count == 2


async def test_record_usage_separates_by_model():
    await db.record_usage("client-a", "model-x", 10, 5, day=_DAY)
    await db.record_usage("client-a", "model-y", 1, 1, day=_DAY)

    rows = await db.query_usage_day(_DAY)
    assert {r.model_id for r in rows} == {"model-x", "model-y"}


async def test_record_usage_separates_by_client():
    await db.record_usage("client-a", "model-x", 10, 5, day=_DAY)
    await db.record_usage("client-b", "model-x", 1, 1, day=_DAY)

    rows = await db.query_usage_day(_DAY)
    assert {r.client_id for r in rows} == {"client-a", "client-b"}


async def test_record_usage_separates_by_day():
    await db.record_usage("client-a", "model-x", 10, 5, day=_DAY)
    await db.record_usage("client-a", "model-x", 1, 1, day=_OTHER_DAY)

    assert len(await db.query_usage_day(_DAY)) == 1
    assert len(await db.query_usage_day(_OTHER_DAY)) == 1


async def test_query_usage_day_empty_when_nothing_recorded():
    assert await db.query_usage_day(_DAY) == []


# ── RM-60: write-time cost + append-only usage_events audit trail ───────────


async def test_record_usage_stores_cost_at_write_time(tmp_path):
    """The whole point of RM-60: cost is computed and stored NOW, using
    whatever pricing.yaml says at write time — never recomputed later.
    """
    pricing_file = tmp_path / "pricing.yaml"
    pricing_file.write_text(
        "models:\n  - id: priced-model\n    prompt_price_per_1m: 1.0\n"
        "    completion_price_per_1m: 2.0\n"
    )
    pricing.init_pricing_table(str(pricing_file))

    await db.record_usage("client-a", "priced-model", 1_000_000, 500_000, day=_DAY)

    rows = await db.query_usage_day(_DAY)
    assert rows[0].cost_usd == pytest.approx(2.0)  # $1.00 + $1.00

    # Now change the price and re-init — a real price change + restart.
    pricing_file.write_text(
        "models:\n  - id: priced-model\n    prompt_price_per_1m: 100.0\n"
        "    completion_price_per_1m: 100.0\n"
    )
    pricing.init_pricing_table(str(pricing_file))

    # The already-recorded day's cost must NOT change — this is the bug fix.
    rows_after = await db.query_usage_day(_DAY)
    assert rows_after[0].cost_usd == pytest.approx(2.0)


async def test_record_usage_unpriced_model_stays_null_not_zero():
    await db.record_usage("client-a", "unpriced-model", 100, 50, day=_DAY)

    rows = await db.query_usage_day(_DAY)
    assert rows[0].cost_usd is None


async def test_record_usage_creates_usage_event_row():
    await db.record_usage("client-a", "model-x", 10, 5, day=_DAY)

    events = await db.query_usage_events_range(_DAY, _DAY)
    assert len(events) == 1
    assert events[0].client_id == "client-a"
    assert events[0].model_id == "model-x"
    assert events[0].request_kind == "chat"
    assert events[0].prompt_tokens == 10
    assert events[0].completion_tokens == 5


async def test_record_usage_image_kind():
    await db.record_usage(
        "client-a", "image-model", 0, 0, request_kind="image", image_count=3, day=_DAY
    )

    events = await db.query_usage_events_range(_DAY, _DAY)
    assert len(events) == 1
    assert events[0].request_kind == "image"
    assert events[0].image_count == 3

    rows = await db.query_usage_day(_DAY)
    assert rows[0].cost_usd is None  # no pricing.yaml configured in this fixture


async def test_usage_events_never_mutated_only_appended():
    await db.record_usage("client-a", "model-x", 10, 5, day=_DAY)
    await db.record_usage("client-a", "model-x", 3, 2, day=_DAY)

    events = await db.query_usage_events_range(_DAY, _DAY)
    assert len(events) == 2  # append-only — not merged like usage_daily's rollup

    rows = await db.query_usage_day(_DAY)
    assert rows[0].prompt_tokens == 13  # the rollup, however, IS merged
    assert rows[0].request_count == 2


async def test_query_usage_events_range_filters_by_client_and_day():
    await db.record_usage("client-a", "model-x", 10, 5, day=_DAY)
    await db.record_usage("client-b", "model-x", 1, 1, day=_DAY)
    await db.record_usage("client-a", "model-x", 1, 1, day=_OTHER_DAY)

    all_events = await db.query_usage_events_range(_DAY, _OTHER_DAY)
    assert len(all_events) == 3

    client_a_only = await db.query_usage_events_range(_DAY, _OTHER_DAY, client_id="client-a")
    assert len(client_a_only) == 2
    assert all(e.client_id == "client-a" for e in client_a_only)

    day_only = await db.query_usage_events_range(_DAY, _DAY)
    assert len(day_only) == 2


async def test_query_daily_cost_range_groups_by_day(tmp_path):
    pricing_file = tmp_path / "pricing.yaml"
    pricing_file.write_text(
        "models:\n  - id: priced-model\n    prompt_price_per_1m: 1.0\n"
        "    completion_price_per_1m: 1.0\n"
    )
    pricing.init_pricing_table(str(pricing_file))

    await db.record_usage("client-a", "priced-model", 1_000_000, 0, day=_DAY)
    await db.record_usage("client-a", "priced-model", 1_000_000, 0, day=_DAY)
    await db.record_usage("client-a", "priced-model", 1_000_000, 0, day=_OTHER_DAY)

    rows = await db.query_daily_cost_range(_DAY, _OTHER_DAY)
    by_day = {r["day"]: r for r in rows}
    assert by_day[_DAY]["cost_usd"] == pytest.approx(2.0)
    assert by_day[_DAY]["request_count"] == 2
    assert by_day[_OTHER_DAY]["cost_usd"] == pytest.approx(1.0)


async def test_query_model_cost_range_groups_by_model(tmp_path):
    pricing_file = tmp_path / "pricing.yaml"
    pricing_file.write_text(
        "models:\n"
        "  - id: model-a\n    prompt_price_per_1m: 1.0\n    completion_price_per_1m: 1.0\n"
        "  - id: model-b\n    prompt_price_per_1m: 2.0\n    completion_price_per_1m: 2.0\n"
    )
    pricing.init_pricing_table(str(pricing_file))

    await db.record_usage("client-a", "model-a", 1_000_000, 0, day=_DAY)
    await db.record_usage("client-a", "model-a", 1_000_000, 0, day=_OTHER_DAY)
    await db.record_usage("client-a", "model-b", 1_000_000, 0, day=_DAY)

    rows = await db.query_model_cost_range(_DAY, _OTHER_DAY)
    by_model = {r["model_id"]: r for r in rows}
    assert by_model["model-a"]["cost_usd"] == pytest.approx(2.0)
    assert by_model["model-a"]["request_count"] == 2
    assert by_model["model-b"]["cost_usd"] == pytest.approx(2.0)
    assert by_model["model-b"]["request_count"] == 1


async def test_query_model_cost_range_filters_by_client():
    await db.record_usage("client-a", "model-a", 10, 5, day=_DAY)
    await db.record_usage("client-b", "model-a", 1, 1, day=_DAY)

    rows = await db.query_model_cost_range(_DAY, _DAY, client_id="client-a")
    assert len(rows) == 1
    assert rows[0]["model_id"] == "model-a"


async def test_record_usage_concurrent_writes_no_lost_updates():
    """Regression test for the atomic-upsert fix: many concurrent record_usage()
    calls for the SAME (day, client, model) must not lose any increments — the
    old read-modify-write pattern (guarded only by an in-process asyncio.Lock)
    is gone in favor of a real ON CONFLICT DO UPDATE.
    """
    n = 25
    await asyncio.gather(
        *[db.record_usage("client-a", "model-x", 1, 1, day=_DAY) for _ in range(n)]
    )

    rows = await db.query_usage_day(_DAY)
    assert len(rows) == 1
    assert rows[0].prompt_tokens == n
    assert rows[0].completion_tokens == n
    assert rows[0].request_count == n

    events = await db.query_usage_events_range(_DAY, _DAY)
    assert len(events) == n


# ── RM-60: billing settings + currency rates ─────────────────────────────────


async def test_client_billing_settings_upsert_and_get():
    assert await db.get_client_billing_settings("client-a") is None

    row = await db.upsert_client_billing_settings(
        "client-a",
        monthly_spend_cap_usd=10.0,
        alert_thresholds_percent="50,90",
        tax_rate_percent=18.0,
        preferred_currency="PEN",
    )
    assert row.monthly_spend_cap_usd == 10.0

    fetched = await db.get_client_billing_settings("client-a")
    assert fetched is not None
    assert fetched.tax_rate_percent == 18.0
    assert fetched.preferred_currency == "PEN"

    # Upsert again — updates in place, doesn't create a second row.
    await db.upsert_client_billing_settings(
        "client-a",
        monthly_spend_cap_usd=20.0,
        alert_thresholds_percent=None,
        tax_rate_percent=0.0,
        preferred_currency="USD",
    )
    fetched_again = await db.get_client_billing_settings("client-a")
    assert fetched_again.monthly_spend_cap_usd == 20.0


async def test_list_client_billing_settings_with_cap_excludes_uncapped():
    await db.upsert_client_billing_settings(
        "capped",
        monthly_spend_cap_usd=5.0,
        alert_thresholds_percent=None,
        tax_rate_percent=0.0,
        preferred_currency="USD",
    )
    await db.upsert_client_billing_settings(
        "uncapped",
        monthly_spend_cap_usd=None,
        alert_thresholds_percent=None,
        tax_rate_percent=0.0,
        preferred_currency="USD",
    )

    capped_clients = await db.list_client_billing_settings_with_cap()
    assert {r.client_id for r in capped_clients} == {"capped"}


async def test_currency_rate_upsert_and_list():
    await db.upsert_currency_rate("PEN", 3.75)
    await db.upsert_currency_rate("EUR", 0.92)

    rates = await db.list_currency_rates()
    assert {r.currency_code: r.units_per_usd for r in rates} == {"PEN": 3.75, "EUR": 0.92}

    await db.upsert_currency_rate("PEN", 3.80)
    rates_after = await db.list_currency_rates()
    assert {r.currency_code: r.units_per_usd for r in rates_after}["PEN"] == 3.80


# ── RM-60 follow-up: admin-configurable model pricing (ModelPriceConfig) ────


async def test_model_price_config_upsert_and_list():
    assert await db.list_model_price_configs() == []

    row = await db.upsert_model_price_config(
        "small-model", prompt_price_per_1m=1.0, completion_price_per_1m=2.0, image_price=None
    )
    assert row.model_id == "small-model"
    assert row.completion_price_per_1m == 2.0

    rows = await db.list_model_price_configs()
    assert len(rows) == 1

    # Upsert again — updates in place, not a second row.
    await db.upsert_model_price_config(
        "small-model", prompt_price_per_1m=5.0, completion_price_per_1m=5.0, image_price=None
    )
    rows_after = await db.list_model_price_configs()
    assert len(rows_after) == 1
    assert rows_after[0].prompt_price_per_1m == 5.0


async def test_model_price_config_delete():
    await db.upsert_model_price_config(
        "small-model", prompt_price_per_1m=1.0, completion_price_per_1m=2.0, image_price=None
    )

    assert await db.delete_model_price_config("small-model") is True
    assert await db.list_model_price_configs() == []
    assert await db.delete_model_price_config("small-model") is False
