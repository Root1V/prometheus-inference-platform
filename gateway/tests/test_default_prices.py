"""PRM-120 — every catalogued model starts with a price.

A model with traffic and no price records `cost_usd = NULL`, which is honest
but useless: the request is unbilled *and* never budget-checked, and nothing
anywhere says so. Found by reading the dashboard — 79 requests the invoice
could not account for.

The base price is flat per modality on purpose. Deriving one from the model's
size and the node's cost was tried first and dropped after measuring it against
this fleet: tokens/s x GB, which would have to be roughly constant, came out
131 / 396 / 1295 for qwen3-0.6b, qwen3-8b-q6 and gpt-oss-20b. A figure wrong by
10x that looks measured is worse than a flat one that admits what it is.
"""

from __future__ import annotations

import pytest

from prometheus_gateway import db, pricing


@pytest.fixture(autouse=True)
async def _fresh_db(tmp_path):
    """Own SQLite file per test, and an empty pricing table — the same shape
    test_usage_db.py uses."""
    db.init_db_engine(f"sqlite+aiosqlite:///{tmp_path}/prices-test.db")
    await db.create_tables(db.get_engine())
    pricing.init_pricing_table(str(tmp_path / "does-not-exist.yaml"))


# ── The base prices themselves ─────────────────────────────────────────────


@pytest.mark.parametrize("modality", ["text", "vision", "embedding", "rerank", "image"])
def test_every_servable_modality_has_a_base_price(modality):
    """The registry's MODALITIES, minus nothing. A modality without an entry
    here is a model that silently starts unpriced again."""
    assert pricing.default_price_for(modality) is not None


def test_token_modalities_price_both_sides():
    """Half a price is treated as unpriced everywhere else in this system, so a
    base price that set only the prompt side would seed a model that still
    bills nothing — the exact bug this closes, wearing a price."""
    for modality in ("text", "vision", "embedding", "rerank"):
        base = pricing.default_price_for(modality)
        assert base.prompt_price_per_1m is not None
        assert base.completion_price_per_1m is not None


def test_images_price_per_image_not_per_token():
    base = pricing.default_price_for("image")
    assert base.image_price is not None
    assert base.prompt_price_per_1m is None


def test_an_unknown_modality_gets_nothing():
    """Better unpriced than invented: we have no published reference for a
    modality we have not shipped."""
    assert pricing.default_price_for("telepathy") is None


# ── Seeding ────────────────────────────────────────────────────────────────


async def test_a_new_model_is_seeded_and_marked_as_a_default():
    seeded = await db.seed_default_prices([("brand-new", "text")])

    assert seeded == ["brand-new"]
    row = {r.model_id: r for r in await db.list_model_price_configs()}["brand-new"]
    assert row.prompt_price_per_1m == pricing.default_price_for("text").prompt_price_per_1m
    assert row.is_default is True, "a seeded price must not look like somebody's decision"


async def test_an_operators_price_is_never_overwritten():
    """The seeding runs on every catalog sync, so this is the guarantee that
    matters: a price somebody set stays set, sync after sync."""
    await db.upsert_model_price_config(
        "chosen", prompt_price_per_1m=99.0, completion_price_per_1m=99.0, image_price=None
    )

    for _ in range(3):
        assert await db.seed_default_prices([("chosen", "text")]) == []

    row = {r.model_id: r for r in await db.list_model_price_configs()}["chosen"]
    assert row.prompt_price_per_1m == 99.0
    assert row.is_default is False


async def test_saving_a_seeded_price_claims_it():
    """Pressing Save on the base figure unchanged still means a person looked
    at it and decided it was right — which is exactly what the flag records."""
    await db.seed_default_prices([("reviewed", "text")])
    base = pricing.default_price_for("text")

    await db.upsert_model_price_config(
        "reviewed",
        prompt_price_per_1m=base.prompt_price_per_1m,
        completion_price_per_1m=base.completion_price_per_1m,
        image_price=None,
    )

    row = {r.model_id: r for r in await db.list_model_price_configs()}["reviewed"]
    assert row.is_default is False


async def test_seeding_is_idempotent_across_syncs():
    catalog = [("a", "text"), ("b", "embedding"), ("c", "image")]
    assert sorted(await db.seed_default_prices(catalog)) == ["a", "b", "c"]
    assert await db.seed_default_prices(catalog) == []
    assert len(await db.list_model_price_configs()) == 3


async def test_a_modality_with_no_base_price_is_skipped_not_crashed():
    seeded = await db.seed_default_prices([("weird", "telepathy"), ("ok", "text")])
    assert seeded == ["ok"]


# ── The point of the whole thing ───────────────────────────────────────────


async def test_a_seeded_model_actually_bills():
    """The end the feature exists for: a brand-new model records a real cost on
    its first request instead of NULL."""
    await db.seed_default_prices([("fresh-model", "text")])
    table = pricing.get_pricing_table()
    base = pricing.default_price_for("text")
    table.set_price(
        "fresh-model",
        prompt_price_per_1m=base.prompt_price_per_1m,
        completion_price_per_1m=base.completion_price_per_1m,
        image_price=None,
    )

    await db.record_usage("client-z", "fresh-model", 1_000_000, 1_000_000)

    from datetime import date

    events = await db.query_usage_events_range(date.today(), date.today())
    assert len(events) == 1
    assert events[0].cost_usd == pytest.approx(
        base.prompt_price_per_1m + base.completion_price_per_1m
    )
