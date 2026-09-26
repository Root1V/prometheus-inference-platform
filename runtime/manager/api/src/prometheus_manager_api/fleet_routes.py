"""Fleet endpoints — which nodes exist. PRM-134.

Implements: docs/roadmap.md — RM-20 (the registry), PRM-133 (declared engines),
PRM-134 (this move).

`/v1/fleet/nodes/*`, a namespace of its own rather than `/v1/nodes`, because
everything else this API serves is about *this* host — its models, its instances,
its processes — and this is the one thing that is about the fleet. The path says
which.

These seven were auth-service's until PRM-134. See
`prometheus_manager_core.fleet` for why they moved and why the fleet has its own
database.

**Only the coordinator answers them.** The router is mounted on every
`manager-api` all the same, and a node that is not the coordinator returns a 409
saying so. That is deliberate: a 404 would be indistinguishable from a typo in
the path, and an operator debugging a fleet that will not list needs to be told
which of the two it is (RM-98 — a refusal that explains itself beats an absence).
"""

from __future__ import annotations

import re
from typing import Annotated, Any

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from prometheus_manager_core.fleet import (
    DEFAULT_ELECTRICITY_USD_PER_HOUR,
    DEFAULT_HARDWARE_AMORTIZATION_USD_PER_HOUR,
    DEFAULT_PRICE_MARGIN_MULTIPLIER,
    NODE_TYPES,
    FleetRegistry,
    Node,
    NodeExistsError,
)
from prometheus_manager_core.telemetry import get_logger
from pydantic import BaseModel, Field, field_validator

from .auth import require_backend_registry_read, require_backend_registry_write

logger = get_logger(__name__)

router = APIRouter()

Claims = dict[str, Any]

# A node's own liveness probe, unauthenticated, as manager-api's /health is.
# Three seconds: this runs while an operator waits on a form, and a node that
# cannot answer a health check in three seconds is not one to route inference at.
_PROBE_TIMEOUT_S = 3.0

# PRM-133: an engine id is validated by shape, not against
# `prometheus_manager_core.registry.BACKENDS`. That list is what *this* node can
# launch; a fleet row describes what some *other* node has installed, and a
# coordinator that rejected an engine it does not have itself would refuse to
# register a perfectly good node. Shape is the only thing the coordinator can
# honestly check.
_ENGINE_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")


def _validate_engines(value: list[str] | None) -> list[str] | None:
    if value is None:
        return None
    seen: list[str] = []
    for item in value:
        if not _ENGINE_ID_RE.match(item):
            raise ValueError(
                f"Invalid engine id {item!r}: lowercase letters, digits, '_' and '-', "
                "starting with a letter, at most 32 characters."
            )
        if item not in seen:
            seen.append(item)
    return seen


def _validate_node_type(value: str | None) -> str | None:
    if value is not None and value not in NODE_TYPES:
        raise ValueError(f"node_type must be one of: {', '.join(NODE_TYPES)}")
    return value


class CreateNodeRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    manager_url: str = Field(..., min_length=1, max_length=512)
    node_type: str
    tag: str | None = Field(None, max_length=255)
    hardware_amortization_usd_per_hour: float | None = Field(None, ge=0)
    electricity_usd_per_hour: float | None = Field(None, ge=0)
    price_margin_multiplier: float | None = Field(None, gt=0)
    engines: list[str] | None = Field(None, max_length=32)

    _engines = field_validator("engines")(_validate_engines)
    _type = field_validator("node_type")(_validate_node_type)


class UpdateNodeRequest(BaseModel):
    """Partial update — only supplied fields change. `name` is immutable."""

    manager_url: str | None = Field(None, min_length=1, max_length=512)
    node_type: str | None = None
    tag: str | None = Field(None, max_length=255)
    hardware_amortization_usd_per_hour: float | None = Field(None, ge=0)
    electricity_usd_per_hour: float | None = Field(None, ge=0)
    price_margin_multiplier: float | None = Field(None, gt=0)
    engines: list[str] | None = Field(None, max_length=32)

    _engines = field_validator("engines")(_validate_engines)
    _type = field_validator("node_type")(_validate_node_type)


