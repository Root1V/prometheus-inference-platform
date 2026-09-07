"""Tests for RM-60 — BudgetTracker (prometheus_gateway/budget.py).

Uses fakeredis, same pattern as test_rate_limiting.py's fake_redis fixture.
"""

from __future__ import annotations

import asyncio

import fakeredis.aioredis as fakeredis
import pytest

from prometheus_gateway.budget import BudgetTracker, parse_thresholds


@pytest.fixture
def fake_redis():
    return fakeredis.FakeRedis()


@pytest.fixture
def tracker(fake_redis):
    return BudgetTracker(fake_redis)


# ── parse_thresholds ─────────────────────────────────────────────────────────


def test_parse_thresholds_uses_override_when_set():
    assert parse_thresholds("50,90", "50,80,100") == [50, 90]


def test_parse_thresholds_falls_back_to_default_when_unset():
    assert parse_thresholds(None, "50,80,100") == [50, 80, 100]


def test_parse_thresholds_dedupes_and_sorts():
    assert parse_thresholds("100, 50, 50, 80", "") == [50, 80, 100]


def test_parse_thresholds_ignores_garbage_entries():
    assert parse_thresholds("50,,abc,90", "") == [50, 90]


# ── reserve / settle ─────────────────────────────────────────────────────────


async def test_reserve_allows_within_cap(tracker):
    result = await tracker.reserve("client-a", 2.0, cap_usd=10.0, alert_thresholds_percent=[])
    assert result.allowed is True
    assert result.total_spend_usd == pytest.approx(2.0)


async def test_reserve_denies_and_rolls_back_over_cap(tracker):
    await tracker.reserve("client-a", 8.0, cap_usd=10.0, alert_thresholds_percent=[])
    result = await tracker.reserve("client-a", 5.0, cap_usd=10.0, alert_thresholds_percent=[])

    assert result.allowed is False
    # Rolled back — spend stays at 8.0, not 13.0.
    assert await tracker.get_spend("client-a") == pytest.approx(8.0)


async def test_settle_corrects_reservation_down(tracker):
    reservation = await tracker.reserve("client-a", 5.0, cap_usd=10.0, alert_thresholds_percent=[])
    await tracker.settle(
        "client-a", reservation.reserved_usd, 1.0, cap_usd=10.0, alert_thresholds_percent=[]
    )

    assert await tracker.get_spend("client-a") == pytest.approx(1.0)


async def test_settle_corrects_reservation_up(tracker):
    reservation = await tracker.reserve("client-a", 1.0, cap_usd=10.0, alert_thresholds_percent=[])
    await tracker.settle(
        "client-a", reservation.reserved_usd, 3.0, cap_usd=10.0, alert_thresholds_percent=[]
    )

    assert await tracker.get_spend("client-a") == pytest.approx(3.0)


async def test_concurrent_reserves_near_cap_boundary_never_overspend(tracker):
    """The actual regression test for the atomic-pipeline design: many
    concurrent reserve() calls near the cap must never let combined spend
    exceed the cap, mirroring rate_limiter.check_and_increment_rpm's
    atomic-pipeline guarantee (never check_tpm_budget's read-then-write gap).
    """
    cap = 10.0
    results = await asyncio.gather(
        *[
            tracker.reserve("client-a", 1.0, cap_usd=cap, alert_thresholds_percent=[])
            for _ in range(20)
        ]
    )

    allowed_count = sum(1 for r in results if r.allowed)
    assert allowed_count == 10  # exactly fills the $10 cap at $1 each
    assert await tracker.get_spend("client-a") == pytest.approx(10.0)


# ── alert thresholds ─────────────────────────────────────────────────────────


async def test_threshold_crossed_once_reported_once(tracker):
    result = await tracker.reserve("client-a", 5.0, cap_usd=10.0, alert_thresholds_percent=[50, 80])
    assert result.crossed_thresholds == [50]

    # Crossing 50% again must not re-report it.
    result2 = await tracker.reserve(
        "client-a", 0.5, cap_usd=10.0, alert_thresholds_percent=[50, 80]
    )
    assert result2.crossed_thresholds == []


async def test_multiple_thresholds_crossed_in_one_reserve(tracker):
    result = await tracker.reserve("client-a", 9.0, cap_usd=10.0, alert_thresholds_percent=[50, 80])
    assert result.crossed_thresholds == [50, 80]


async def test_no_cap_never_crosses_thresholds(tracker):
    result = await tracker.reserve("client-a", 100.0, cap_usd=None, alert_thresholds_percent=[50])
    assert result.allowed is True
    assert result.crossed_thresholds == []


async def test_get_notified_thresholds_reflects_crossed_state(tracker):
    await tracker.reserve("client-a", 9.0, cap_usd=10.0, alert_thresholds_percent=[50, 80])
    assert await tracker.get_notified_thresholds("client-a") == [50, 80]
