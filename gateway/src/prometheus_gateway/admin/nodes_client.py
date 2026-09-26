"""Read the fleet's node list from the coordinator manager-api.

Implements: docs/roadmap.md — RM-20 (replaced the static MANAGER_NODES env var),
PRM-134 (the registry moved from auth-service to the fleet coordinator).

Shared by models/manager_sync.py's poll loop and admin/billing_router.py — both
need the current (name, manager_url) pairs, read on every call rather than
cached, so a node added in the dashboard becomes usable without a restart.

**This used to call auth-service with a shared admin key.** It now asks the
manager-api whose `manager.toml` sets `[fleet] coordinator = true`, with the
`backend-registry:read` token the caller already holds for polling each node.
Three things follow:

  * The identity service is no longer on the inference path. On 2026-09-26 a
    gateway that could not authenticate to auth-service emptied its whole model
    catalog; that class of outage is gone.
  * One credential on this path instead of two.
  * The fleet is owned by the thing that manages the fleet, which is what
    Kubernetes, Nomad and Consul all do.

The caller passes headers rather than credentials because both callers already
maintain a renewed token; minting a second one here would double the token
traffic and give the two paths different expiry windows.
"""

from __future__ import annotations

import httpx

_TIMEOUT_S = 10.0


async def fetch_nodes(
    fleet_url: str,
    headers: dict[str, str],
    *,
    tls_verify: bool = True,
) -> list[tuple[str, str]]:
    """Return [(node_name, manager_url), ...] for active nodes.

    Inactive nodes are excluded: routing and instance control must never target a
    node already known to be unreachable.

    PRM-151: `is_active` is **derived by the coordinator** — the operator has not
    cordoned the node *and* it was seen within the liveness TTL — and this
    deliberately does not recompute it. The definition of routable belongs to the
    fleet's owner; a second copy of that rule here is exactly how two answers to
    one question start disagreeing. The response also carries `enabled` and
    `last_seen_at` separately for anything that needs to tell "cordoned" from
    "not answering", which this does not. The unfiltered list, inactive rows included, is what the
    dashboard's Nodes page shows, and it comes from the coordinator directly.

    Raises on any transport or status error rather than returning `[]`. That is
    deliberate and it is RM-98/PRM-146: a registry that could not be *asked* must
    never be indistinguishable from a registry with nothing in it. The caller
    decides what to do with the failure; it must not be told there are no nodes.
    """
    if not fleet_url:
        raise RuntimeError(
            "No fleet coordinator configured — set MANAGER_FLEET_URL to the base URL "
            "of the manager-api whose manager.toml has [fleet] coordinator = true."
        )
    async with httpx.AsyncClient(timeout=_TIMEOUT_S, verify=tls_verify) as client:
        resp = await client.get(f"{fleet_url}/v1/fleet/nodes", headers=headers)
    resp.raise_for_status()
    return [
        (node["name"], node["manager_url"]) for node in resp.json() if node.get("is_active", True)
    ]
