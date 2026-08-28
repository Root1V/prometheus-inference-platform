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
GET    /admin/api/config                                    — dashboard-facing settings (RM-31)
GET    /admin/api/sessions                                  — clients active in the last 15m (RM-23)

Implements: docs/roadmap.md — RM-10 (gateway admin dashboard, phase 1)
Implements: docs/roadmap.md — RM-11 (Users section, dual login modes)
Implements: docs/roadmap.md — RM-20 (Nodes section, replaces static MANAGER_NODES)
Implements: docs/roadmap.md — RM-31 (Overview: link out to Grafana/Tempo)
Implements: docs/roadmap.md — RM-13 (admin dashboard: live log viewer)
Implements: docs/roadmap.md — RM-16 (routing & rate-limit visibility)
Implements: docs/roadmap.md — RM-23 (active sessions / connected users)
"""

from __future__ import annotations

from typing import Any

import httpx
from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

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
        return await _auth_admin_request(request, "POST", f"/clients/{client_id}/share", json=body)

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
        """Dashboard-facing settings — Grafana link (RM-31), rate-limit/circuit-breaker
        config (RM-16). Read-only: live-editing these stays out of scope — .env remains
        the single source of truth.
        """
        if (forbidden := _require_scope(request, "admin:read")) is not None:
            return forbidden
        settings: Settings = request.app.state.settings
        return {
            "grafana_url": settings.grafana_url,
            "rate_limit_rpm": settings.rate_limit_rpm,
            "rate_limit_tpm": settings.rate_limit_tpm,
            "rate_limit_rpm_chat_completions": settings.rate_limit_rpm_chat_completions,
            "rate_limit_tpm_chat_completions": settings.rate_limit_tpm_chat_completions,
            "rate_limit_strict": settings.rate_limit_strict,
            "circuit_breaker_failure_threshold": settings.circuit_breaker_failure_threshold,
            "circuit_breaker_recovery_timeout": settings.circuit_breaker_recovery_timeout,
            "circuit_breaker_success_threshold": settings.circuit_breaker_success_threshold,
        }

    @router.get("/admin/api/sessions")
    async def list_sessions(request: Request) -> Any:
        """Clients active in the last 15 minutes (RM-23) — a last-seen-based
        approximation, not a real connection registry. See ActivityTracker.
        """
        if (forbidden := _require_scope(request, "admin:read")) is not None:
            return forbidden
        return {"sessions": await activity_tracker.snapshot()}

    return router
