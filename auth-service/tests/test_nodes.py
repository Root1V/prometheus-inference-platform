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


async def test_the_table_is_gone(client):
    """PRM-154. This test asserted the opposite until the rollback window closed.

    It is rewritten rather than deleted, because what it pins has not changed —
    only the answer. It said `nodes` must still exist: PRM-134 left the table and
    its rows as the way back, the rows were copied by a script that deleted
    nothing, and the mechanism of the day could not have dropped it anyway. All
    three are now spent. The coordinator has owned the registry since
    2026-09-26, PRM-151 and PRM-152 built on top of it, and a revert to a
    registry nothing has written to since would lose more than it restored.

    So the guard now holds the drop in place: a table this service does not use
    must not come back, because a second writable node registry is worse than
    either service owning it.

    Depends on `client` because that fixture is what initialises the engine and
    runs create_tables; the assertion is about the schema, not the endpoint.
    """
    from prometheus_auth.db import get_engine

    async with get_engine().connect() as conn:
        tables = await conn.run_sync(lambda sync: inspect(sync).get_table_names())
    assert "nodes" not in tables, (
        "the `nodes` table is back. The fleet's node list belongs to the coordinator "
        "manager-api (PRM-134); this service owns security and only security."
    )


async def test_the_model_is_gone_too(client):
    """A table dropped while the model survives comes back on the next adoption:
    `create_all` builds it from the models before the baseline is stamped, and
    the drop revision has already run. The model going is what makes it stay
    gone."""
    import prometheus_auth.db as db

    assert not hasattr(db, "Node"), "the Node model is back, so create_all will rebuild the table"
    assert not hasattr(db, "NodeType")
