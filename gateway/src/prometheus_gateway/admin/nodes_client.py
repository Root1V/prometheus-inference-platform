"""Fetch the live node list from auth-service's node registry.

Implements: docs/roadmap.md — RM-20 (replaces the static MANAGER_NODES env var).

Shared by admin/router.py's dashboard proxy and models/manager_sync.py's periodic
poll loop — both need the current (name, manager_url) pairs, refreshed on every
call rather than cached, so an admin-added node becomes usable without a restart.
"""

from __future__ import annotations

import httpx


async def fetch_nodes(
    auth_service_admin_url: str,
    auth_service_admin_api_key: str,
    *,
    tls_verify: bool = True,
    timeout: float = 10.0,
) -> list[tuple[str, str]]:
    """Return [(node_name, manager_url), ...] from auth-service's /admin/nodes."""
    async with httpx.AsyncClient(timeout=timeout, verify=tls_verify) as client:
        resp = await client.get(
            f"{auth_service_admin_url}/nodes",
            headers={"X-Admin-Key": auth_service_admin_api_key},
        )
    resp.raise_for_status()
    return [(node["name"], node["manager_url"]) for node in resp.json()]
