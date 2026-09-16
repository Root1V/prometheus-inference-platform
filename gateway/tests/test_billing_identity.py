"""PRM-113 — billing survives a rename.

Usage rows were keyed on the model's *slug*, which RM-70 lets an operator set
once. Naming a model therefore did two things silently: it split the billing
history into two buckets under two names, and it made the model miss its
pricing.yaml entry, so from that moment it billed nothing while nothing failed.

Both were found in this deployment's own data — qwen3-0.6b (85 rows) sitting
beside qwen3-0-6b-iq4-nl-local-2 (37), one model in two piles.
"""

from __future__ import annotations

import pathlib
import tempfile
from datetime import datetime, timezone

import pytest

from prometheus_gateway import db
from prometheus_gateway.pricing import init_pricing_table

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
async def _fresh_db(tmp_path):
    """Own database per test, like test_usage_db's — record_usage() prices at
    write time, so it needs a pricing table initialised too."""
    db.init_db_engine(f"sqlite+aiosqlite:///{tmp_path}/billing-identity.db")
    await db.create_tables(db.get_engine())
    init_pricing_table(str(tmp_path / "no-such-file.yaml"))


def _priced(name: str):
    f = pathlib.Path(tempfile.mkdtemp()) / "pricing.yaml"
    f.write_text(
        f"""
models:
  - id: {name}
    prompt_price_per_1m: 1.0
    completion_price_per_1m: 2.0
"""
    )
    return init_pricing_table(str(f))


# ── The price must not depend on which name you ask by ─────────────────────


async def test_a_price_is_found_under_either_name():
    """The failure this fixes: a table written against the old name stopped
    matching the moment the model was renamed, and the row silently priced to
    nothing."""
    table = _priced("catalog-id-1")
    assert table.estimate_cost_usd("catalog-id-1", 1_000_000, 0) == 1.0
    # asked by the id, priced under the slug
    table = _priced("friendly-name")
    assert table.estimate_cost_usd("catalog-id-1", 1_000_000, 0, model_slug="friendly-name") == 1.0


async def test_an_unpriced_model_is_still_unpriced():
    """None means "no price", not "free" — the lookup widening must not turn a
    missing price into a zero."""
    table = _priced("something-else")
    assert table.estimate_cost_usd("catalog-id-1", 1_000_000, 0, model_slug="friendly") is None


# ── The history must not split ─────────────────────────────────────────────


async def test_rows_written_before_and_after_a_rename_share_one_key():
    """The whole point. Two requests for the same model, the slug changing in
    between, must aggregate as one model."""
    today = datetime.now(timezone.utc).date()

    await db.record_usage("c1", "catalog-id-1", 10, 5, model_slug="old-name")
    await db.record_usage("c1", "catalog-id-1", 20, 7, model_slug="new-name")

    events = await db.query_usage_events_range(today, today)
    assert {e.model_id for e in events} == {"catalog-id-1"}, "one model, one key"
    # and the label each row carried at the time is preserved, because an
    # invoice should say what the thing was called then.
    assert sorted(e.model_slug for e in events) == ["new-name", "old-name"]


async def test_the_daily_rollup_keeps_one_row_across_a_rename():
    today = datetime.now(timezone.utc).date()

    await db.record_usage("c2", "catalog-id-2", 10, 5, model_slug="before")
    await db.record_usage("c2", "catalog-id-2", 20, 7, model_slug="after")

    rows = await db.query_usage_day(today)
    mine = [r for r in rows if r.model_id == "catalog-id-2"]
    assert len(mine) == 1, "a rename must not open a second daily bucket"
    assert mine[0].prompt_tokens == 30
    # the rollup shows the name in force now
    assert mine[0].model_slug == "after"


async def test_a_caller_with_only_one_identifier_still_writes_something_true():
    """Nothing should be forced to invent a display name."""
    today = datetime.now(timezone.utc).date()
    await db.record_usage("c3", "just-an-id", 1, 1)
    events = [e for e in await db.query_usage_events_range(today, today) if e.client_id == "c3"]
    assert events[0].model_slug == "just-an-id"


# ── Counting what depends on a name ────────────────────────────────────────


async def test_usage_rows_are_found_under_either_name():
    """Feeds the rename check: rows written before PRM-113 carry the slug in
    model_id, rows after carry it in model_slug. Both must count."""
    await db.record_usage("c4", "cat-4", 3, 3, model_slug="pretty-4")

    assert await db.count_usage_rows_for_model("cat-4") >= 1
    assert await db.count_usage_rows_for_model("nope", "pretty-4") >= 1
    assert await db.count_usage_rows_for_model("unrelated", "also-unrelated") == 0
