"""Tests for RM-23 — ActivityTracker (prometheus_gateway/telemetry.py)."""

from __future__ import annotations

from unittest.mock import patch

from prometheus_gateway.telemetry import ActivityTracker


async def test_touch_then_snapshot_reports_the_entry():
    tracker = ActivityTracker()
    await tracker.touch("client-a", "user-a", "dashboard")

    sessions = await tracker.snapshot()

    assert len(sessions) == 1
    assert sessions[0]["client_id"] == "client-a"
    assert sessions[0]["user_id"] == "user-a"
    assert sessions[0]["connection_type"] == "dashboard"
    assert sessions[0]["last_seen_ago_s"] == 0


async def test_touch_overwrites_previous_entry_for_same_client():
    tracker = ActivityTracker()
    await tracker.touch("client-a", "user-a", "dashboard")
    await tracker.touch("client-a", "user-a", "api")

    sessions = await tracker.snapshot()

    assert len(sessions) == 1
    assert sessions[0]["connection_type"] == "api"


async def test_snapshot_excludes_entries_older_than_15_minutes():
    tracker = ActivityTracker()
    with patch("prometheus_gateway.telemetry.time.time", return_value=1_000.0):
        await tracker.touch("stale-client", "user-a", "api")

    with patch("prometheus_gateway.telemetry.time.time", return_value=1_000.0 + 15 * 60 + 1):
        sessions = await tracker.snapshot()

    assert sessions == []


async def test_snapshot_sorts_most_recently_active_first():
    tracker = ActivityTracker()
    with patch("prometheus_gateway.telemetry.time.time", return_value=1_000.0):
        await tracker.touch("older", "user-a", "api")
    with patch("prometheus_gateway.telemetry.time.time", return_value=1_010.0):
        await tracker.touch("newer", "user-b", "api")
        sessions = await tracker.snapshot()

    assert [s["client_id"] for s in sessions] == ["newer", "older"]
