"""The fleet registry — which nodes exist. PRM-134.

Implements: docs/roadmap.md — RM-20 (the registry), PRM-133 (declared engines),
PRM-134 (this move).

## Why this lives here, and why it is a second database

RM-20 put fleet inventory in **auth-service**, because auth-service was the only
central service that existed. The reason was real and the placement was wrong: a
node row touches no principal, no token and no scope. It is topology
(`manager_url`), a hardware class, cost figures, and a list of installed
engines. Keeping it there made the identity service a dependency of serving
inference — measured on 2026-09-26, when a gateway that could not authenticate
to auth-service emptied its whole model catalog.

The fleet belongs to the thing that manages the fleet. That is this package, and
it is what Kubernetes, Nomad, Consul and Ray all do: node objects live in the
control plane's own store and the identity system never holds them.

**It is a separate database from `registry.db` on purpose.** `registry.db` is
per-node — the models and instances on *this* host — and every `manager-api` has
its own. The list *of* nodes is fleet-level and there is exactly one of it, so
mixing the two in one file would make a per-node database contain data that is
not per-node, and a node's own registry would stop being safe to wipe or move.

## The coordinator

`manager-api` runs on every node, so "the manager owns the fleet" needs an
answer to *which* manager. One is designated the coordinator in its
`manager.toml`:

    [fleet]
    coordinator = true

Only that one opens this database and serves the fleet endpoints; the others do
not, and `manager-api` on a plain node behaves exactly as before. That is the
server/client split Nomad uses and the control-plane/node split Kubernetes uses:
symmetric software, one configured role.

## What is deliberately not here

Node **self-registration and heartbeat liveness**, which is the other half of
what those systems do. `is_active` below is still decided by a probe from the
centre, and a registry that cannot be reached is still a registry that answers
nothing — the failure class PRM-146 patched rather than removed. Filed
separately, because it changes how a node comes into existence and that is its
own decision.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

# RM-62 follow-up: platform defaults for a node's cost fields, applied when the
# operator leaves them blank — derived from a MacBook Pro M4 Max (~$8,100
# amortized over three years) plus ~70W sustained inference load at Lima, Peru's
# highest residential electricity tier. Editable per node at any time.
DEFAULT_HARDWARE_AMORTIZATION_USD_PER_HOUR = 0.3082
DEFAULT_ELECTRICITY_USD_PER_HOUR = 0.0146
DEFAULT_PRICE_MARGIN_MULTIPLIER = 1.3

NODE_TYPES = ("mac", "nvidia", "other")

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS nodes (
    id                                  TEXT PRIMARY KEY,
    name                                TEXT NOT NULL UNIQUE,
    manager_url                         TEXT NOT NULL,
    node_type                           TEXT NOT NULL,
    tag                                 TEXT,
    is_active                           INTEGER NOT NULL DEFAULT 0,
    hardware_amortization_usd_per_hour  REAL NOT NULL,
    electricity_usd_per_hour            REAL NOT NULL,
    price_margin_multiplier             REAL NOT NULL,
    -- NULL means this node never declared its engines; '[]' means it declared
    -- it has none. A caller that collapses the two is the bug (PRM-133).
    engines                             TEXT,
    created_at                          TEXT NOT NULL,
    updated_at                          TEXT
);
CREATE INDEX IF NOT EXISTS idx_nodes_name ON nodes(name);
"""


