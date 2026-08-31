"""Write endpoints for the Prometheus Manager REST API — RM-10.

POST   /v1/backends                  — register a model
PATCH  /v1/backends/{model_id}       — update a registered model's fields
DELETE /v1/backends/{model_id}       — deregister (stops it first if running)
POST   /v1/backends/{model_id}/start
POST   /v1/backends/{model_id}/stop
POST   /v1/backends/{model_id}/restart

All require `backend-registry:write`. Read-only /v1/backends[/{id}] in
routes.py is unaffected — this only adds mutation endpoints.

Implements: docs/roadmap.md — RM-10 (gateway admin dashboard, phase 1)
"""

from __future__ import annotations

import asyncio
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from opentelemetry.trace import SpanKind
from prometheus_manager_core.config import ManagerConfig
from prometheus_manager_core.lifecycle import (
    LifecycleError,
    deregister_instance,
    restart_instance,
    start_instance,
    stop_instance,
)
from prometheus_manager_core.registry import (
    Registry,
    RegistryEntry,
    _validate_backend,
    _validate_modality,
    _validate_path,
    _validate_port,
)
from prometheus_manager_core.scanner import scan
from prometheus_manager_core.telemetry import get_tracer

from .auth import require_backend_registry_write
from .routes import _merge

# Fields an operator may PATCH — everything except `id` (the registry key —
# renaming would mean remove+re-add, not an in-place update) and
# `hf_filenames` (sharded-download internal detail, not surfaced in the
# admin dashboard's edit form).
_UPDATABLE_FIELDS = frozenset(
    {
        "path",
        "context_length",
        "family",
        "quantization",
        "backend",
        "modality",
        "mmproj_path",
        "discovery",
        "hf_repo",
        "hf_sha256",
        "port",
    }
)

router = APIRouter()

Claims = dict[str, Any]

_tracer = get_tracer("manager.api")


def _problem(status: int, error_type: str, title: str, detail: str) -> HTTPException:
    return HTTPException(
        status_code=status,
        detail={
            "type": f"https://prometheus.local/errors/{error_type}",
            "title": title,
            "status": status,
            "detail": detail,
        },
    )


@router.post("/v1/backends", tags=["backends"], status_code=201)
async def register_backend(
    body: dict[str, Any],
    request: Request,
    _claims: Annotated[Claims, Depends(require_backend_registry_write)],
) -> dict[str, Any]:
    """Register a new model — mirrors `pmgr register`.

    Body is a plain dict, validated field-by-field by RegistryEntry's own
    validators (_validate_id/_validate_backend/_validate_modality/_validate_path/
    _validate_port) below — avoids a second, drifting Pydantic schema.

    Implements: memory/specs/008-llama-server-manager.md — AC-3, AC-16, AC-17
    Implements: docs/roadmap.md — RM-10
    """
    with _tracer.start_as_current_span("backend.register", kind=SpanKind.INTERNAL) as span:
        registry: Registry = request.app.state.registry
        model_id = body.get("id", "")
        span.set_attribute("model_id", model_id)

        try:
            entry: RegistryEntry = RegistryEntry(
                id=model_id,
                port=int(body.get("port", 0)),
                context_length=int(body.get("context_length", 4096)),
                path=body.get("path", ""),
                family=body.get("family", ""),
                quantization=body.get("quantization", ""),
                backend=body.get("backend", "llama_cpp"),
                modality=body.get("modality", "text"),
                mmproj_path=body.get("mmproj_path", ""),
                discovery=bool(body.get("discovery", False)),
                hf_repo=body.get("hf_repo", ""),
                hf_sha256=body.get("hf_sha256", ""),
                hf_filenames=body.get("hf_filenames", []),
            )
            registry.add(entry)
        except (ValueError, TypeError) as exc:
            span.set_attribute("http.status_code", 400)
            raise _problem(400, "invalid-registration", "Invalid Registration", str(exc)) from exc

        span.set_attribute("http.status_code", 201)
        result: dict[str, Any] = entry.to_dict()
        return result


@router.patch("/v1/backends/{model_id}", tags=["backends"])
async def update_backend(
    model_id: str,
    body: dict[str, Any],
    request: Request,
    _claims: Annotated[Claims, Depends(require_backend_registry_write)],
) -> dict[str, Any]:
    """Update one or more fields of an already-registered model.

    Partial update — only keys present in the body are changed; `id` cannot
    be changed this way (it's the registry key). Validated with the same
    RegistryEntry validators register_backend uses, applied to the
    *resulting* merged values so e.g. changing only `backend` still
    re-validates `path` against the new backend.

    Implements: docs/roadmap.md — RM-10
    """
    with _tracer.start_as_current_span("backend.update", kind=SpanKind.INTERNAL) as span:
        span.set_attribute("model_id", model_id)
        registry: Registry = request.app.state.registry

        entry = registry.get(model_id)
        if entry is None:
            span.set_attribute("http.status_code", 404)
            raise _problem(404, "not-found", "Not Found", f"Model {model_id!r} not registered.")

        updates = {k: v for k, v in body.items() if k in _UPDATABLE_FIELDS}
        merged_backend = updates.get("backend", entry.backend)
        merged_path = updates.get("path", entry.path)
        merged_port = updates.get("port", entry.port)
        merged_modality = updates.get("modality", entry.modality)

        try:
            _validate_backend(merged_backend)
            _validate_modality(merged_modality)
            _validate_path(merged_path, merged_backend)
            _validate_port(int(merged_port))
        except (ValueError, TypeError) as exc:
            span.set_attribute("http.status_code", 400)
            raise _problem(400, "invalid-update", "Invalid Update", str(exc)) from exc

        registry.update(model_id, **updates)
        span.set_attribute("http.status_code", 200)
        updated = registry.get(model_id)
        assert updated is not None  # just updated above
        result: dict[str, Any] = updated.to_dict()
        return result


