"""Tests for RM-32 — persisted usage tracking (prometheus_gateway/db.py)."""

from __future__ import annotations

from datetime import date

import pytest

from prometheus_gateway import db

_DAY = date(2026, 1, 15)
_OTHER_DAY = date(2026, 1, 16)


@pytest.fixture(autouse=True)
async def _fresh_db(tmp_path):
    """Each test gets its own SQLite file, independent of the gateway app fixtures."""
    db.init_db_engine(f"sqlite+aiosqlite:///{tmp_path}/usage-test.db")
    await db.create_tables(db.get_engine())


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