@dataclass
class Node:
    """One node in the fleet.

    `hourly_cost_usd` is not stored: it is the sum of the two components, and a
    stored total is a second answer to the same question that can disagree with
    the first.
    """

    name: str
    manager_url: str
    node_type: str
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    tag: str | None = None
    is_active: bool = False
    hardware_amortization_usd_per_hour: float = DEFAULT_HARDWARE_AMORTIZATION_USD_PER_HOUR
    electricity_usd_per_hour: float = DEFAULT_ELECTRICITY_USD_PER_HOUR
    price_margin_multiplier: float = DEFAULT_PRICE_MARGIN_MULTIPLIER
    # Kept as the parsed list; `None` and `[]` are different states.
    engines: list[str] | None = None
    created_at: str = ""
    updated_at: str | None = None

    @property
    def hourly_cost_usd(self) -> float:
        return self.hardware_amortization_usd_per_hour + self.electricity_usd_per_hour

    def to_dict(self) -> dict[str, object]:
        """The wire shape, unchanged from what auth-service served."""
        return {
            "id": self.id,
            "name": self.name,
            "manager_url": self.manager_url,
            "node_type": self.node_type,
            "tag": self.tag,
            "is_active": self.is_active,
            "hardware_amortization_usd_per_hour": self.hardware_amortization_usd_per_hour,
            "electricity_usd_per_hour": self.electricity_usd_per_hour,
            "price_margin_multiplier": self.price_margin_multiplier,
            "hourly_cost_usd": self.hourly_cost_usd,
            "engines": self.engines,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _engines_from_column(raw: str | None) -> list[str] | None:
    """Stored JSON back into a list, preserving "never declared".

    A row that reads NULL must stay `None` all the way to the caller: the
    instance form decides what to offer from it, and `[]` would tell it the node
    can launch nothing.
    """
    if raw is None:
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return [str(x) for x in parsed] if isinstance(parsed, list) else None


def _row_to_node(row: sqlite3.Row) -> Node:
    return Node(
        id=row["id"],
        name=row["name"],
        manager_url=row["manager_url"],
        node_type=row["node_type"],
        tag=row["tag"],
        is_active=bool(row["is_active"]),
        hardware_amortization_usd_per_hour=row["hardware_amortization_usd_per_hour"],
        electricity_usd_per_hour=row["electricity_usd_per_hour"],
        price_margin_multiplier=row["price_margin_multiplier"],
        engines=_engines_from_column(row["engines"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


class NodeExistsError(ValueError):
    """A node with that name is already registered. Names are unique because
    dashboard URLs use them."""


class FleetRegistry:
    """The fleet's node list, in its own SQLite database.

    Same connection idiom as `Registry` — WAL, a lock, an idempotent schema
    script — so the two behave alike operationally. Deliberately *not* the same
    file: see this module's docstring.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.RLock()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.executescript(_SCHEMA_SQL)
        self._conn.commit()

    @property
    def path(self) -> Path:
        return self._path

    def list(self) -> list[Node]:
        """Every node, newest first."""
        with self._lock:
            rows = self._conn.execute("SELECT * FROM nodes ORDER BY created_at DESC").fetchall()
        return [_row_to_node(r) for r in rows]

    def get(self, node_id: str) -> Node | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM nodes WHERE id = ?", (node_id,)).fetchone()
        return _row_to_node(row) if row else None

    def get_by_name(self, name: str) -> Node | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM nodes WHERE name = ?", (name,)).fetchone()
        return _row_to_node(row) if row else None

    def add(self, node: Node) -> Node:
        """Insert. Raises NodeExistsError on a duplicate name."""
        if not node.created_at:
            node.created_at = _now()
        with self._lock:
            if self._conn.execute("SELECT 1 FROM nodes WHERE name = ?", (node.name,)).fetchone():
                raise NodeExistsError(node.name)
            self._conn.execute(
                "INSERT INTO nodes (id, name, manager_url, node_type, tag, is_active, "
                "hardware_amortization_usd_per_hour, electricity_usd_per_hour, "
                "price_margin_multiplier, engines, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    node.id,
                    node.name,
                    node.manager_url,
                    node.node_type,
                    node.tag,
                    int(node.is_active),
                    node.hardware_amortization_usd_per_hour,
                    node.electricity_usd_per_hour,
                    node.price_margin_multiplier,
                    json.dumps(node.engines) if node.engines is not None else None,
                    node.created_at,
                    node.updated_at,
                ),
            )
            self._conn.commit()
        return node

    def update(self, node: Node) -> Node:
        """Write every mutable column of an existing row and stamp `updated_at`.

        The caller mutates a `Node` it read and hands it back, so which fields
        are "set" is decided in the API layer where `model_fields_set` is
        available — not here, where an unset field and a cleared one look alike.
        """
        node.updated_at = _now()
        with self._lock:
            self._conn.execute(
                "UPDATE nodes SET manager_url = ?, node_type = ?, tag = ?, is_active = ?, "
                "hardware_amortization_usd_per_hour = ?, electricity_usd_per_hour = ?, "
                "price_margin_multiplier = ?, engines = ?, updated_at = ? WHERE id = ?",
                (
                    node.manager_url,
                    node.node_type,
                    node.tag,
                    int(node.is_active),
                    node.hardware_amortization_usd_per_hour,
                    node.electricity_usd_per_hour,
                    node.price_margin_multiplier,
                    json.dumps(node.engines) if node.engines is not None else None,
                    node.updated_at,
                    node.id,
                ),
            )
            self._conn.commit()
        return node

    def delete(self, node_id: str) -> bool:
        """True if a row was removed, False if there was nothing to remove."""
        with self._lock:
            cursor = self._conn.execute("DELETE FROM nodes WHERE id = ?", (node_id,))
            self._conn.commit()
            return cursor.rowcount > 0

    def close(self) -> None:
        with self._lock:
            self._conn.close()
