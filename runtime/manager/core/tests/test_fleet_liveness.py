"""Two facts, not one flag — PRM-151.

`is_active` used to be a single stored boolean written both by `/deactivate` (an
operator cordoning a node for maintenance) and by the connectivity probe. So the
next `/check` erased the cordon and the node silently returned to rotation. That
was verified live before the fix.

Kubernetes keeps the pair apart and nothing observed writes the operator's half:
`spec.unschedulable` is intent, `status.conditions[Ready]` is observation. These
tests pin that separation and the TTL that replaces the boolean.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from prometheus_manager_core.fleet import (
    DEFAULT_LIVENESS_TTL_S,
    FleetRegistry,
    Node,
    now_iso,
)


@pytest.fixture
def fleet(tmp_path: Path):
    reg = FleetRegistry(tmp_path / "fleet.db")
    yield reg
    reg.close()


def _node(**kw) -> Node:
    return Node(name=kw.pop("name", "n1"), manager_url="http://n1:8090", node_type="mac", **kw)


# ── Liveness has an age, where a boolean had none ─────────────────────────────


def test_a_node_never_seen_is_not_routable():
    assert _node().is_active() is False


def test_a_node_seen_now_is_routable():
    assert _node(last_seen_at=now_iso()).is_active() is True


def test_a_sighting_older_than_the_ttl_is_not_routable():
    old = (datetime.now(UTC) - timedelta(seconds=DEFAULT_LIVENESS_TTL_S + 30)).isoformat()
    assert _node(last_seen_at=old).is_active() is False


def test_one_missed_sweep_is_not_an_outage():
    """The TTL is several sweep intervals, so a single miss keeps the node up."""
    recent = (datetime.now(UTC) - timedelta(seconds=20)).isoformat()
    assert _node(last_seen_at=recent).is_active() is True


def test_an_unparseable_timestamp_is_not_routable():
    """A value nobody can read must not become "recently seen"."""
    assert _node(last_seen_at="last tuesday").is_active() is False


# ── The cordon is the operator's, and nothing observed may lift it ────────────


def test_a_cordoned_node_is_not_routable_even_while_answering(fleet):
    node = fleet.add(_node(last_seen_at=now_iso()))
    assert node.is_active() is True

    node.enabled = False
    fleet.update(node)
    assert fleet.get(node.id).is_active() is False


def test_a_sighting_does_not_lift_a_cordon(fleet):
    """The bug, asserted directly: this is what `/check` used to undo."""
    node = fleet.add(_node(enabled=False, last_seen_at=None))
    fleet.mark_seen(node.id)

    after = fleet.get(node.id)
    assert after.last_seen_at is not None, "the sighting was not recorded"
    assert after.enabled is False, "a probe overruled the operator's cordon"
    assert after.is_active() is False


def test_a_cordoned_node_still_records_sightings(fleet):
    """Deliberate: an operator needs to see that a cordoned node is healthy
    before lifting the cordon, so liveness keeps being observed."""
    node = fleet.add(_node(enabled=False))
    fleet.mark_seen(node.id)
    assert fleet.get(node.id).seen_within(DEFAULT_LIVENESS_TTL_S) is True


def test_mark_seen_reports_whether_a_row_existed(fleet):
    assert fleet.mark_seen("no-such-node") is False
    node = fleet.add(_node())
    assert fleet.mark_seen(node.id) is True


# ── The wire shape reports both facts, not only the verdict ───────────────────


def test_the_dict_carries_the_verdict_and_both_facts_it_comes_from(fleet):
    node = fleet.add(_node(last_seen_at=now_iso()))
    d = node.to_dict()
    assert d["is_active"] is True
    assert d["enabled"] is True
    assert d["last_seen_at"]


def test_cordoned_and_not_answering_are_distinguishable_on_the_wire():
    """A single boolean could not tell these apart, and they need different
    actions from an operator: lift the cordon, or go and find out why the node
    is silent."""
    cordoned = _node(enabled=False, last_seen_at=now_iso()).to_dict()
    silent = _node(enabled=True, last_seen_at=None).to_dict()

    assert cordoned["is_active"] is False and silent["is_active"] is False
    assert (cordoned["enabled"], bool(cordoned["last_seen_at"])) == (False, True)
    assert (silent["enabled"], bool(silent["last_seen_at"])) == (True, False)


# ── The migration onto a fleet.db written before these columns ────────────────


def test_a_pre_existing_fleet_db_gains_the_columns(tmp_path: Path):
    """PRM-134 shipped this table without them; an upgrade must not need a wipe."""
    import sqlite3

    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE nodes (
            id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, manager_url TEXT NOT NULL,
            node_type TEXT NOT NULL, tag TEXT, is_active INTEGER NOT NULL DEFAULT 0,
            hardware_amortization_usd_per_hour REAL NOT NULL,
            electricity_usd_per_hour REAL NOT NULL, price_margin_multiplier REAL NOT NULL,
            engines TEXT, created_at TEXT NOT NULL, updated_at TEXT
        );
        INSERT INTO nodes VALUES
            ('id1','legacy','http://legacy:8090','mac',NULL,1,0.3,0.01,1.3,NULL,'2026-01-01',NULL);
        """
    )
    conn.commit()
    conn.close()

    reg = FleetRegistry(path)
    try:
        node = reg.get_by_name("legacy")
        assert node is not None
        # `enabled` defaults to 1: every existing row was registered by an
        # operator who wanted it, and defaulting to 0 would cordon the fleet.
        assert node.enabled is True
        assert node.last_seen_at is None
        # Never seen, so not routable until the first sweep — which is honest
        # rather than convenient.
        assert node.is_active() is False
    finally:
        reg.close()