def _fleet(request: Request) -> FleetRegistry | None:
    """The coordinator's registry, or None on a node that is not the coordinator."""
    return getattr(request.app.state, "fleet", None)


def _not_coordinator() -> JSONResponse:
    return JSONResponse(
        status_code=409,
        content={
            "detail": (
                "This manager-api is not the fleet coordinator, so it holds no node "
                "registry. Exactly one node in the fleet sets `[fleet] coordinator = "
                "true` in its manager.toml; ask that one. This is a 409 rather than a "
                "404 so it cannot be mistaken for a wrong path."
            )
        },
    )


def _not_found() -> JSONResponse:
    return JSONResponse(status_code=404, content={"detail": "Node not found."})


async def _probe(manager_url: str) -> bool:
    """GET {manager_url}/health — the node's own liveness probe, no auth needed."""
    try:
        async with httpx.AsyncClient(timeout=_PROBE_TIMEOUT_S) as client:
            resp = await client.get(f"{manager_url.rstrip('/')}/health")
        return resp.status_code == 200
    except Exception:
        return False


@router.get("/v1/fleet/nodes", tags=["fleet"])
async def list_nodes(
    request: Request,
    _claims: Annotated[Claims, Depends(require_backend_registry_read)],
) -> Any:
    fleet = _fleet(request)
    if fleet is None:
        return _not_coordinator()
    return [n.to_dict() for n in fleet.list()]


@router.post("/v1/fleet/nodes", tags=["fleet"], status_code=201)
async def create_node(
    body: CreateNodeRequest,
    request: Request,
    _claims: Annotated[Claims, Depends(require_backend_registry_write)],
) -> Any:
    """Register a node.

    A connectivity check runs immediately and an unreachable node is still
    registered — created inactive rather than rejected, so the operator does not
    lose the entry they just typed to a node that is merely asleep.
    """
    fleet = _fleet(request)
    if fleet is None:
        return _not_coordinator()

    reachable = await _probe(body.manager_url)
    node = Node(
        name=body.name,
        manager_url=body.manager_url,
        node_type=body.node_type,
        tag=body.tag,
        is_active=reachable,
        hardware_amortization_usd_per_hour=(
            body.hardware_amortization_usd_per_hour
            if body.hardware_amortization_usd_per_hour is not None
            else DEFAULT_HARDWARE_AMORTIZATION_USD_PER_HOUR
        ),
        electricity_usd_per_hour=(
            body.electricity_usd_per_hour
            if body.electricity_usd_per_hour is not None
            else DEFAULT_ELECTRICITY_USD_PER_HOUR
        ),
        price_margin_multiplier=(
            body.price_margin_multiplier
            if body.price_margin_multiplier is not None
            else DEFAULT_PRICE_MARGIN_MULTIPLIER
        ),
        # PRM-133: no default, unlike the three above. A node without a cost is
        # unusable so it gets a platform constant; a node without a declared
        # engine list is merely undeclared, and inventing one here would make
        # "we don't know" indistinguishable from a claim.
        engines=body.engines,
    )
    try:
        fleet.add(node)
    except NodeExistsError:
        return JSONResponse(
            status_code=409, content={"detail": f"Node {body.name!r} already exists."}
        )

    logger.info("fleet.node_created", node_id=node.id, name=node.name, is_active=reachable)
    return JSONResponse(status_code=201, content=node.to_dict())


