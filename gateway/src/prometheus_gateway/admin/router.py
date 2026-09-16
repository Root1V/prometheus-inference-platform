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

Implements: docs/roadmap.md — RM-10 (gateway admin dashboard, phase 1)
Implements: docs/roadmap.md — RM-11 (Users section, dual login modes)
Implements: docs/roadmap.md — RM-20 (Nodes section, replaces static MANAGER_NODES)
Implements: docs/roadmap.md — RM-13 (admin dashboard: live log viewer)
Implements: docs/roadmap.md — RM-16 (routing & rate-limit visibility)
Implements: docs/roadmap.md — RM-23 (active sessions / connected users)
Implements: docs/roadmap.md — RM-48 (Models: discover/download/manage on Hugging Face)
"""

from __future__ import annotations

import json
from typing import Any

import httpx
from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from .. import db, pricing, rate_limits
from ..config import Settings
from ..router import _problem
from ..telemetry import activity_tracker, get_logger
from .client import ManagerApiClient
from .nodes_client import fetch_nodes

logger = get_logger(__name__)


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

    RM-20: node topology lives in auth-service's node registry, fetched fresh on
    every call (admin-only, low-QPS path — not the hot inference request path).
    """
    settings: Settings = request.app.state.settings
    nodes = await fetch_nodes(
        settings.auth_service_admin_url,  # type: ignore[arg-type]
        settings.auth_service_admin_api_key,  # type: ignore[arg-type]
        tls_verify=settings.auth_service_tls_verify,
    )
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

        if resp.status_code != 200:
            # auth-service returns standard OAuth2 error bodies
            # ({"error": ..., "error_description": ...}), not the manager-api
            # shape _passthrough() understands — normalize separately here.
            try:
                oauth_error = resp.json()
            except Exception:
                oauth_error = {}
            return _problem(
                request,
                401 if resp.status_code in (400, 401) else 502,
                "invalid-credentials",
                "Invalid Credentials",
                oauth_error.get("error_description")
                or oauth_error.get("error")
                or "The auth-service rejected these credentials.",
            )
        body_json = resp.json()
        return JSONResponse(
            content={
                "access_token": body_json.get("access_token"),
                "expires_in": body_json.get("expires_in"),
            }
        )

    @router.get("/admin/api/instances")
    async def list_instances(request: Request) -> Any:
        if (forbidden := _require_scope(request, "admin:read")) is not None:
            return forbidden

        settings: Settings = request.app.state.settings
        instances: list[dict[str, Any]] = []
        unreachable_nodes: list[str] = []

        nodes = await fetch_nodes(
            settings.auth_service_admin_url,  # type: ignore[arg-type]
            settings.auth_service_admin_api_key,  # type: ignore[arg-type]
            tls_verify=settings.auth_service_tls_verify,
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
            settings.auth_service_admin_url,  # type: ignore[arg-type]
            settings.auth_service_admin_api_key,  # type: ignore[arg-type]
            tls_verify=settings.auth_service_tls_verify,
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

    @router.post("/admin/api/nodes/{node}/models/downloads/{model_id}/{action}")
    async def download_action_proxy(
        node: str, model_id: str, action: str, request: Request
    ) -> Response:
        if action not in ("cancel", "pause", "resume", "retry"):
            return _problem(request, 404, "not-found", "Not Found", f"Unknown action {action!r}.")
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

    @router.post("/admin/api/nodes/{node}/instances/{model_id}/{action}")
    async def control_instance(node: str, model_id: str, action: str, request: Request) -> Response:
        if action not in ("start", "stop", "restart"):
            return _problem(request, 404, "not-found", "Not Found", f"Unknown action {action!r}.")
        if (forbidden := _require_scope(request, "admin:write")) is not None:
            return forbidden
        node_url = await _resolve_node(request, node)
        if node_url is None:
            return _problem(
                request, 400, "unknown-node", "Unknown Node", f"Node {node!r} is not configured."
            )

        try:
            resp = await manager_client.post(node_url, f"/v1/backends/{model_id}/{action}")
        except Exception as exc:
            return _proxy_error_response(request, exc)
        return _passthrough(resp)

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

    # ── Nodes — docs/roadmap.md RM-20 ─────────────────────────────────────────
    # Proxies to auth-service's /admin/nodes/* — same X-Admin-Key pattern as Users.

    @router.get("/admin/api/nodes")
    async def list_nodes(request: Request) -> Response:
        if (forbidden := _require_scope(request, "admin:read")) is not None:
            return forbidden
        return await _auth_admin_request(request, "GET", "/nodes")

    @router.post("/admin/api/nodes")
    async def create_node(body: dict[str, Any], request: Request) -> Response:
        if (forbidden := _require_scope(request, "admin:write")) is not None:
            return forbidden
        return await _auth_admin_request(request, "POST", "/nodes", json=body)

    @router.patch("/admin/api/nodes/{node_id}")
    async def update_node(node_id: str, body: dict[str, Any], request: Request) -> Response:
        if (forbidden := _require_scope(request, "admin:write")) is not None:
            return forbidden
        return await _auth_admin_request(request, "PATCH", f"/nodes/{node_id}", json=body)

    @router.delete("/admin/api/nodes/{node_id}")
    async def delete_node(node_id: str, request: Request) -> Response:
        if (forbidden := _require_scope(request, "admin:write")) is not None:
            return forbidden
        return await _auth_admin_request(request, "DELETE", f"/nodes/{node_id}")

    @router.post("/admin/api/nodes/{node_id}/check")
    async def check_node(node_id: str, request: Request) -> Response:
        if (forbidden := _require_scope(request, "admin:write")) is not None:
            return forbidden
        return await _auth_admin_request(request, "POST", f"/nodes/{node_id}/check")

    @router.post("/admin/api/nodes/{node_id}/activate")
    async def activate_node(node_id: str, request: Request) -> Response:
        if (forbidden := _require_scope(request, "admin:write")) is not None:
            return forbidden
        return await _auth_admin_request(request, "POST", f"/nodes/{node_id}/activate")

    @router.post("/admin/api/nodes/{node_id}/deactivate")
    async def deactivate_node(node_id: str, request: Request) -> Response:
        if (forbidden := _require_scope(request, "admin:write")) is not None:
            return forbidden
        return await _auth_admin_request(request, "POST", f"/nodes/{node_id}/deactivate")

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
            "env_defaults": request.app.state.rate_limit_env_defaults,
            # False = the .env values are in effect verbatim.
            "is_overridden": is_overridden,
            # Not editable here — changing fail-open/fail-closed behaviour is a
            # deployment decision, not a tuning knob.
            "rate_limit_strict": settings.rate_limit_strict,
            "min_admin_rpm": rate_limits.MIN_ADMIN_RPM,
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
            values: dict[str, int | None] = {
                "rate_limit_rpm": _parse("rate_limit_rpm", required=True),
                "rate_limit_tpm": _parse("rate_limit_tpm", required=True),
                "rate_limit_rpm_chat_completions": _parse(
                    "rate_limit_rpm_chat_completions", required=False
                ),
                "rate_limit_tpm_chat_completions": _parse(
                    "rate_limit_tpm_chat_completions", required=False
                ),
                "rate_limit_rpm_admin": _parse("rate_limit_rpm_admin", required=False),
                "rate_limit_tpm_admin": _parse("rate_limit_tpm_admin", required=False),
            }
        except ValueError as exc:
            return _problem(request, 400, "invalid-limit", "Invalid Limit", str(exc))

        # The admin bucket is the operator's own way back in — see
        # rate_limits.MIN_ADMIN_RPM. Applies to whichever limit /admin/api/*
        # actually lands on: its own override, or the global when unset.
        effective_admin_rpm = values["rate_limit_rpm_admin"] or values["rate_limit_rpm"]
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

        await db.upsert_rate_limit_config(
            rate_limit_rpm=values["rate_limit_rpm"],  # type: ignore[arg-type]
            rate_limit_tpm=values["rate_limit_tpm"],  # type: ignore[arg-type]
            rate_limit_rpm_chat_completions=values["rate_limit_rpm_chat_completions"],
            rate_limit_tpm_chat_completions=values["rate_limit_tpm_chat_completions"],
            rate_limit_rpm_admin=values["rate_limit_rpm_admin"],
            rate_limit_tpm_admin=values["rate_limit_tpm_admin"],
        )
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
        """
        if (forbidden := _require_scope(request, "admin:read")) is not None:
            return forbidden
        return {"sessions": await activity_tracker.snapshot()}

    return router
