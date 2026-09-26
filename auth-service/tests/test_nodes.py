"""The node registry is not here any more — PRM-134.

RM-20 put fleet inventory in this service and 25 behaviour tests here with it.
Those tests are not deleted: they were ported verbatim to
`runtime/manager/api/tests/test_fleet.py`, where the registry now lives. What
stays behind is the guard that the move actually happened and stays happened.

Why a guard and not nothing: two services that both serve a writable node
registry is worse than either owning it, and the way that happens is somebody
restoring these routes because a test was missing. This file is that test.
"""

from __future__ import annotations

import pytest
from sqlalchemy import inspect

from .conftest import ADMIN_HEADERS

_GONE = (
    ("get", "/admin/nodes"),
    ("post", "/admin/nodes"),
    ("patch", "/admin/nodes/any-id"),
    ("post", "/admin/nodes/any-id/check"),
    ("post", "/admin/nodes/any-id/activate"),
    ("post", "/admin/nodes/any-id/deactivate"),
    ("delete", "/admin/nodes/any-id"),
)


@pytest.mark.parametrize("method,path", _GONE)
async def test_the_node_routes_are_gone(client, method, path):
    """404 — the route does not exist, as opposed to 403 or 409, which would mean
    it still exists and is refusing."""
    resp = await getattr(client, method)(path, headers=ADMIN_HEADERS)
    assert resp.status_code == 404, (
        f"{method.upper()} {path} still answers {resp.status_code}. The fleet registry "
        "lives in the coordinator manager-api now (PRM-134); two writable copies is "
        "worse than either owning it."
    )


async def test_no_route_on_this_service_mentions_nodes(settings):
    """The parametrised list above only catches paths somebody thought to name.
    This catches a node route added under any path at all."""
    from prometheus_auth.main import create_app

    paths = [getattr(r, "path", "") for r in create_app(settings=settings).routes]
    offenders = [p for p in paths if "node" in p.lower()]
    assert not offenders, f"auth-service is serving node routes again: {offenders}"


async def test_the_table_is_still_here(client):
    """Deliberate, and it is the rollback path.

    The rows were copied to the coordinator by scripts/migrate_node_registry.py,
    which deletes nothing. Reverting the code restores a working registry, and this
    codebase's additive-only convention does not drop tables — so `nodes` stays
    until a later cleanup removes the model too.

    Depends on `client` because that fixture is what initialises the engine and
    runs create_tables; the assertion is about the schema, not the endpoint.
    """
    from prometheus_auth.db import get_engine

    async with get_engine().connect() as conn:
        tables = await conn.run_sync(lambda sync: inspect(sync).get_table_names())
    assert "nodes" in tables, (
        "the `nodes` table was dropped. It is the rollback path for PRM-134 and this "
        "codebase does not drop tables."
    )
