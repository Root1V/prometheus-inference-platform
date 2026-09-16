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

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from opentelemetry.trace import SpanKind
from prometheus_manager_core.config import ManagerConfig
from prometheus_manager_core.lifecycle import (
    LifecycleError,
    deregister_instance,
    deregister_model,
    restart_instance,
    start_instance,
    stop_instance,
)
from prometheus_manager_core.registry import (
    Registry,
    RegistryEntry,
    _assert_modality_matches_file,
    _validate_backend,
    _validate_modality,
    _validate_path,
    _validate_port,
)
from prometheus_manager_core.scanner import scan
from prometheus_manager_core.telemetry import get_tracer

from .auth import require_backend_registry_read, require_backend_registry_write
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
        # PRM-109: "modality" is deliberately absent. It belongs to the model,
        # not to one of its processes — see the refusal below, which explains
        # where to change it instead of silently dropping the field.
        "mmproj_path",
        "discovery",
        "hf_repo",
        "hf_sha256",
        "port",
        "vae_path",
        "clip_l_path",
        "t5xxl_path",
        "cfg_scale",
        # RM-70: display label only. `slug` is deliberately NOT here — it goes
        # through registry.set_slug(), which enforces the once-only rule that a
        # plain column update would bypass.
        "name",
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

    RM-51: an optional `model_id` field selects a different mode — "create a
    new instance of an already-catalogued model" (the admin dashboard's
    RegisterModelModal uses this once a downloaded model is picked from the
    catalog) instead of today's "register a brand-new model" (manual/
    hand-typed-path registration, which upserts both a catalog row and an
    instance row under the same id — unchanged, still the default when
    `model_id` is absent).

    Implements: memory/specs/008-llama-server-manager.md — AC-3, AC-16, AC-17
    Implements: docs/roadmap.md — RM-10, RM-51
    """
    with _tracer.start_as_current_span("backend.register", kind=SpanKind.INTERNAL) as span:
        registry: Registry = request.app.state.registry
        instance_id = body.get("id", "")
        span.set_attribute("model_id", instance_id)

        model_id = body.get("model_id")
        if model_id:
            # RM-70: adding a replica shouldn't ask for a name. When the caller
            # omits `id`, derive the next free one — the whole point is that
            # going from one instance to two is a port and a node, not an
            # exercise in inventing globally unique strings.
            if not instance_id:
                instance_id = registry.next_instance_id(model_id)
                span.set_attribute("model_id", instance_id)
            if registry.get_catalog(model_id) is None:
                span.set_attribute("http.status_code", 404)
                raise _problem(
                    404,
                    "not-found",
                    "Not Found",
                    f"No catalog entry {model_id!r} — download or register it first.",
                )
            try:
                registry.add_instance(
                    instance_id,
                    model_id,
                    port=int(body.get("port", 0)),
                    backend=body.get("backend", "llama_cpp"),
                    modality=body.get("modality", "text"),
                    context_length=int(body.get("context_length", 4096)),
                    discovery=bool(body.get("discovery", False)),
                    rss_estimate_mb=body.get("rss_estimate_mb"),
                    cfg_scale=body.get("cfg_scale"),
                )
            except (ValueError, TypeError) as exc:
                span.set_attribute("http.status_code", 400)
                raise _problem(
                    400, "invalid-registration", "Invalid Registration", str(exc)
                ) from exc
            span.set_attribute("http.status_code", 201)
            created = registry.get(instance_id)
            assert created is not None  # just added above
            created_result: dict[str, Any] = created.to_dict()
            return created_result

        try:
            entry: RegistryEntry = RegistryEntry(
                id=instance_id,
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
                vae_path=body.get("vae_path", ""),
                clip_l_path=body.get("clip_l_path", ""),
                t5xxl_path=body.get("t5xxl_path", ""),
                cfg_scale=body.get("cfg_scale"),
            )
            # RM-70: both default to the instance id inside add(), matching
            # every registration made before slugs existed.
            registry.add(entry, slug=body.get("slug", ""), name=body.get("name", ""))
        except (ValueError, TypeError) as exc:
            span.set_attribute("http.status_code", 400)
            raise _problem(400, "invalid-registration", "Invalid Registration", str(exc)) from exc

        span.set_attribute("http.status_code", 201)
        # RM-51: add() sets the catalog FK to entry.id under the hood, but the
        # in-memory `entry` built above never had .model_id assigned — return
        # the freshly-persisted entry instead of the stale transient one.
        persisted = registry.get(instance_id)
        assert persisted is not None  # just added above
        result: dict[str, Any] = persisted.to_dict()
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

        # RM-70: naming a model is allowed exactly once, while its slug is
        # still the placeholder the migration left behind (slug == model id).
        # After that it's frozen: clients route on it and their grants key off
        # it, which is what immutability is protecting. set_slug enforces both.
        if "slug" in body:
            try:
                registry.set_slug(entry.model_id or entry.id, str(body["slug"]))
            except (ValueError, TypeError) as exc:
                span.set_attribute("http.status_code", 400)
                raise _problem(400, "invalid-update", "Invalid Update", str(exc)) from exc

        if "modality" in body:
            span.set_attribute("http.status_code", 400)
            raise _problem(
                400,
                "invalid-update",
                "Invalid Update",
                "Modality belongs to the model, not to one of its instances — every "
                f"replica must agree on it. Use PATCH /v1/models/{entry.model_id or model_id} "
                "instead.",
            )

        updates = {k: v for k, v in body.items() if k in _UPDATABLE_FIELDS}
        merged_backend = updates.get("backend", entry.backend)
        merged_path = updates.get("path", entry.path)
        merged_port = updates.get("port", entry.port)
        merged_modality = entry.modality
        merged_vae_path = updates.get("vae_path", entry.vae_path)
        merged_clip_l_path = updates.get("clip_l_path", entry.clip_l_path)
        merged_t5xxl_path = updates.get("t5xxl_path", entry.t5xxl_path)

        try:
            _validate_backend(merged_backend)
            _validate_modality(merged_modality)
            # PRM-107: the same check registration does. Correcting a modality
            # is the main reason this endpoint exists, so it must be possible to
            # set the right one — and impossible to set one the file denies.
            _assert_modality_matches_file(merged_path, merged_modality)
            _validate_path(merged_path, merged_backend)
            _validate_path(merged_vae_path, merged_backend)
            _validate_path(merged_clip_l_path, merged_backend)
            _validate_path(merged_t5xxl_path, merged_backend)
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


@router.get("/v1/models/archived", tags=["models"])
async def list_archived_models(
    request: Request,
    _claims: Annotated[Claims, Depends(require_backend_registry_read)],
) -> dict[str, Any]:
    """Models retired from service — docs/roadmap.md RM-74.

    They no longer route, list, or accept instances, but their names stay
    reserved and their metadata stays answerable for usage billed under them.
    """
    registry: Registry = request.app.state.registry
    return {"archived": [c.to_dict() for c in registry.list_archived()]}


@router.delete("/v1/models/{model_id}", tags=["models"], status_code=204)
async def retire_model(
    model_id: str,
    request: Request,
    _claims: Annotated[Claims, Depends(require_backend_registry_write)],
    confirm: Annotated[
        bool,
        Query(
            description="Required when any instance of this model is running — "
            "otherwise a 400 lists them instead of stopping them silently."
        ),
    ] = False,
) -> Response:
    """Retire a model: stop and remove its instances, keep its name — RM-76.

    Separate from `DELETE /v1/models/{id}/downloaded` because that endpoint
    conflates two different acts. It means "reclaim the disk", so it refuses
    anything not downloaded through that flow — which left a hand-registered
    model with no way to be retired at all. That gap mattered once archiving
    became what keeps a published slug from being handed to a different model
    ([[RM-74]]).

    This touches no files. The catalog row is archived rather than deleted, so
    the model's name stays reserved and its metadata stays answerable for usage
    already billed under it; `POST /v1/models/{id}/restore` undoes it.
    """
    with _tracer.start_as_current_span("model.retire", kind=SpanKind.INTERNAL) as span:
        span.set_attribute("model_id", model_id)
        registry: Registry = request.app.state.registry
        config: ManagerConfig = request.app.state.config
        pid_dir = request.app.state.pid_dir

        if registry.get_catalog(model_id) is None:
            span.set_attribute("http.status_code", 404)
            raise _problem(404, "not-found", "Not Found", f"Model {model_id!r} not registered.")

        instance_ids = {e.id for e in registry.entries if e.model_id == model_id}
        live = [p for p in await asyncio.to_thread(scan, pid_dir, instance_ids) if p.model_id]
        if live and not confirm:
            span.set_attribute("http.status_code", 400)
            raise _problem(
                400,
                "confirmation-required",
                "Confirmation Required",
                f"{model_id!r} has running instances {sorted(p.model_id for p in live)} — "
                "pass confirm=true to stop and remove them along with the model.",
            )

        await asyncio.to_thread(deregister_model, model_id, config, registry)
        span.set_attribute("http.status_code", 204)
        return Response(status_code=204)


@router.post("/v1/models/{model_id}/restore", tags=["models"])
async def restore_archived_model(
    model_id: str,
    request: Request,
    _claims: Annotated[Claims, Depends(require_backend_registry_write)],
) -> dict[str, Any]:
    """Bring an archived model back — RM-74.

    The escape hatch that lets archiving be the default: archiving the wrong
    model is undoable, whereas deleting it and losing its name is not.
    """
    with _tracer.start_as_current_span("model.restore", kind=SpanKind.INTERNAL) as span:
        span.set_attribute("model_id", model_id)
        registry: Registry = request.app.state.registry
        try:
            registry.restore_catalog(model_id)
        except KeyError as exc:
            span.set_attribute("http.status_code", 404)
            raise _problem(
                404, "not-found", "Not Found", f"No archived model {model_id!r}."
            ) from exc
        span.set_attribute("http.status_code", 200)
        restored = registry.get_catalog(model_id)
        assert restored is not None  # just restored above
        result: dict[str, Any] = restored.to_dict()
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
    return _merge(entry.to_dict(), live.get(model_id), proxy_host, pid_dir=pid_dir)


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