@router.delete("/v1/backends/{model_id}", tags=["backends"], status_code=204)
async def deregister_backend(
    model_id: str,
    request: Request,
    _claims: Annotated[Claims, Depends(require_backend_registry_write)],
) -> Response:
    """Stop (if running) and remove a model from the registry.

    Implements: memory/specs/008-llama-server-manager.md — AC-6d
    Implements: docs/roadmap.md — RM-10
    """
    with _tracer.start_as_current_span("backend.deregister", kind=SpanKind.INTERNAL) as span:
        span.set_attribute("model_id", model_id)
        registry: Registry = request.app.state.registry
        config: ManagerConfig = request.app.state.config

        if registry.get(model_id) is None:
            span.set_attribute("http.status_code", 404)
            raise _problem(404, "not-found", "Not Found", f"Model {model_id!r} not registered.")

        await asyncio.to_thread(deregister_instance, model_id, config, registry)
        span.set_attribute("http.status_code", 204)
        return Response(status_code=204)


async def _control_action(action: str, model_id: str, request: Request) -> dict[str, Any]:
    registry: Registry = request.app.state.registry
    config: ManagerConfig = request.app.state.config
    pid_dir = request.app.state.pid_dir
    proxy_host = getattr(request.app.state, "proxy_host", "")

    if registry.get(model_id) is None:
        raise _problem(404, "not-found", "Not Found", f"Model {model_id!r} not registered.")

    try:
        if action == "start":
            await asyncio.to_thread(start_instance, model_id, config, registry)
        elif action == "stop":
            await asyncio.to_thread(stop_instance, model_id, config, registry)
        else:
            await asyncio.to_thread(restart_instance, model_id, config, registry)
    except LifecycleError as exc:
        raise _problem(409, "lifecycle-conflict", "Lifecycle Conflict", str(exc)) from exc
    except OSError as exc:
        # subprocess.Popen-level failure (binary not found, permission denied,
        # etc.) — surfaced as a raw 500 with no detail otherwise. Caught here
        # rather than at a global handler so the admin dashboard shows the
        # operator something actionable instead of "Request failed with
        # status code 500".
        raise _problem(
            500,
            "backend-launch-error",
            "Backend Launch Error",
            f"Could not {action} {model_id!r}: {exc}",
        ) from exc

    registry.reload()
    entry = registry.get(model_id)
    live = {
        proc.model_id: proc
        for proc in await asyncio.to_thread(scan, pid_dir, {model_id})
        if proc.model_id
    }
    assert entry is not None  # just validated above
    return _merge(entry.__dict__, live.get(model_id), proxy_host, pid_dir=pid_dir)


@router.post("/v1/backends/{model_id}/start", tags=["backends"])
async def start_backend(
    model_id: str,
    request: Request,
    _claims: Annotated[Claims, Depends(require_backend_registry_write)],
) -> dict[str, Any]:
    """Implements: memory/specs/008-llama-server-manager.md — AC-4, AC-5, AC-9, AC-10"""
    with _tracer.start_as_current_span("backend.start", kind=SpanKind.INTERNAL) as span:
        span.set_attribute("model_id", model_id)
        result = await _control_action("start", model_id, request)
        span.set_attribute("http.status_code", 200)
        return result


@router.post("/v1/backends/{model_id}/stop", tags=["backends"])
async def stop_backend(
    model_id: str,
    request: Request,
    _claims: Annotated[Claims, Depends(require_backend_registry_write)],
) -> dict[str, Any]:
    """Implements: memory/specs/008-llama-server-manager.md — AC-6, AC-7"""
    with _tracer.start_as_current_span("backend.stop", kind=SpanKind.INTERNAL) as span:
        span.set_attribute("model_id", model_id)
        result = await _control_action("stop", model_id, request)
        span.set_attribute("http.status_code", 200)
        return result


@router.post("/v1/backends/{model_id}/restart", tags=["backends"])
async def restart_backend(
    model_id: str,
    request: Request,
    _claims: Annotated[Claims, Depends(require_backend_registry_write)],
) -> dict[str, Any]:
    """Implements: memory/specs/008-llama-server-manager.md — AC-8"""
    with _tracer.start_as_current_span("backend.restart", kind=SpanKind.INTERNAL) as span:
        span.set_attribute("model_id", model_id)
        result = await _control_action("restart", model_id, request)
        span.set_attribute("http.status_code", 200)
        return result
