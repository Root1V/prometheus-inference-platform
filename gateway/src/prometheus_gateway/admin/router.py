"""Admin dashboard JSON API — RM-10.

Mounted at /admin/api by main.py when admin_dashboard_enabled=True. Every
route except /admin/api/auth/login requires a Bearer JWT (validated by the
gateway's global JWTAuthMiddleware, same as /v1/backends) plus admin:read
(GET) or admin:write (mutations). The SPA itself is served separately as
static files — see main.py and auth/middleware.py's _is_exempt().

POST   /admin/api/auth/login                                — exchange client_id/secret for a JWT
GET    /admin/api/nodes                                    — list registered nodes (RM-20)
POST   /admin/api/nodes                                    — register a node
PATCH  /admin/api/nodes/{node_id}                            — update a node
DELETE /admin/api/nodes/{node_id}                            — remove a node
POST   /admin/api/nodes/{node_id}/check                      — re-run connectivity check
POST   /admin/api/nodes/{node_id}/activate                   — re-probe and activate only if reachable
POST   /admin/api/nodes/{node_id}/deactivate                 — manually mark inactive (no probe, on-demand)
GET    /admin/api/instances                                 — aggregated across all nodes
POST   /admin/api/nodes/{node}/models                        — register
PATCH  /admin/api/nodes/{node}/models/{model_id}               — update fields
DELETE /admin/api/nodes/{node}/models/{model_id}              — deregister
GET    /admin/api/nodes/{node}/models/search                 — search Hugging Face (RM-48)
GET    /admin/api/nodes/{node}/models/search/files            — list a repo's GGUF files
GET    /admin/api/nodes/{node}/models/search/card              — fetch a repo's model card
POST   /admin/api/nodes/{node}/models/downloads               — register + start downloading
GET    /admin/api/nodes/{node}/models/downloads               — list download progress
POST   /admin/api/nodes/{node}/models/downloads/{model_id}/cancel
POST   /admin/api/nodes/{node}/models/downloads/{model_id}/pause
POST   /admin/api/nodes/{node}/models/downloads/{model_id}/resume
POST   /admin/api/nodes/{node}/models/downloads/{model_id}/retry
DELETE /admin/api/nodes/{node}/models/{model_id}/downloaded    — delete file + deregister
GET    /admin/api/nodes/{node}/models/config                  — current download settings
PATCH  /admin/api/nodes/{node}/models/config                  — update them (this session)
POST   /admin/api/nodes/{node}/instances/{model_id}/start
POST   /admin/api/nodes/{node}/instances/{model_id}/stop
POST   /admin/api/nodes/{node}/instances/{model_id}/restart
GET    /admin/api/nodes/{node}/instances/{model_id}/logs   — tail its log file (RM-13)
GET    /admin/api/users                                     — list principals
POST   /admin/api/users                                     — create (oauth2 or password)
PATCH  /admin/api/users/{client_id}                          — update
DELETE /admin/api/users/{client_id}                          — deactivate
POST   /admin/api/users/{client_id}/reactivate
POST   /admin/api/users/{client_id}/rotate-secret            — oauth2 principals
POST   /admin/api/users/{client_id}/reset-password           — password principals
POST   /admin/api/users/{client_id}/share                    — one-time credential link
POST   /admin/api/users/share/{token_id}/revoke
GET    /admin/api/limits                                    — current rate limits + .env defaults (RM-56)
PUT    /admin/api/limits                                    — set limits live, persisted (RM-56)
DELETE /admin/api/limits                                    — drop the override, back to .env (RM-56)
GET    /admin/api/circuit-breaker                           — current CB thresholds + .env defaults (RM-67)
PUT    /admin/api/circuit-breaker                           — set them live, persisted (RM-67)
DELETE /admin/api/circuit-breaker                           — drop the override, back to .env (RM-67)
GET    /admin/api/config                                    — dashboard-facing settings (RM-31)
GET    /admin/api/sessions                                  — clients active in the last 15m (RM-23)
GET    /admin/api/activity                                  — who is consuming and how much (PRM-235)

Implements: docs/roadmap.md — RM-10 (gateway admin dashboard, phase 1)
Implements: docs/roadmap.md — RM-11 (Users section, dual login modes)
Implements: docs/roadmap.md — RM-20 (Nodes section, replaces static MANAGER_NODES)
Implements: docs/roadmap.md — RM-13 (admin dashboard: live log viewer)
Implements: docs/roadmap.md — RM-16 (routing & rate-limit visibility)
Implements: docs/roadmap.md — RM-23 (active sessions / connected users)
Implements: docs/roadmap.md — RM-48 (Models: discover/download/manage on Hugging Face)
"""

from __future__ import annotations

import datetime as _dt
import json
import time as _time
from typing import Any

import httpx
from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from .. import audit, db, pricing, rate_limits, traffic_split
from ..budget import resolve_client_limits
from ..rate_limit_middleware import INFERENCE_ENDPOINT_SLUGS
from ..rate_limiter import PLATFORM_IDENTITY, RateLimiter
from ..config import Settings
from ..router import _problem
from ..telemetry import activity_tracker, get_logger
from .client import _CONTROL_TIMEOUT_S, ManagerApiClient
from .nodes_client import fetch_nodes

logger = get_logger(__name__)

# PRM-235: how many consumers the Activity page lists. Per-consumer series are
# high-cardinality — the reason LiteLLM keeps end users out of its Prometheus
# export by default — and a page that renders every credential on a platform
# built for hundreds is a page nobody reads. The rest are counted, not listed.
_ACTIVITY_TOP_N = 50


def _claims(request: Request) -> Any:
    return getattr(getattr(request, "state", None), "claims", None)


def _require_scope(request: Request, scope: str) -> JSONResponse | None:
    claims = _claims(request)
    if claims is None or not claims.has_scope(scope):
        return _problem(
            request, 403, "forbidden", "Forbidden", f"This endpoint requires {scope} scope."
        )
    return None


async def _resolve_node(request: Request, node: str) -> str | None:
    """Return the registered manager_url for *node*, or None if unknown.

    RM-20: node topology is fetched fresh on every call (admin-only, low-QPS
    path — not the hot inference request path).

    PRM-134: from the fleet coordinator rather than auth-service. This function
    is module-level, so it takes the manager client off `app.state` the same way
    it already takes settings — main.py puts it there for exactly this.
    """
    settings: Settings = request.app.state.settings
    client = getattr(request.app.state, "manager_client", None)
    if client is None:
        raise RuntimeError(
            "No manager client on app.state — the admin router was mounted without "
            "one, so the fleet coordinator cannot be asked for the node list."
        )
    nodes = await fetch_nodes(settings.resolved_fleet_url, await client._headers())
    for name, url in nodes:
        if name == node:
            return url
    return None


def _rewrite_share_url(resp: Response, request: Request) -> Response:
    """Point the share link at the gateway, not at auth-service.

    PRM-102: auth-service builds the link from the base URL of the request that
    asked for it. That request comes from the gateway, so the operator was shown
    an address on the internal network — a link the recipient could not open
    even before the port was closed. The gateway is the only party that knows
    its own public address, so it is the one that can fix this.
    """
    if resp.status_code != 200:
        return resp
    try:
        body = json.loads(bytes(resp.body))
    except (ValueError, TypeError):
        return resp
    url = body.get("share_url")
    if not isinstance(url, str):
        return resp
    _, sep, token = url.rpartition("/share/")
    if not sep:
        return resp
    body["share_url"] = f"{str(request.base_url).rstrip('/')}/share/{token}"
    return JSONResponse(content=body, status_code=200)


def _passthrough(resp: httpx.Response) -> JSONResponse:
    """Forward a manager-api response, flattening its RFC 9457 error shape.

    manager-api raises errors as FastAPI HTTPException(detail={...}), which
    FastAPI's default handler wraps as {"detail": {...}}. The gateway's own
    _problem() returns the RFC 9457 fields at the top level instead — flatten
    here so every /admin/api/* error has the same shape regardless of which
    service actually produced it.
    """
    body = resp.json() if resp.content else None
    if (
        resp.status_code >= 400
        and isinstance(body, dict)
        and isinstance(body.get("detail"), dict)
        and "type" in body["detail"]
    ):
        body = body["detail"]
    return JSONResponse(content=body, status_code=resp.status_code)


def _proxy_error_response(request: Request, exc: Exception) -> JSONResponse:
    if isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout, httpx.RemoteProtocolError)):
        return _problem(
            request,
            503,
            "backend-unavailable",
            "Backend Unavailable",
            f"The manager node is currently unreachable: {exc}",
        )
    return _problem(
        request,
        502,
        "upstream-error",
        "Upstream Error",
        f"The manager node returned an error: {exc}",
    )