@router.patch("/v1/fleet/nodes/{node_id}", tags=["fleet"])
async def update_node(
    node_id: str,
    body: UpdateNodeRequest,
    request: Request,
    _claims: Annotated[Claims, Depends(require_backend_registry_write)],
) -> Any:
    """Partially update a node. Changing `manager_url` re-runs the probe, because
    a URL change invalidates whatever was last observed."""
    fleet = _fleet(request)
    if fleet is None:
        return _not_coordinator()
    node = fleet.get(node_id)
    if node is None:
        return _not_found()

    if body.manager_url is not None and body.manager_url != node.manager_url:
        node.manager_url = body.manager_url
        node.is_active = await _probe(body.manager_url)
    if body.node_type is not None:
        node.node_type = body.node_type
    if "tag" in body.model_fields_set:
        node.tag = body.tag
    if body.hardware_amortization_usd_per_hour is not None:
        node.hardware_amortization_usd_per_hour = body.hardware_amortization_usd_per_hour
    if body.electricity_usd_per_hour is not None:
        node.electricity_usd_per_hour = body.electricity_usd_per_hour
    if body.price_margin_multiplier is not None:
        node.price_margin_multiplier = body.price_margin_multiplier
    # PRM-133: `model_fields_set`, like `tag` — `engines: []` is a real edit
    # meaning "this node has none", and an `is not None` check would drop it.
    if "engines" in body.model_fields_set:
        node.engines = body.engines

    fleet.update(node)
    logger.info("fleet.node_updated", node_id=node.id)
    return node.to_dict()


@router.post("/v1/fleet/nodes/{node_id}/check", tags=["fleet"])
async def check_node(
    node_id: str,
    request: Request,
    _claims: Annotated[Claims, Depends(require_backend_registry_write)],
) -> Any:
    """Re-run the probe and report. How a node marked inactive comes back."""
    fleet = _fleet(request)
    if fleet is None:
        return _not_coordinator()
    node = fleet.get(node_id)
    if node is None:
        return _not_found()

    node.is_active = await _probe(node.manager_url)
    fleet.update(node)
    logger.info("fleet.node_checked", node_id=node.id, is_active=node.is_active)
    return node.to_dict()


@router.post("/v1/fleet/nodes/{node_id}/activate", tags=["fleet"])
async def activate_node(
    node_id: str,
    request: Request,
    _claims: Annotated[Claims, Depends(require_backend_registry_write)],
) -> Any:
    """Try to bring a node back into rotation — gated on an actual probe.

    Unlike /deactivate this cannot just flip the flag: showing "Active" for a node
    that still cannot be reached is a lie the operator would act on. Functionally
    the same probe as /check, kept a separate route because "I want this node back
    in service" and "tell me the current status" are different intents and the
    dashboard messages them differently.
    """
    fleet = _fleet(request)
    if fleet is None:
        return _not_coordinator()
    node = fleet.get(node_id)
    if node is None:
        return _not_found()

    node.is_active = await _probe(node.manager_url)
    fleet.update(node)
    logger.info("fleet.node_activate_attempted", node_id=node.id, is_active=node.is_active)
    return node.to_dict()


@router.post("/v1/fleet/nodes/{node_id}/deactivate", tags=["fleet"])
async def deactivate_node(
    node_id: str,
    request: Request,
    _claims: Annotated[Claims, Depends(require_backend_registry_write)],
) -> Any:
    """Mark a node inactive — an on-demand override, e.g. for maintenance.

    No probe: taking a node out of rotation needs no permission from the node.
    """
    fleet = _fleet(request)
    if fleet is None:
        return _not_coordinator()
    node = fleet.get(node_id)
    if node is None:
        return _not_found()

    node.is_active = False
    fleet.update(node)
    logger.info("fleet.node_deactivated", node_id=node.id)
    return node.to_dict()


@router.delete("/v1/fleet/nodes/{node_id}", tags=["fleet"])
async def delete_node(
    node_id: str,
    request: Request,
    _claims: Annotated[Claims, Depends(require_backend_registry_write)],
) -> Any:
    fleet = _fleet(request)
    if fleet is None:
        return _not_coordinator()
    if not fleet.delete(node_id):
        return _not_found()
    logger.info("fleet.node_deleted", node_id=node_id)
    return JSONResponse(status_code=204, content=None)
