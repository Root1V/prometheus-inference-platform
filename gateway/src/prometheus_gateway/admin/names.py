"""Client display names, resolved by the gateway — PRM-272.

The admin endpoints name clients by id, and the dashboard turns ids into names
by joining against /admin/api/users — which needs `admin:principals:read` and
carries every principal's email and scopes along with the name. A reader that
is not allowed the principal list, the assistant, was left reading
"657cdef9-…" for every client.

This gateway already reaches auth-service with its own admin key, so it
resolves the name itself and the endpoints hand out only that: what an
operator calls a client by, never its email, scopes or role. Same precedence
as the Activity page — the label, then the client's name.

Cached for a minute because the dashboard polls these pages every few seconds,
and a rename appearing a minute late costs nothing. A failure is an empty map,
never an error: a name decorates these pages, and they must not go down
because auth-service is slow.
"""

from __future__ import annotations

import time
from typing import Any

import httpx
from fastapi import Request

from ..telemetry import get_logger

logger = get_logger(__name__)

_TTL_S = 60.0


async def client_directory(request: Request) -> list[dict[str, Any]]:
    """Every principal auth-service knows, as `{client_id, name, active}`.

    The roster behind `client_names`, and the answer to "who are the clients"
    for a reader that cannot have the principal list. Whether a client is
    active travels with the name because "is X still enabled" is the next
    question; its role does not, because a role says which credentials are
    admins — that is the inventory this module exists to keep back.
    """
    state = request.app.state
    cached = getattr(state, "client_directory", None)
    if cached is not None and cached[0] > time.monotonic():
        hit: list[dict[str, Any]] = cached[1]
        return hit
    settings = state.settings
    if not settings.auth_service_admin_url or not settings.auth_service_admin_api_key:
        return []
    try:
        async with httpx.AsyncClient(
            timeout=3.0, verify=settings.auth_service_tls_verify
        ) as http_client:
            resp = await http_client.get(
                f"{settings.auth_service_admin_url}/clients",
                headers={"X-Admin-Key": settings.auth_service_admin_api_key},
            )
        resp.raise_for_status()
        directory: list[dict[str, Any]] = []
        for row in resp.json():
            # Stripped: labels are typed by hand, and one measured here ended
            # in a space that every answer then carried.
            name = (row.get("label") or "").strip() or (row.get("client_name") or "").strip()
            if row.get("client_id") and name:
                directory.append(
                    {
                        "client_id": row["client_id"],
                        "name": name,
                        "active": bool(row.get("is_active", True)),
                    }
                )
    except Exception as exc:
        logger.warning("admin.client_names_unavailable", error=str(exc))
        return []
    state.client_directory = (time.monotonic() + _TTL_S, directory)
    return directory


async def client_names(request: Request) -> dict[str, str]:
    """`client_id → display name` for every principal auth-service knows."""
    return {row["client_id"]: row["name"] for row in await client_directory(request)}