def create_admin_router(manager_client: ManagerApiClient) -> APIRouter:
    router = APIRouter()

    @router.post("/admin/api/auth/login")
    async def login(body: dict[str, Any], request: Request) -> Response:
        """Exchange client_id/secret for a JWT — proxied server-side to the
        auth-service so the SPA never needs a cross-origin call (the browser
        blocks that with CORS since auth-service doesn't run on the gateway's
        origin). No Bearer token required to call this — see _is_exempt().
        """
        settings: Settings = request.app.state.settings
        if not settings.auth_service_token_url:
            return _problem(
                request,
                500,
                "not-configured",
                "Not Configured",
                "AUTH_SERVICE_TOKEN_URL is not set on the gateway — the admin dashboard "
                "cannot authenticate. Contact the platform operator.",
            )

        if "email" in body and "password" in body:
            form = {
                "grant_type": "password",
                "username": body.get("email", ""),
                "password": body.get("password", ""),
                "scope": "admin:read admin:write",
            }
        else:
            form = {
                "grant_type": "client_credentials",
                "client_id": body.get("client_id", ""),
                "client_secret": body.get("client_secret", ""),
                "scope": "admin:read admin:write",
            }
        try:
            async with httpx.AsyncClient(
                timeout=10.0, verify=settings.auth_service_tls_verify
            ) as client:
                resp = await client.post(settings.auth_service_token_url, data=form)
        except Exception as exc:
            return _proxy_error_response(request, exc)

        # PRM-157: this route audits itself, and it is the only one that does.
        # "Who tried to sign in, and did it work" is the most audited fact in any
        # system, and the answer is in the body — which the audit middleware
        # deliberately never reads, because this same body carries a password.
        # Here the handler has already parsed it, so it can record the email and
        # nothing else. A failed attempt is recorded as carefully as a successful
        # one: a run of failures against one address is the pattern an auditor is
        # looking for, and a log of successes alone cannot show it.
        attempted_email = body.get("email") if "email" in body else None

        if resp.status_code != 200:
            # auth-service returns standard OAuth2 error bodies
            # ({"error": ..., "error_description": ...}), not the manager-api
            # shape _passthrough() understands — normalize separately here.
            try:
                oauth_error = resp.json()
            except Exception:
                oauth_error = {}
            status = 401 if resp.status_code in (400, 401) else 502
            await audit.record(
                action="POST /admin/api/auth/login",
                outcome=audit.outcome_for(status),
                status_code=status,
                actor_email=attempted_email,
                actor_client_id=(body.get("client_id") if attempted_email is None else None),
                request_id=getattr(getattr(request, "state", None), "request_id", None),
                trace_id=getattr(getattr(request, "state", None), "trace_id", None),
                source_ip=(request.client.host if request.client else None),
                user_agent=request.headers.get("user-agent"),
            )
            return _problem(
                request,
                status,
                "invalid-credentials",
                "Invalid Credentials",
                oauth_error.get("error_description")
                or oauth_error.get("error")
                or "The auth-service rejected these credentials.",
            )
        body_json = resp.json()
        await audit.record(
            action="POST /admin/api/auth/login",
            outcome=audit.outcome_for(200),
            status_code=200,
            actor_email=attempted_email,
            actor_client_id=(body.get("client_id") if attempted_email is None else None),
            request_id=getattr(getattr(request, "state", None), "request_id", None),
            trace_id=getattr(getattr(request, "state", None), "trace_id", None),
            source_ip=(request.client.host if request.client else None),
            user_agent=request.headers.get("user-agent"),
        )
        return JSONResponse(
            content={
                "access_token": body_json.get("access_token"),
                "expires_in": body_json.get("expires_in"),
            }
        )

    @router.get("/admin/api/traffic-splits")
    async def list_splits(request: Request) -> Any:
        """The live splits — PRM-160. `admin:read`."""
        if (forbidden := _require_scope(request, "admin:read")) is not None:
            return forbidden
        table = traffic_split.get_split_table()
        return {
            "splits": [
                {
                    "name": name,
                    "variants": [
                        {
                            "model_id": v.model_id,
                            "weight": v.weight,
                            # The share this variant actually receives, computed
                            # rather than stored: weights are integers so an
                            # operator can write 1:2 without making them add to
                            # 100, and a stored percentage would be a second
                            # answer that could disagree with the weights.
                            "share_percent": round(
                                v.weight * 100 / sum(x.weight for x in variants), 2
                            ),
                        }
                        for v in variants
                    ],
                }
                for name, variants in sorted(table.all().items())
            ]
        }

    @router.put("/admin/api/traffic-splits/{name}")
    async def put_split(name: str, body: dict[str, Any], request: Request) -> Any:
        """Define or replace a split — PRM-160. `admin:write`.

        Validated before it is stored, not after: a split that would misroute is
        refused where it is written, which is the only place an operator is
        looking. Stored and applied in that order, so a table that rejects the row
        does not leave the process routing on something the database does not have.
        """
        if (forbidden := _require_scope(request, "admin:write")) is not None:
            return forbidden
        try:
            variants = traffic_split.parse_variants(body.get("variants"))
        except (traffic_split.SplitError, ValueError) as exc:
            return _problem(
                request, 400, "invalid-traffic-split", "Invalid Traffic Split", str(exc)
            )

        # Every variant has to be a model this gateway can actually route to, or
        # the split is a promise it cannot keep — and it would only be discovered
        # by whichever caller drew the missing variant.
        registry = getattr(request.app.state, "registry", None)
        if registry is not None:
            unknown = [v.model_id for v in variants if registry.resolve(v.model_id) is None]
            if unknown:
                return _problem(
                    request,
                    400,
                    "unknown-model",
                    "Unknown Model",
                    f"These variants are not registered: {', '.join(sorted(unknown))}. A "
                    "split pointing at a model this gateway does not have would fail for "
                    "whichever share of traffic drew it.",
                )

        await db.upsert_traffic_split(name, traffic_split.serialise(variants))
        traffic_split.get_split_table().set(name, variants)
        logger.info(
            "traffic_split.updated",
            name=name,
            variants=[(v.model_id, v.weight) for v in variants],
        )
        return {
            "name": name,
            "variants": [{"model_id": v.model_id, "weight": v.weight} for v in variants],
        }

    @router.delete("/admin/api/traffic-splits/{name}")
    async def remove_split(name: str, request: Request) -> Any:
        """Stop splitting a name — PRM-160. `admin:write`.

        Afterwards the name resolves as an ordinary model again, which is what it
        was before the split and what ending a rollout means.
        """
        if (forbidden := _require_scope(request, "admin:write")) is not None:
            return forbidden
        existed = await db.delete_traffic_split(name)
        traffic_split.get_split_table().remove(name)
        if not existed:
            return _problem(
                request, 404, "not-found", "Not Found", f"No traffic split named {name!r}."
            )
        logger.info("traffic_split.removed", name=name)
        return Response(status_code=204)

    @router.get("/admin/api/audit")
    async def list_audit(request: Request) -> Any:
        """The audit trail — PRM-157. `admin:read`, newest first.

        A log nobody can read is not a control, which is why this exists rather
        than leaving the rows to whoever has database access. `admin:read` and not
        `admin:write`: reading the trail is not an administrative change, and
        requiring write to see who wrote would mean only the people who can alter
        the system can check it.

        Served from the gateway's own table rather than queried out of Argus,
        because that table is the record. Argus has a copy for search and
        alerting, and a copy is not a source.
        """
        if (forbidden := _require_scope(request, "admin:read")) is not None:
            return forbidden
        try:
            limit = int(request.query_params.get("limit", "100"))
        except ValueError:
            return _problem(
                request, 400, "invalid-limit", "Invalid Limit", "`limit` must be an integer."
            )
        actor = request.query_params.get("actor_client_id")
        events = await db.list_audit_events(limit=limit, actor_client_id=actor)
        return {
            "events": [
                {
                    "id": e.id,
                    "at": e.at.isoformat() if e.at else None,
                    "actor": {
                        "client_id": e.actor_client_id,
                        "user_id": e.actor_user_id,
                        "email": e.actor_email,
                    },
                    "action": e.action,
                    "target": json.loads(e.target) if e.target else None,
                    "outcome": e.outcome,
                    "status_code": e.status_code,
                    "request_id": e.request_id,
                    "trace_id": e.trace_id,
                    "source_ip": e.source_ip,
                    "user_agent": e.user_agent,
                }
                for e in events
            ]
        }

    @router.get("/admin/api/instances")
    async def list_instances(request: Request) -> Any:
        if (forbidden := _require_scope(request, "admin:read")) is not None:
            return forbidden

        settings: Settings = request.app.state.settings
        instances: list[dict[str, Any]] = []
        unreachable_nodes: list[str] = []

        nodes = await fetch_nodes(
            settings.resolved_fleet_url,
            await manager_client._headers(),  # PRM-134: the coordinator, not auth-service
        )
        for name, url in nodes:
            try:
                resp = await manager_client.get(
                    url, "/v1/backends", params={"include_hidden": "true"}
                )
                resp.raise_for_status()
                body = resp.json()
                for entry in body.get("backends", []):
                    entry["node"] = name
                    instances.append(entry)
            except Exception as exc:
                logger.warning("admin.node_unreachable", node=name, error=str(exc))
                unreachable_nodes.append(name)

        return {"instances": instances, "unreachable_nodes": unreachable_nodes}

    @router.get("/admin/api/models")
    async def list_models(request: Request) -> Any:
        """RM-51: cross-node catalog listing — mirrors list_instances above,
        but aggregates manager-api's GET /v1/models (the catalog: downloaded/
        known models) instead of GET /v1/backends (running instances). A
        catalog entry with zero instances (freshly downloaded, not yet
        deployed) only ever shows up here, not in list_instances."""
        if (forbidden := _require_scope(request, "admin:read")) is not None:
            return forbidden

        settings: Settings = request.app.state.settings
        models: list[dict[str, Any]] = []
        unreachable_nodes: list[str] = []

        nodes = await fetch_nodes(
            settings.resolved_fleet_url,
            await manager_client._headers(),  # PRM-134: the coordinator, not auth-service
        )
        for name, url in nodes:
            try:
                resp = await manager_client.get(url, "/v1/models")
                resp.raise_for_status()
                body = resp.json()
                for entry in body.get("models", []):
                    entry["node"] = name
                    models.append(entry)
            except Exception as exc:
                logger.warning("admin.node_unreachable", node=name, error=str(exc))
                unreachable_nodes.append(name)

        # PRM-114: tell the dashboard which of these can still be renamed, so the
        # form stops offering a field the server will refuse. The rule is "does
        # anything depend on this name", and until now the UI used the old one
        # ("was it ever set"), which let you type a name into gpt-oss-20b-mxfp4
        # — 3 grants and 39 billed rows — and only learn on save.
        #
        # Computed for the whole list at once: one grant lookup, one usage query
        # per model, prices in memory. Failing to work it out leaves the field
        # off rather than guessing it is safe.
        await _annotate_rename_blockers(request, models)
        return {"models": models, "unreachable_nodes": unreachable_nodes}

    @router.post("/admin/api/nodes/{node}/models")
    async def register_model(node: str, body: dict[str, Any], request: Request) -> Response:
        if (forbidden := _require_scope(request, "admin:write")) is not None:
            return forbidden
        node_url = await _resolve_node(request, node)
        if node_url is None:
            return _problem(
                request, 400, "unknown-node", "Unknown Node", f"Node {node!r} is not configured."
            )

        try:
            resp = await manager_client.post(node_url, "/v1/backends", json=body)
        except Exception as exc:
            return _proxy_error_response(request, exc)
        return _passthrough(resp)

    # RM-48 follow-up: these two literal /models/config routes MUST be
    # registered before the wildcard PATCH/DELETE /models/{model_id} routes
    # below — Starlette matches path routes in registration order, so a
    # wildcard segment registered first would swallow the literal "config"
    # as if it were a model_id (confirmed by a real 502 in testing before
    # this was moved here).
    @router.get("/admin/api/nodes/{node}/models/config")
    async def get_models_config_proxy(node: str, request: Request) -> Response:
        if (forbidden := _require_scope(request, "admin:read")) is not None:
            return forbidden
        node_url = await _resolve_node(request, node)
        if node_url is None:
            return _problem(
                request, 400, "unknown-node", "Unknown Node", f"Node {node!r} is not configured."
            )
        try:
            resp = await manager_client.get(node_url, "/v1/models/config")
        except Exception as exc:
            return _proxy_error_response(request, exc)
        return _passthrough(resp)

    @router.patch("/admin/api/nodes/{node}/models/config")
    async def update_models_config_proxy(
        node: str, body: dict[str, Any], request: Request
    ) -> Response:
        if (forbidden := _require_scope(request, "admin:write")) is not None:
            return forbidden
        node_url = await _resolve_node(request, node)
        if node_url is None:
            return _problem(
                request, 400, "unknown-node", "Unknown Node", f"Node {node!r} is not configured."
            )
        try:
            resp = await manager_client.patch(node_url, "/v1/models/config", json=body)
        except Exception as exc:
            return _proxy_error_response(request, exc)
        return _passthrough(resp)

    async def _grant_holders(request: Request) -> dict[str, list[str]] | None:
        """model name -> client names holding a grant for it. None when
        auth-service could not be asked, which is never read as "nobody"."""
        settings: Settings = request.app.state.settings
        if not (settings.auth_service_admin_url and settings.auth_service_admin_api_key):
            return {}
        try:
            async with httpx.AsyncClient(
                timeout=10.0, verify=settings.auth_service_tls_verify
            ) as http_client:
                resp = await http_client.get(
                    f"{settings.auth_service_admin_url}/clients",
                    headers={"X-Admin-Key": settings.auth_service_admin_api_key},
                )
            if resp.status_code != 200:
                return None
            clients = resp.json()
        except Exception:
            return None
        holders: dict[str, list[str]] = {}
        for client in clients if isinstance(clients, list) else []:
            if not isinstance(client, dict):
                continue
            who = str(client.get("client_name") or client.get("client_id") or "?")
            for scope in client.get("allowed_scopes") or []:
                if isinstance(scope, str) and scope.startswith("model:"):
                    holders.setdefault(scope[len("model:") :], []).append(who)
        return holders

    async def _annotate_rename_blockers(request: Request, models: list[dict[str, Any]]) -> None:
        holders = await _grant_holders(request)
        table = pricing.get_pricing_table()
        for entry in models:
            model_id = str(entry.get("id") or "")
            slug = str(entry.get("slug") or model_id)
            names = {n for n in (model_id, slug) if n}
            blockers: list[str] = []
            if holders is None:
                blockers.append("auth-service could not be reached to check for model grants")
            else:
                who = sorted({w for n in names for w in holders.get(n, [])})
                if who:
                    blockers.append(f"held as a model grant by: {', '.join(who)}")
            try:
                rows = await db.count_usage_rows_for_model(model_id, slug)
            except Exception:
                rows = None
            if rows is None:
                blockers.append("usage history could not be checked")
            elif rows:
                blockers.append(f"{rows} usage row(s) billed under this name")
            if any(table.get_price(n) is not None for n in names):
                blockers.append("a price is configured for this name")
            entry["rename_blockers"] = blockers

    async def _current_slug(request: Request, node_url: str, model_id: str) -> str | None:
        """Today's slug, straight from the node — the rename is only checked
        when the name is actually changing."""
        try:
            resp = await manager_client.get(node_url, "/v1/models")
            body = resp.json()
        except Exception:
            return None
        entries = body if isinstance(body, list) else body.get("models", [])
        for entry in entries:
            if isinstance(entry, dict) and entry.get("id") == model_id:
                return str(entry.get("slug") or model_id)
        return None

    async def _slug_blockers(request: Request, model_id: str, current_slug: str) -> list[str]:
        """What would break if this model were renamed — PRM-113.

        RM-70 froze a slug permanently after one change, which made a typo
        permanent while a model nobody had touched was locked just as hard. The
        thing worth protecting is not "it was set once", it is "something
        depends on it", and three things can:

        - a `model:<slug>` grant held by a client, which would start returning
          403 on the new name,
        - usage rows already billed under that name,
        - a pricing.yaml entry keyed on it, whose price the model would stop
          finding — the failure that bills nothing while nothing errors.

        Only the gateway can see all three: grants live in auth-service and the
        other two in its own database. Returns a human-readable list, empty when
        renaming is safe.
        """
        blockers: list[str] = []
        names = {n for n in (model_id, current_slug) if n}

        # Grants — best effort. auth-service being unreachable must not be read
        # as "nothing depends on it": that would be the dangerous direction, so
        # an unreachable service blocks rather than waves through.
        settings: Settings = request.app.state.settings
        if settings.auth_service_admin_url and settings.auth_service_admin_api_key:
            try:
                async with httpx.AsyncClient(
                    timeout=10.0, verify=settings.auth_service_tls_verify
                ) as http_client:
                    resp = await http_client.get(
                        f"{settings.auth_service_admin_url}/clients",
                        headers={"X-Admin-Key": settings.auth_service_admin_api_key},
                    )
                clients = resp.json() if resp.status_code == 200 else None
            except Exception:
                clients = None
            if clients is None:
                blockers.append(
                    "auth-service could not be reached to check for model grants — refusing "
                    "rather than assuming nobody holds one"
                )
            else:
                holders = [
                    c.get("client_name") or c.get("client_id")
                    for c in clients
                    if isinstance(c, dict)
                    and any(f"model:{n}" in (c.get("allowed_scopes") or []) for n in names)
                ]
                if holders:
                    blockers.append(
                        f"{len(holders)} client(s) hold a model grant for this name: "
                        + ", ".join(str(h) for h in holders[:5])
                    )

        rows = await db.count_usage_rows_for_model(model_id, current_slug)
        if rows:
            blockers.append(f"{rows} usage row(s) are already billed under this name")

        table = pricing.get_pricing_table()
        if any(table.get_price(n) is not None for n in names):
            blockers.append("a price is configured for this name in the pricing table")

        return blockers

    @router.patch("/admin/api/nodes/{node}/catalog/{model_id}")
    async def update_catalog_entry(
        node: str, model_id: str, body: dict[str, Any], request: Request
    ) -> Response:
        """PRM-109: edit the model itself — its name and its modality.

        Separate from the instance PATCH below on purpose. Modality is a
        property of the weights, so every replica inherits one answer; editing
        it per instance let two replicas of one model route differently.
        """
        if (forbidden := _require_scope(request, "admin:write")) is not None:
            return forbidden
        node_url = await _resolve_node(request, node)
        if node_url is None:
            return _problem(
                request, 400, "unknown-node", "Unknown Node", f"Node {node!r} is not configured."
            )
        # PRM-113: renaming is allowed while nothing depends on the old name.
        if "slug" in body:
            current = await _current_slug(request, node_url, model_id)
            if current is not None and str(body["slug"]).strip() not in ("", current):
                blockers = await _slug_blockers(request, model_id, current)
                if blockers:
                    return _problem(
                        request,
                        409,
                        "slug-in-use",
                        "Name Already In Use",
                        f"{model_id!r} cannot be renamed from {current!r}: "
                        + "; ".join(blockers)
                        + ". Renaming would leave those behind — grants stop matching, history "
                        "splits in two, and a configured price stops being found.",
                    )

        try:
            resp = await manager_client.patch(node_url, f"/v1/models/{model_id}", json=body)
        except Exception as exc:
            return _proxy_error_response(request, exc)
        return _passthrough(resp)

    @router.patch("/admin/api/nodes/{node}/models/{model_id}")
    async def update_model(
        node: str, model_id: str, body: dict[str, Any], request: Request
    ) -> Response:
        if (forbidden := _require_scope(request, "admin:write")) is not None:
            return forbidden
        node_url = await _resolve_node(request, node)
        if node_url is None:
            return _problem(
                request, 400, "unknown-node", "Unknown Node", f"Node {node!r} is not configured."
            )

        try:
            resp = await manager_client.patch(node_url, f"/v1/backends/{model_id}", json=body)
        except Exception as exc:
            return _proxy_error_response(request, exc)
        return _passthrough(resp)

    @router.delete("/admin/api/nodes/{node}/models/{model_id}")
    async def deregister_model(node: str, model_id: str, request: Request) -> Response:
        if (forbidden := _require_scope(request, "admin:write")) is not None:
            return forbidden
        node_url = await _resolve_node(request, node)
        if node_url is None:
            return _problem(
                request, 400, "unknown-node", "Unknown Node", f"Node {node!r} is not configured."
            )

        try:
            resp = await manager_client.delete(node_url, f"/v1/backends/{model_id}")
        except Exception as exc:
            return _proxy_error_response(request, exc)
        return _passthrough(resp)

    # ── RM-48: Models — search Hugging Face, download, manage the lifecycle ──

    @router.get("/admin/api/nodes/{node}/models/search")
    async def search_models_proxy(
        node: str, request: Request, q: str, sort: str | None = None
    ) -> Response:
        if (forbidden := _require_scope(request, "admin:read")) is not None:
            return forbidden
        node_url = await _resolve_node(request, node)
        if node_url is None:
            return _problem(
                request, 400, "unknown-node", "Unknown Node", f"Node {node!r} is not configured."
            )
        params: dict[str, str] = {"q": q}
        if sort:
            params["sort"] = sort
        try:
            resp = await manager_client.get(node_url, "/v1/models/search", params=params)
        except Exception as exc:
            return _proxy_error_response(request, exc)
        return _passthrough(resp)

    @router.get("/admin/api/nodes/{node}/models/search/files")
    async def search_model_files_proxy(node: str, request: Request, repo_id: str) -> Response:
        if (forbidden := _require_scope(request, "admin:read")) is not None:
            return forbidden
        node_url = await _resolve_node(request, node)
        if node_url is None:
            return _problem(
                request, 400, "unknown-node", "Unknown Node", f"Node {node!r} is not configured."
            )
        try:
            resp = await manager_client.get(
                node_url, "/v1/models/search/files", params={"repo_id": repo_id}
            )
        except Exception as exc:
            return _proxy_error_response(request, exc)
        return _passthrough(resp)

    @router.get("/admin/api/nodes/{node}/models/search/card")
    async def search_model_card_proxy(node: str, request: Request, repo_id: str) -> Response:
        if (forbidden := _require_scope(request, "admin:read")) is not None:
            return forbidden
        node_url = await _resolve_node(request, node)
        if node_url is None:
            return _problem(
                request, 400, "unknown-node", "Unknown Node", f"Node {node!r} is not configured."
            )
        try:
            resp = await manager_client.get(
                node_url, "/v1/models/search/card", params={"repo_id": repo_id}
            )
        except Exception as exc:
            return _proxy_error_response(request, exc)
        return _passthrough(resp)

    @router.post("/admin/api/nodes/{node}/models/downloads")
    async def start_download_proxy(node: str, body: dict[str, Any], request: Request) -> Response:
        if (forbidden := _require_scope(request, "admin:write")) is not None:
            return forbidden
        node_url = await _resolve_node(request, node)
        if node_url is None:
            return _problem(
                request, 400, "unknown-node", "Unknown Node", f"Node {node!r} is not configured."
            )
        try:
            resp = await manager_client.post(node_url, "/v1/models/downloads", json=body)
        except Exception as exc:
            return _proxy_error_response(request, exc)
        return _passthrough(resp)

    @router.get("/admin/api/nodes/{node}/models/downloads")
    async def list_downloads_proxy(node: str, request: Request) -> Response:
        if (forbidden := _require_scope(request, "admin:read")) is not None:
            return forbidden
        node_url = await _resolve_node(request, node)
        if node_url is None:
            return _problem(
                request, 400, "unknown-node", "Unknown Node", f"Node {node!r} is not configured."
            )
        try:
            resp = await manager_client.get(node_url, "/v1/models/downloads")
        except Exception as exc:
            return _proxy_error_response(request, exc)
        return _passthrough(resp)

    # PRM-172: four explicit routes rather than one `{action}`. The verb was a path
    # parameter, so `http.route` — which is what an audit event's action is since
    # PRM-162, and what Argus groups by — put cancel, pause, resume and retry in one
    # bucket. Their own example of the use case was "how many deactivations this
    # week", and that could not be counted. Told to them in P-35; splitting is ours
    # and breaks nobody, because only the dashboard calls these.
    #
    # The unknown-action check went with it: a verb that is not a route is a 404
    # from the router, which is what the hand-rolled check was reproducing.
    async def _download_action(node: str, model_id: str, action: str, request: Request) -> Response:
        if (forbidden := _require_scope(request, "admin:write")) is not None:
            return forbidden
        node_url = await _resolve_node(request, node)
        if node_url is None:
            return _problem(
                request, 400, "unknown-node", "Unknown Node", f"Node {node!r} is not configured."
            )
        try:
            resp = await manager_client.post(node_url, f"/v1/models/downloads/{model_id}/{action}")
        except Exception as exc:
            return _proxy_error_response(request, exc)
        return _passthrough(resp)

    @router.post("/admin/api/nodes/{node}/models/downloads/{model_id}/cancel")
    async def cancel_download(node: str, model_id: str, request: Request) -> Response:
        return await _download_action(node, model_id, "cancel", request)

    @router.post("/admin/api/nodes/{node}/models/downloads/{model_id}/pause")
    async def pause_download(node: str, model_id: str, request: Request) -> Response:
        return await _download_action(node, model_id, "pause", request)

    @router.post("/admin/api/nodes/{node}/models/downloads/{model_id}/resume")
    async def resume_download(node: str, model_id: str, request: Request) -> Response:
        return await _download_action(node, model_id, "resume", request)

    @router.post("/admin/api/nodes/{node}/models/downloads/{model_id}/retry")
    async def retry_download(node: str, model_id: str, request: Request) -> Response:
        return await _download_action(node, model_id, "retry", request)

    @router.delete("/admin/api/nodes/{node}/models/{model_id}/downloaded")
    async def delete_downloaded_model_proxy(node: str, model_id: str, request: Request) -> Response:
        if (forbidden := _require_scope(request, "admin:write")) is not None:
            return forbidden
        node_url = await _resolve_node(request, node)
        if node_url is None:
            return _problem(
                request, 400, "unknown-node", "Unknown Node", f"Node {node!r} is not configured."
            )
        try:
            # RM-51: forward ?confirm=true — required by manager-api's cascade
            # delete whenever the model has running instances.
            resp = await manager_client.delete(
                node_url,
                f"/v1/models/{model_id}/downloaded",
                params=dict(request.query_params),
            )
        except Exception as exc:
            return _proxy_error_response(request, exc)
        return _passthrough(resp)

    # PRM-172: see the download actions above — three explicit routes, so starting,
    # stopping and restarting an instance are three countable actions instead of one.
    async def _control_instance(
        node: str, model_id: str, action: str, request: Request
    ) -> Response:
        if (forbidden := _require_scope(request, "admin:write")) is not None:
            return forbidden
        node_url = await _resolve_node(request, node)
        if node_url is None:
            return _problem(
                request, 400, "unknown-node", "Unknown Node", f"Node {node!r} is not configured."
            )

        try:
            # PRM-135: the one call that waits for a model to load. The manager
            # decides how long that may take (`start_timeout_s`, per backend);
            # this side only has to not give up first.
            resp = await manager_client.post(
                node_url, f"/v1/backends/{model_id}/{action}", timeout=_CONTROL_TIMEOUT_S
            )
        except Exception as exc:
            return _proxy_error_response(request, exc)
        return _passthrough(resp)

    @router.post("/admin/api/nodes/{node}/instances/{model_id}/start")
    async def start_instance(node: str, model_id: str, request: Request) -> Response:
        return await _control_instance(node, model_id, "start", request)

    @router.post("/admin/api/nodes/{node}/instances/{model_id}/stop")
    async def stop_instance(node: str, model_id: str, request: Request) -> Response:
        return await _control_instance(node, model_id, "stop", request)

    @router.post("/admin/api/nodes/{node}/instances/{model_id}/restart")
    async def restart_instance(node: str, model_id: str, request: Request) -> Response:
        return await _control_instance(node, model_id, "restart", request)

    @router.get("/admin/api/nodes/{node}/instances/{model_id}/logs")
    async def get_instance_logs(
        node: str, model_id: str, request: Request, tail: int = 200
    ) -> Response:
        """Tail a running instance's log file (RM-13)."""
        if (forbidden := _require_scope(request, "admin:read")) is not None:
            return forbidden
        node_url = await _resolve_node(request, node)
        if node_url is None:
            return _problem(
                request, 400, "unknown-node", "Unknown Node", f"Node {node!r} is not configured."
            )

        try:
            resp = await manager_client.get(
                node_url, f"/v1/backends/{model_id}/logs", params={"tail": tail}
            )
        except Exception as exc:
            return _proxy_error_response(request, exc)
        return _passthrough(resp)

    # ── Users — docs/roadmap.md RM-11 ─────────────────────────────────────────
    # Proxies to auth-service's /admin/clients/* using the static X-Admin-Key
    # that service requires (distinct from manager_client's OAuth2 flow).

    async def _auth_admin_request(
        request: Request,
        method: str,
        path: str,
        json: Any = None,
        params: dict[str, Any] | None = None,
    ) -> Response:
        settings: Settings = request.app.state.settings
        if not settings.auth_service_admin_url or not settings.auth_service_admin_api_key:
            return _problem(
                request,
                500,
                "not-configured",
                "Not Configured",
                "AUTH_SERVICE_ADMIN_URL / AUTH_SERVICE_ADMIN_API_KEY are not set on the "
                "gateway — the Users section cannot reach auth-service.",
            )
        try:
            async with httpx.AsyncClient(
                timeout=10.0, verify=settings.auth_service_tls_verify
            ) as http_client:
                resp = await http_client.request(
                    method,
                    f"{settings.auth_service_admin_url}{path}",
                    json=json,
                    params=params,
                    headers={"X-Admin-Key": settings.auth_service_admin_api_key},
                )
        except Exception as exc:
            return _proxy_error_response(request, exc)
        return _passthrough(resp)

    @router.get("/admin/api/users")
    async def list_users(request: Request) -> Response:
        if (forbidden := _require_scope(request, "admin:read")) is not None:
            return forbidden
        return await _auth_admin_request(request, "GET", "/clients")

    @router.post("/admin/api/users")
    async def create_user(body: dict[str, Any], request: Request) -> Response:
        if (forbidden := _require_scope(request, "admin:write")) is not None:
            return forbidden
        return await _auth_admin_request(request, "POST", "/clients", json=body)

    @router.patch("/admin/api/users/{client_id}")
    async def update_user(client_id: str, body: dict[str, Any], request: Request) -> Response:
        if (forbidden := _require_scope(request, "admin:write")) is not None:
            return forbidden
        return await _auth_admin_request(request, "PATCH", f"/clients/{client_id}", json=body)

    @router.delete("/admin/api/users/{client_id}")
    async def deactivate_user(
        client_id: str, request: Request, permanent: bool = False
    ) -> Response:
        """`?permanent=true` hard-deletes the user (RM-27); default is soft deactivate."""
        if (forbidden := _require_scope(request, "admin:write")) is not None:
            return forbidden
        return await _auth_admin_request(
            request, "DELETE", f"/clients/{client_id}", params={"permanent": permanent}
        )

    @router.post("/admin/api/users/{client_id}/reactivate")
    async def reactivate_user(client_id: str, request: Request) -> Response:
        if (forbidden := _require_scope(request, "admin:write")) is not None:
            return forbidden
        return await _auth_admin_request(request, "POST", f"/clients/{client_id}/reactivate")

    @router.post("/admin/api/users/{client_id}/rotate-secret")
    async def rotate_user_secret(client_id: str, request: Request) -> Response:
        if (forbidden := _require_scope(request, "admin:write")) is not None:
            return forbidden
        return await _auth_admin_request(request, "POST", f"/clients/{client_id}/rotate-secret")

    @router.post("/admin/api/users/{client_id}/reset-password")
    async def reset_user_password(client_id: str, request: Request) -> Response:
        if (forbidden := _require_scope(request, "admin:write")) is not None:
            return forbidden
        return await _auth_admin_request(request, "POST", f"/clients/{client_id}/reset-password")

    @router.post("/admin/api/users/{client_id}/share")
    async def share_user_credential(
        client_id: str, body: dict[str, Any], request: Request
    ) -> Response:
        if (forbidden := _require_scope(request, "admin:write")) is not None:
            return forbidden
        resp = await _auth_admin_request(request, "POST", f"/clients/{client_id}/share", json=body)
        return _rewrite_share_url(resp, request)

    # ── Nodes — RM-20, moved to the coordinator in PRM-134 ────────────────────
    #
    # These seven proxied to auth-service's /admin/nodes/* with a shared admin
    # key, because RM-20 had put fleet inventory in the identity service. They
    # now proxy to the manager-api whose manager.toml sets
    # `[fleet] coordinator = true`, with the same manager token everything else
    # in this file uses.
    #
    # The dashboard's paths do not change, so the SPA is untouched. What changes
    # is that listing nodes no longer requires auth-service to be reachable —
    # which is what emptied the catalog on 2026-09-26.
    #
    # Everything under /admin/api/nodes/{node}/... stays a per-node proxy to that
    # node's own manager-api: models, instances, catalog, downloads, search. Those
    # are node-local state and PRM-134 does not touch them.

    async def _fleet_request(
        request: Request, method: str, path: str, json: Any = None
    ) -> Response:
        """Proxy to the fleet coordinator, mirroring _auth_admin_request's shape."""
        settings: Settings = request.app.state.settings
        url = settings.resolved_fleet_url
        if not url:
            return _problem(
                request,
                503,
                "not-configured",
                "Not Configured",
                "No fleet coordinator is configured on this gateway — set "
                "MANAGER_FLEET_URL to the manager-api whose manager.toml has "
                "[fleet] coordinator = true.",
            )
        try:
            if method == "GET":
                resp = await manager_client.get(url, path)
            elif method == "POST":
                resp = await manager_client.post(url, path, json=json)
            elif method == "PATCH":
                resp = await manager_client.patch(url, path, json=json)
            elif method == "DELETE":
                resp = await manager_client.delete(url, path)
            else:  # pragma: no cover — every caller below is one of the four
                raise ValueError(f"unsupported method {method!r}")
        except Exception as exc:
            return _proxy_error_response(request, exc)
        return _passthrough(resp)

    @router.get("/admin/api/nodes")
    async def list_nodes(request: Request) -> Response:
        if (forbidden := _require_scope(request, "admin:read")) is not None:
            return forbidden
        return await _fleet_request(request, "GET", "/v1/fleet/nodes")

    @router.post("/admin/api/nodes")
    async def create_node(body: dict[str, Any], request: Request) -> Response:
        if (forbidden := _require_scope(request, "admin:write")) is not None:
            return forbidden
        return await _fleet_request(request, "POST", "/v1/fleet/nodes", json=body)

    @router.patch("/admin/api/nodes/{node_id}")
    async def update_node(node_id: str, body: dict[str, Any], request: Request) -> Response:
        if (forbidden := _require_scope(request, "admin:write")) is not None:
            return forbidden
        return await _fleet_request(request, "PATCH", f"/v1/fleet/nodes/{node_id}", json=body)

    @router.delete("/admin/api/nodes/{node_id}")
    async def delete_node(node_id: str, request: Request) -> Response:
        if (forbidden := _require_scope(request, "admin:write")) is not None:
            return forbidden
        return await _fleet_request(request, "DELETE", f"/v1/fleet/nodes/{node_id}")

    @router.post("/admin/api/nodes/{node_id}/check")
    async def check_node(node_id: str, request: Request) -> Response:
        if (forbidden := _require_scope(request, "admin:write")) is not None:
            return forbidden
        return await _fleet_request(request, "POST", f"/v1/fleet/nodes/{node_id}/check")

    @router.post("/admin/api/nodes/{node_id}/activate")
    async def activate_node(node_id: str, request: Request) -> Response:
        if (forbidden := _require_scope(request, "admin:write")) is not None:
            return forbidden
        return await _fleet_request(request, "POST", f"/v1/fleet/nodes/{node_id}/activate")

    @router.post("/admin/api/nodes/{node_id}/deactivate")
    async def deactivate_node(node_id: str, request: Request) -> Response:
        if (forbidden := _require_scope(request, "admin:write")) is not None:
            return forbidden
        return await _fleet_request(request, "POST", f"/v1/fleet/nodes/{node_id}/deactivate")

    @router.post("/admin/api/users/share/{token_id}/revoke")
    async def revoke_user_share(token_id: str, request: Request) -> Response:
        if (forbidden := _require_scope(request, "admin:write")) is not None:
            return forbidden
        return await _auth_admin_request(request, "POST", f"/clients/share/{token_id}/revoke")

    @router.get("/admin/api/config")
    async def get_dashboard_config(request: Request) -> Any:
        """Dashboard-facing settings — rate-limit and circuit-breaker config
        (RM-16). The rate-limit values here reflect whatever is live right
        now, including an admin override applied via PUT /admin/api/limits (RM-56);
        circuit-breaker settings remain .env-only.
        """
        if (forbidden := _require_scope(request, "admin:read")) is not None:
            return forbidden
        settings: Settings = request.app.state.settings
        return {
            "rate_limit_rpm": settings.rate_limit_rpm,
            "rate_limit_tpm": settings.rate_limit_tpm,
            "rate_limit_rpm_chat_completions": settings.rate_limit_rpm_chat_completions,
            "rate_limit_tpm_chat_completions": settings.rate_limit_tpm_chat_completions,
            "rate_limit_strict": settings.rate_limit_strict,
            "circuit_breaker_failure_threshold": settings.circuit_breaker_failure_threshold,
            "circuit_breaker_recovery_timeout": settings.circuit_breaker_recovery_timeout,
            "circuit_breaker_success_threshold": settings.circuit_breaker_success_threshold,
        }

    # ── RM-56: live-editable rate limits ─────────────────────────────────────

    def _observed_capacity(request: Request) -> dict[str, Any]:
        monitor = getattr(request.app.state, "health_monitor", None)
        if monitor is None:
            return {"slots": None, "reporting": 0}
        capacity: dict[str, Any] = dict(monitor.capacity())
        return capacity

    def _limits_payload(request: Request, *, is_overridden: bool) -> dict[str, Any]:
        settings: Settings = request.app.state.settings
        return {
            "limits": rate_limits.current_limits(settings),
            # PRM-229: and all of them, grouped by layer. `limits` above stays
            # exactly as it was — it is what the editable form posts back, and
            # widening it would have made the form's shape depend on a display
            # concern.
            "layers": rate_limits.limits_by_layer(
                settings, endpoint_count=len(INFERENCE_ENDPOINT_SLUGS)
            ),
            "env_defaults": request.app.state.rate_limit_env_defaults,
            # False = the .env values are in effect verbatim.
            "is_overridden": is_overridden,
            # Not editable here — changing fail-open/fail-closed behaviour is a
            # deployment decision, not a tuning knob.
            "rate_limit_strict": settings.rate_limit_strict,
            "min_admin_rpm": rate_limits.MIN_ADMIN_RPM,
            # PRM-233: the rate limit this page never showed.
            #
            # Every ceiling above is keyed on a credential, which means every
            # one of them applies *after* authentication. `/ui/login` is where
            # credentials are guessed rather than presented, and it has had a
            # per-IP throttle since spec 017 AC-11 — enforced, and absent from
            # the one page called Limits. Read-only here: it is not on
            # `RateLimitConfig`, and a login throttle an attacker could widen
            # by reaching the admin API is not obviously one worth making
            # editable from the admin API.
            "login_throttle": {
                "rpm": settings.ui_login_rate_limit_rpm,
                "active": settings.ui_enabled,
            },
            # RM-71 (decision #8): observed capacity shown next to the limit,
            # never used to derive one. Inferring a limit from slots, model size
            # and quantization is guesswork that throttles or over-admits in
            # silence; this just stops the operator setting the number blind.
            "capacity": _observed_capacity(request),
        }

    @router.get("/admin/api/limits")
    async def get_rate_limits(request: Request) -> Any:
        """Current effective limits plus what .env asked for — RM-56."""
        if (forbidden := _require_scope(request, "admin:read")) is not None:
            return forbidden
        row = await db.get_rate_limit_config()
        return _limits_payload(request, is_overridden=row is not None)

    @router.get("/admin/api/limits/live")
    async def get_live_limits(request: Request) -> Any:
        """Every counter standing this minute, against the ceiling it is
        measured by — PRM-230.

        The question a limits page is actually opened with is "something is
        being refused, which ceiling did it hit". Three conjoined layers and
        seven dimensions make that un-deducible from a settings screen: a
        caller sees one 429, and the reason is whichever of twenty-one
        counters crossed first. So this reports measurements, not
        configuration, and the configuration cards above it keep their job.

        Nothing here is a ceiling *forecast*. A row at 96% is a row that was
        at 96% when Redis was read, in a bucket that resets within the minute.
        """
        if (forbidden := _require_scope(request, "admin:read")) is not None:
            return forbidden
        redis_client = getattr(request.app.state, "shared_redis", None)
        if redis_client is None:
            # Not an error, and worth saying rather than rendering an empty
            # table that reads as quiet traffic. Only reachable in fail-open
            # mode: under `rate_limit_strict` the middleware 503s every path
            # including this one, so there is no page to put a message on.
            return {
                "available": False,
                "reason": "No rate-limit store configured — nothing is being counted or refused",
                "rows": [],
            }

        settings: Settings = request.app.state.settings
        limiter = RateLimiter(redis_client)
        try:
            counters = await limiter.live_counters()
        except Exception as exc:
            logger.warning("admin.live_limits_read_error", error=str(exc))
            return {
                "available": False,
                "reason": f"Could not read the rate-limit store: {exc}",
                "rows": [],
            }

        # One tier read per distinct consumer, and only for the rows a tier can
        # change — the client layer's own two dimensions. Cached in `budget`,
        # so a second row for the same client costs nothing.
        endpoint_count = len(INFERENCE_ENDPOINT_SLUGS)
        tiers: dict[str, Any] = {}
        for counter in counters:
            identity = counter["identity"]
            layer = rate_limits.counter_layer(identity, counter["endpoint"])
            if layer == "client" and identity not in tiers:
                try:
                    tiers[identity] = await resolve_client_limits(identity)
                except Exception:  # pragma: no cover — tier read is advisory
                    tiers[identity] = None

        rows = []
        for counter in counters:
            layer = rate_limits.counter_layer(counter["identity"], counter["endpoint"])
            limit, source = rate_limits.live_limit_for(
                settings,
                layer=layer,
                dimension=counter["dimension"],
                endpoint=counter["endpoint"],
                endpoint_count=endpoint_count,
                tier=tiers.get(counter["identity"]),
            )
            rows.append(
                {
                    **counter,
                    "layer": layer,
                    # PRM-234: and which of the three it is, from the server's
                    # own ordering — the page should not be counting layers.
                    "layer_n": rate_limits.LAYER_NUMBERS[layer],
                    "limit": limit,
                    "limit_source": source,
                    # Server-side so the sort below and the page agree. A
                    # counter with no ceiling has no percentage — not 0%,
                    # which would sort it with the idle ones and read as
                    # "plenty of room" rather than "nothing is watching".
                    "percent": round(counter["used"] / limit * 100, 1)
                    if limit not in (None, 0)
                    else None,
                }
            )
        # Closest to refusing first; unmetered counters last, where they read
        # as a finding rather than as headroom.
        rows.sort(key=lambda r: (r["percent"] is None, -(r["percent"] or 0)))
        return {
            "available": True,
            "reason": None,
            "rows": rows,
            "minute_resets_in": max(0, (int(_time.time()) // 60 + 1) * 60 - int(_time.time())),
        }

    @router.put("/admin/api/limits")
    async def put_rate_limits(body: dict[str, Any], request: Request) -> Any:
        """Persist limits and apply them to the live Settings object — RM-56.

        The rate-limit middleware re-reads Settings on every request, so the
        next request is already limited by the new values; the DB row is what
        makes that survive a restart.
        """
        if (forbidden := _require_scope(request, "admin:write")) is not None:
            return forbidden

        def _parse(key: str, *, required: bool) -> int | None:
            raw = body.get(key)
            if raw is None:
                if required:
                    raise ValueError(f"{key} is required.")
                return None
            if isinstance(raw, bool) or not isinstance(raw, (int, float)) or int(raw) != raw:
                raise ValueError(f"{key} must be a whole number.")
            value = int(raw)
            if value < 1:
                raise ValueError(f"{key} must be at least 1 — 0 would reject every request.")
            return value

        try:
            # PRM-232: one loop over the field list rather than seventeen
            # hand-written lines. The list is already the single source of what
            # is editable — `limits_by_layer` marks the page's fields from it
            # and a test asserts every name is a real column — so spelling the
            # names again here would be a third copy to keep in step.
            values: dict[str, int | None] = {
                field: _parse(field, required=field in rate_limits.REQUIRED_RATE_LIMIT_FIELDS)
                for field in rate_limits.RATE_LIMIT_FIELDS
                # Absent means "leave it as it is"; present and null means
                # "clear it". The form posts every field, but an API caller
                # editing one limit should not have to resend sixteen.
                if field in body or field in rate_limits.REQUIRED_RATE_LIMIT_FIELDS
            }
        except ValueError as exc:
            return _problem(request, 400, "invalid-limit", "Invalid Limit", str(exc))

        # The admin bucket is the operator's own way back in — see
        # rate_limits.MIN_ADMIN_RPM. Applies to whichever limit /admin/api/*
        # actually lands on: its own override, or the global when unset.
        effective_admin_rpm = values.get("rate_limit_rpm_admin") or values["rate_limit_rpm"]
        if effective_admin_rpm is not None and effective_admin_rpm < rate_limits.MIN_ADMIN_RPM:
            return _problem(
                request,
                400,
                "invalid-limit",
                "Invalid Limit",
                f"The admin API would be limited to {effective_admin_rpm} RPM, below the "
                f"{rate_limits.MIN_ADMIN_RPM} RPM floor. The dashboard polls several endpoints "
                "continuously, so a lower value would rate-limit the very page needed to undo "
                "it — leaving .env plus a restart as the only way back.",
            )

        await db.upsert_rate_limit_config(values)
        rate_limits.apply_limits(request.app.state.settings, values)
        logger.info("admin.rate_limits_updated", **{k: v for k, v in values.items()})
        return _limits_payload(request, is_overridden=True)

    @router.delete("/admin/api/limits")
    async def reset_rate_limits(request: Request) -> Any:
        """Drop the override and go back to what .env said — RM-56."""
        if (forbidden := _require_scope(request, "admin:write")) is not None:
            return forbidden
        await db.delete_rate_limit_config()
        rate_limits.apply_limits(
            request.app.state.settings, request.app.state.rate_limit_env_defaults
        )
        logger.info("admin.rate_limits_reset")
        return _limits_payload(request, is_overridden=False)

    # ── RM-67: live-editable circuit-breaker thresholds ──────────────────────

    _CB_FIELDS = (
        "circuit_breaker_failure_threshold",
        "circuit_breaker_recovery_timeout",
        "circuit_breaker_success_threshold",
    )
    # A typo here has a lasting, silent effect — a healthy backend stays cut
    # off for however long was entered — so cap it at an hour.
    _MAX_RECOVERY_TIMEOUT = 3600

    def _cb_payload(request: Request, *, is_overridden: bool) -> dict[str, Any]:
        settings: Settings = request.app.state.settings
        return {
            "settings": {field: getattr(settings, field) for field in _CB_FIELDS},
            "env_defaults": request.app.state.circuit_breaker_env_defaults,
            "is_overridden": is_overridden,
            "max_recovery_timeout": _MAX_RECOVERY_TIMEOUT,
        }

    @router.get("/admin/api/circuit-breaker")
    async def get_circuit_breaker_settings(request: Request) -> Any:
        """Current thresholds plus what .env asked for — RM-67."""
        if (forbidden := _require_scope(request, "admin:read")) is not None:
            return forbidden
        row = await db.get_circuit_breaker_config()
        return _cb_payload(request, is_overridden=row is not None)

    @router.put("/admin/api/circuit-breaker")
    async def put_circuit_breaker_settings(body: dict[str, Any], request: Request) -> Any:
        """Persist thresholds and push them into the live pool — RM-67.

        Unlike the rate limits, this can't rely on Settings being re-read per
        request: BackendPool and every CircuitBreaker it already built hold
        their own copies, so both are updated explicitly.
        """
        if (forbidden := _require_scope(request, "admin:write")) is not None:
            return forbidden

        def _parse(key: str) -> int:
            raw = body.get(key)
            if raw is None:
                raise ValueError(f"{key} is required.")
            if isinstance(raw, bool) or not isinstance(raw, (int, float)) or int(raw) != raw:
                raise ValueError(f"{key} must be a whole number.")
            value = int(raw)
            if value < 1:
                raise ValueError(f"{key} must be at least 1.")
            return value

        try:
            values = {field: _parse(field) for field in _CB_FIELDS}
        except ValueError as exc:
            return _problem(request, 400, "invalid-threshold", "Invalid Threshold", str(exc))

        if values["circuit_breaker_recovery_timeout"] > _MAX_RECOVERY_TIMEOUT:
            return _problem(
                request,
                400,
                "invalid-threshold",
                "Invalid Threshold",
                f"circuit_breaker_recovery_timeout is capped at {_MAX_RECOVERY_TIMEOUT} seconds "
                "— a longer value would keep a recovered backend cut off with nothing "
                "surfacing the mistake.",
            )

        await db.upsert_circuit_breaker_config(**values)
        settings: Settings = request.app.state.settings
        for field, value in values.items():
            setattr(settings, field, value)
        request.app.state.backend_pool.update_circuit_breaker_settings(
            failure_threshold=values["circuit_breaker_failure_threshold"],
            recovery_timeout=values["circuit_breaker_recovery_timeout"],
            success_threshold=values["circuit_breaker_success_threshold"],
        )
        logger.info("admin.circuit_breaker_updated", **values)
        return _cb_payload(request, is_overridden=True)

    @router.delete("/admin/api/circuit-breaker")
    async def reset_circuit_breaker_settings(request: Request) -> Any:
        """Drop the override and go back to what .env said — RM-67."""
        if (forbidden := _require_scope(request, "admin:write")) is not None:
            return forbidden
        await db.delete_circuit_breaker_config()
        defaults = request.app.state.circuit_breaker_env_defaults
        settings: Settings = request.app.state.settings
        for field, value in defaults.items():
            setattr(settings, field, value)
        request.app.state.backend_pool.update_circuit_breaker_settings(
            failure_threshold=defaults["circuit_breaker_failure_threshold"],
            recovery_timeout=defaults["circuit_breaker_recovery_timeout"],
            success_threshold=defaults["circuit_breaker_success_threshold"],
        )
        logger.info("admin.circuit_breaker_reset")
        return _cb_payload(request, is_overridden=False)

    @router.get("/admin/api/sessions")
    async def list_sessions(request: Request) -> Any:
        """Clients active in the last 15 minutes (RM-23) — a last-seen-based
        approximation, not a real connection registry. See ActivityTracker.

        Superseded by /admin/api/activity (PRM-235) and kept because it is a
        published endpoint: the dashboard no longer calls it, and removing it
        is a separate decision from replacing the page.
        """
        if (forbidden := _require_scope(request, "admin:read")) is not None:
            return forbidden
        return {"sessions": await activity_tracker.snapshot()}

    def _platform_cell(
        settings: Settings, live: list[dict[str, Any]], dimension: str, endpoint_count: int
    ) -> dict[str, Any] | None:
        """The platform's own counter for one dimension — PRM-235.

        Carries `percent` like every other cell on this page, and it is the
        reason this is a function: assembled inline it was the one cell built
        without it, which the page rendered as `undefined%`. Usually None here,
        because the platform layer is opt-in and nothing sets a ceiling on it —
        a counter with no ceiling has no percentage, not a zero one.
        """
        counter = next(
            (c for c in live if c["identity"] == PLATFORM_IDENTITY and c["dimension"] == dimension),
            None,
        )
        if counter is None:
            return None
        limit, source = rate_limits.live_limit_for(
            settings,
            layer="platform",
            dimension=dimension,
            endpoint=counter["endpoint"],
            endpoint_count=endpoint_count,
        )
        return {
            **counter,
            "layer": "platform",
            "limit": limit,
            "limit_source": source,
            "percent": round(counter["used"] / limit * 100, 1) if limit not in (None, 0) else None,
        }

    @router.get("/admin/api/activity")
    async def get_activity(request: Request) -> Any:
        """Who is consuming the platform, and how much — PRM-235.

        Replaces the Sessions page, which listed presence and nothing else.
        Three sources, because no one of them answers the question:

        * **Redis counters** (PRM-230) — what a consumer is spending *this
          minute*, and how close that is to each ceiling. Survives a gateway
          restart, which is the reason they lead rather than the tracker.
        * **`usage_events`** — today's requests, tokens and cost, per consumer
          and per end user. The only source that knows about end users.
        * **`ActivityTracker`** — last-seen and the kind of route last called.
          In-process, so a restart empties it; a consumer with Redis or
          database activity and no tracker entry is reported as such rather
          than dropped. A page that goes blank after a deploy says "nobody is
          using this platform", which is the mistake PRM-223 fixed elsewhere.

        Top-N by request rate, with the rest counted but not listed: the
        practice is explicit that per-consumer series are high-cardinality, and
        LiteLLM does not even export end users to Prometheus by default.
        """
        if (forbidden := _require_scope(request, "admin:read")) is not None:
            return forbidden
        settings: Settings = request.app.state.settings
        endpoint_count = len(INFERENCE_ENDPOINT_SLUGS)

        # ── live counters ────────────────────────────────────────────────────
        live: list[dict[str, Any]] = []
        redis_client = getattr(request.app.state, "shared_redis", None)
        if redis_client is not None:
            try:
                live = await RateLimiter(redis_client).live_counters()
            except Exception as exc:  # pragma: no cover — advisory, never fatal
                logger.warning("admin.activity_live_read_error", error=str(exc))

        # ── today, from the usage rows ───────────────────────────────────────
        today_rows: list[dict[str, Any]] = []
        try:
            today_rows = await db.query_activity_today(_dt.date.today())
        except Exception as exc:  # pragma: no cover — advisory, never fatal
            logger.warning("admin.activity_usage_read_error", error=str(exc))

        # ── presence ─────────────────────────────────────────────────────────
        seen = {e["client_id"]: e for e in await activity_tracker.snapshot()}

        consumers: dict[str, dict[str, Any]] = {}

        def _consumer(identity: str) -> dict[str, Any]:
            if identity not in consumers:
                tracked = seen.get(identity)
                consumers[identity] = {
                    "identity": identity,
                    # PRM-237: whether this row is the credential reading the
                    # page. The dashboard polls several endpoints every few
                    # seconds, so an operator who has only opened Activity
                    # finds themselves at the top of it with hundreds of
                    # requests — measured: 248 in 15 minutes from one tab. The
                    # traffic is real and belongs here; what was missing was
                    # the page saying whose it is.
                    "is_you": identity == getattr(_claims(request), "client_id", None),
                    "last_seen_ago_s": tracked["last_seen_ago_s"] if tracked else None,
                    "connection_type": tracked["connection_type"] if tracked else None,
                    # PRM-236: what this credential has been doing, from the
                    # tracker — inference, listing its models, the dashboard,
                    # the Playground. Empty when the tracker has no entry,
                    # which after a restart is every consumer, and is why the
                    # page says that rather than showing an empty list as
                    # "did nothing".
                    "actions": tracked["actions"] if tracked else [],
                    "rpm": None,
                    "tpm": None,
                    "worst": None,
                    "today": {"request_count": 0, "total_tokens": 0, "cost_usd": 0.0},
                    "end_users": [],
                    "models": [],
                }
            return consumers[identity]

        tiers: dict[str, Any] = {}
        for counter in live:
            identity = counter["identity"]
            if identity == PLATFORM_IDENTITY:
                continue
            layer = rate_limits.counter_layer(identity, counter["endpoint"])
            if layer == "client" and identity not in tiers:
                try:
                    tiers[identity] = await resolve_client_limits(identity)
                except Exception:  # pragma: no cover — tier read is advisory
                    tiers[identity] = None
            limit, source = rate_limits.live_limit_for(
                settings,
                layer=layer,
                dimension=counter["dimension"],
                endpoint=counter["endpoint"],
                endpoint_count=endpoint_count,
                tier=tiers.get(identity),
            )
            percent = round(counter["used"] / limit * 100, 1) if limit not in (None, 0) else None
            cell = {
                **counter,
                "layer": layer,
                "limit": limit,
                "limit_source": source,
                "percent": percent,
            }
            entry = _consumer(identity)
            if layer == "client" and counter["dimension"] in ("rpm", "tpm"):
                entry[counter["dimension"]] = cell
            if percent is not None and (
                entry["worst"] is None or percent > entry["worst"]["percent"]
            ):
                entry["worst"] = cell

        # PRM-236: the usage rows group by model and kind as well now, so the
        # per-consumer and per-end-user totals are rolled up here rather than
        # asked for a second time. Two dictionaries, keyed the way the page
        # reads them: "who did this client serve" and "what did it run".
        per_user: dict[tuple[str, str | None], dict[str, Any]] = {}
        per_model: dict[tuple[str, str | None, str], dict[str, Any]] = {}
        for row in today_rows:
            entry = _consumer(row["client_id"])
            entry["today"]["request_count"] += row["request_count"]
            entry["today"]["total_tokens"] += row["total_tokens"]
            entry["today"]["cost_usd"] += row["cost_usd"] or 0.0

            user_key = (row["client_id"], row["end_user"])
            user_entry = per_user.get(user_key)
            if user_entry is None:
                user_entry = per_user[user_key] = {
                    "end_user": row["end_user"],
                    "request_count": 0,
                    "total_tokens": 0,
                    "cost_usd": 0.0,
                    "last_seen": row["last_seen"],
                    "models": [],
                }
                entry["end_users"].append(user_entry)
            user_entry["request_count"] += row["request_count"]
            user_entry["total_tokens"] += row["total_tokens"]
            user_entry["cost_usd"] += row["cost_usd"] or 0.0
            if row["last_seen"] and (
                user_entry["last_seen"] is None or row["last_seen"] > user_entry["last_seen"]
            ):
                user_entry["last_seen"] = row["last_seen"]
            user_entry["models"].append(
                {
                    "model": row["model"],
                    "request_kind": row["request_kind"],
                    "request_count": row["request_count"],
                }
            )

            model_key = (row["client_id"], row["model"], row["request_kind"])
            model_entry = per_model.get(model_key)
            if model_entry is None:
                model_entry = per_model[model_key] = {
                    "model": row["model"],
                    "request_kind": row["request_kind"],
                    "request_count": 0,
                    "total_tokens": 0,
                    "cost_usd": 0.0,
                }
                entry["models"].append(model_entry)
            model_entry["request_count"] += row["request_count"]
            model_entry["total_tokens"] += row["total_tokens"]
            model_entry["cost_usd"] += row["cost_usd"] or 0.0

        for identity in seen:
            _consumer(identity)

        for entry in consumers.values():
            # Named end users first, then the unnamed remainder — which is one
            # row and is usually the biggest, so sorting it by size would bury
            # every real end user beneath it.
            entry["end_users"].sort(key=lambda u: (u["end_user"] is None, -u["request_count"]))
            entry["models"].sort(key=lambda m: -m["request_count"])
            for user_entry in entry["end_users"]:
                user_entry["models"].sort(key=lambda m: -m["request_count"])

        ordered = sorted(
            consumers.values(),
            key=lambda c: (
                -(c["worst"]["percent"] if c["worst"] else -1),
                -c["today"]["request_count"],
                c["last_seen_ago_s"] if c["last_seen_ago_s"] is not None else 1 << 30,
            ),
        )
        # PRM-236: and the same day seen from the other side. "Which end users
        # interacted today" is its own question — one person can appear under
        # several consumers, and a list nested inside each consumer cannot be
        # read across them.
        end_users_today: dict[str, dict[str, Any]] = {}
        for row in today_rows:
            if row["end_user"] is None:
                continue
            seen_user = end_users_today.setdefault(
                row["end_user"],
                {
                    "end_user": row["end_user"],
                    "request_count": 0,
                    "total_tokens": 0,
                    "cost_usd": 0.0,
                    "consumers": [],
                    "models": [],
                },
            )
            seen_user["request_count"] += row["request_count"]
            seen_user["total_tokens"] += row["total_tokens"]
            seen_user["cost_usd"] += row["cost_usd"] or 0.0
            if row["client_id"] not in seen_user["consumers"]:
                seen_user["consumers"].append(row["client_id"])
            if row["model"] and row["model"] not in seen_user["models"]:
                seen_user["models"].append(row["model"])

        return {
            "end_users_today": sorted(end_users_today.values(), key=lambda u: -u["request_count"])[
                :_ACTIVITY_TOP_N
            ],
            "consumers": ordered[:_ACTIVITY_TOP_N],
            "omitted": max(0, len(ordered) - _ACTIVITY_TOP_N),
            "tracker_window_minutes": activity_tracker._STALE_AFTER_S // 60,
            "platform": {
                dimension: _platform_cell(settings, live, dimension, endpoint_count)
                for dimension in ("rpm", "tpm")
            },
        }

    return router
