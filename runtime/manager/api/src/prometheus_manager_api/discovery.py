"""Model discovery + download endpoints for the Prometheus Manager REST API.

GET    /v1/models/search               — search Hugging Face for GGUF models
GET    /v1/models/search/files         — list a repo's GGUF files
GET    /v1/models/search/card          — fetch a repo's model card
POST   /v1/models/downloads            — register + start downloading a model
GET    /v1/models/downloads            — list active/recent download progress
POST   /v1/models/downloads/{id}/cancel
POST   /v1/models/downloads/{id}/retry — restart from scratch (no byte-range resume)
DELETE /v1/models/{id}/downloaded      — delete the on-disk file(s) + deregister

Implements: docs/roadmap.md — RM-48 (Models page: discover/download/manage)

The download orchestration below mirrors the TUI's own App._do_download /
action_discovery_download exactly (see
runtime/manager/tui/src/prometheus_manager_tui/app.py) — register a
RegistryEntry with downloaded=False first, then download each shard in the
background, and only mark downloaded=True + set path once every shard
reports status="done". Progress is tracked in an in-memory
list[DownloadState] on app.state.downloads, the same shape the TUI's own
App._downloads list uses, polled by the admin dashboard the same way the
TUI's DownloadsView polls it on a refresh tick.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from opentelemetry.trace import SpanKind
from prometheus_manager_core.config import ManagerConfig
from prometheus_manager_core.downloader import DownloadError, DownloadState, download_model
from prometheus_manager_core.hf_discovery import (
    auto_id,
    fetch_model_card,
    infer_quant,
    list_model_files,
    next_free_port,
    search_models,
    shard_filenames,
)
from prometheus_manager_core.registry import Registry, RegistryEntry
from prometheus_manager_core.scanner import scan
from prometheus_manager_core.telemetry import get_tracer

from .auth import require_backend_registry_read, require_backend_registry_write

router = APIRouter()

Claims = dict[str, Any]

_tracer = get_tracer("manager.api")

# States a download is still "in flight" for — used to find/cancel/replace
# entries by model_id (which may carry a "{id} [n/total]" shard suffix).
_ACTIVE_STATUSES = {"queued", "downloading", "verifying"}


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


def _downloads_list(request: Request) -> list[DownloadState]:
    """The in-memory download-progress list, created lazily (mirrors how
    proxy_host/pid_dir are read via getattr elsewhere in this codebase)."""
    if not hasattr(request.app.state, "downloads"):
        request.app.state.downloads = []
    result: list[DownloadState] = request.app.state.downloads
    return result


def _serialize(ds: DownloadState) -> dict[str, Any]:
    return {
        "model_id": ds.model_id,
        "hf_repo": ds.hf_repo,
        "hf_filename": ds.hf_filename,
        "total_bytes": ds.total_bytes,
        "downloaded_bytes": ds.downloaded_bytes,
        "progress": ds.progress,
        "status": ds.status,
        "error": ds.error,
        "speed_bps": ds.speed_bps,
        "eta_seconds": ds.eta_seconds,
    }


# ── GET /v1/models/search[/files|/card] ──────────────────────────────────────


@router.get("/v1/models/search", tags=["models"])
async def search(
    request: Request,
    _claims: Annotated[Claims, Depends(require_backend_registry_read)],
    q: Annotated[str, Query(min_length=1)],
    sort: Annotated[str | None, Query()] = None,
) -> dict[str, Any]:
    with _tracer.start_as_current_span("models.search", kind=SpanKind.INTERNAL) as span:
        span.set_attribute("query", q)
        config: ManagerConfig = request.app.state.config
        try:
            results = await asyncio.to_thread(
                search_models, q, 30, config.hf_token, config.resolved_ca_bundle, sort
            )
        except ValueError as exc:
            span.set_attribute("http.status_code", 400)
            raise _problem(400, "invalid-sort", "Invalid Sort", str(exc)) from exc
        except Exception as exc:
            span.set_attribute("http.status_code", 502)
            raise _problem(502, "hf-search-failed", "Search Failed", str(exc)) from exc
        span.set_attribute("http.status_code", 200)
        return {"results": results}


@router.get("/v1/models/search/files", tags=["models"])
async def search_files(
    request: Request,
    _claims: Annotated[Claims, Depends(require_backend_registry_read)],
    repo_id: Annotated[str, Query(min_length=1)],
) -> dict[str, Any]:
    with _tracer.start_as_current_span("models.search_files", kind=SpanKind.INTERNAL) as span:
        span.set_attribute("repo_id", repo_id)
        config: ManagerConfig = request.app.state.config
        try:
            files = await asyncio.to_thread(
                list_model_files, repo_id, config.hf_token, config.resolved_ca_bundle
            )
        except Exception as exc:
            span.set_attribute("http.status_code", 502)
            raise _problem(502, "hf-files-failed", "File Listing Failed", str(exc)) from exc
        span.set_attribute("http.status_code", 200)
        return {"files": files}


@router.get("/v1/models/search/card", tags=["models"])
async def search_card(
    request: Request,
    _claims: Annotated[Claims, Depends(require_backend_registry_read)],
    repo_id: Annotated[str, Query(min_length=1)],
) -> dict[str, Any]:
    with _tracer.start_as_current_span("models.search_card", kind=SpanKind.INTERNAL) as span:
        span.set_attribute("repo_id", repo_id)
        config: ManagerConfig = request.app.state.config
        try:
            card: dict[str, Any] = await asyncio.to_thread(
                fetch_model_card, repo_id, config.hf_token, config.resolved_ca_bundle
            )
        except Exception as exc:
            span.set_attribute("http.status_code", 502)
            raise _problem(502, "hf-card-failed", "Model Card Fetch Failed", str(exc)) from exc
        span.set_attribute("http.status_code", 200)
        return card


# ── Download orchestration ────────────────────────────────────────────────────


async def _run_download(
    request_app_state: Any,
    downloads: list[DownloadState],
    registry: Registry,
    config: ManagerConfig,
    model_id: str,
    hf_repo: str,
    shard_files: list[str],
    expected_sha256: str,
) -> None:
    """Background task — downloads every shard, then marks the entry
    downloaded=True. Mirrors app.py's App._do_download exactly."""
    total = len(shard_files)
    shard_states: list[DownloadState] = []
    for idx, hf_filename in enumerate(shard_files):
        label = f"{model_id} [{idx + 1}/{total}]" if total > 1 else model_id
        ds = DownloadState(
            model_id=label, hf_repo=hf_repo, hf_filename=hf_filename, status="queued"
        )
        shard_states.append(ds)
        downloads.append(ds)

    first_dest: Path | None = None
    for ds in shard_states:
        if ds.cancel_requested:
            ds.status = "cancelled"
            continue

        def on_progress(state: DownloadState, _ds: DownloadState = ds) -> None:
            _ds.total_bytes = state.total_bytes
            _ds.downloaded_bytes = state.downloaded_bytes
            _ds.status = state.status
            _ds.error = state.error
            _ds.speed_bps = state.speed_bps
            _ds.eta_seconds = state.eta_seconds
            if _ds.cancel_requested:
                state.cancel_requested = True

        try:
            dest = await asyncio.to_thread(
                download_model,
                model_id=ds.model_id,
                hf_repo=hf_repo,
                hf_filename=ds.hf_filename,
                dest_dir=config.resolved_downloads_dir,
                hf_token=config.hf_token,
                expected_sha256=(expected_sha256 or None) if total == 1 else None,
                on_progress=on_progress,
                ca_bundle=config.resolved_ca_bundle,
            )
        except DownloadError as exc:
            ds.status = "failed"
            ds.error = str(exc)
            for remaining in shard_states:
                if remaining.status == "queued":
                    remaining.status = "cancelled"
            return

        if ds.status != "cancelled" and first_dest is None:
            first_dest = dest
        if ds.status == "cancelled":
            for remaining in shard_states:
                if remaining.status == "queued":
                    remaining.status = "cancelled"
            return

    if all(s.status == "done" for s in shard_states) and first_dest is not None:
        registry.update(model_id, downloaded=True, path=str(first_dest))


@router.post("/v1/models/downloads", tags=["models"], status_code=202)
async def start_download(
    body: dict[str, Any],
    request: Request,
    _claims: Annotated[Claims, Depends(require_backend_registry_write)],
) -> dict[str, Any]:
    """Register a model from a Hugging Face repo/file and start downloading it.

    Body: {repo_id, filename, model_id?, context_length?, family?,
    quantization?, modality?}. Shard siblings (HF's NNNNN-of-MMMMM naming) are
    detected automatically from the repo's file list — the caller only needs
    to pick one filename from GET /v1/models/search/files.
    """
    with _tracer.start_as_current_span("models.download.start", kind=SpanKind.INTERNAL) as span:
        registry: Registry = request.app.state.registry
        config: ManagerConfig = request.app.state.config
        registry.reload()

        repo_id = body.get("repo_id", "")
        filename = body.get("filename", "")
        if not repo_id or not filename:
            span.set_attribute("http.status_code", 400)
            raise _problem(
                400, "invalid-download", "Invalid Download", "repo_id and filename are required."
            )
        span.set_attribute("repo_id", repo_id)
        span.set_attribute("filename", filename)

        try:
            all_files = await asyncio.to_thread(
                list_model_files, repo_id, config.hf_token, config.resolved_ca_bundle
            )
        except Exception as exc:
            span.set_attribute("http.status_code", 502)
            raise _problem(502, "hf-files-failed", "File Listing Failed", str(exc)) from exc

        all_filenames = [f["filename"] for f in all_files]
        if filename not in all_filenames:
            span.set_attribute("http.status_code", 400)
            raise _problem(
                400,
                "invalid-download",
                "Invalid Download",
                f"{filename!r} is not a GGUF file in {repo_id!r}.",
            )

        shard_files = shard_filenames(filename, all_filenames)

        existing_ids = {e.id for e in registry.entries}
        model_id = body.get("model_id") or auto_id(shard_files[0], existing_ids)
        if model_id in existing_ids:
            span.set_attribute("http.status_code", 409)
            raise _problem(
                409,
                "already-registered",
                "Already Registered",
                f"Model {model_id!r} already exists.",
            )

        used_ports = {e.port for e in registry.entries}
        port = next_free_port(used_ports)

        entry = RegistryEntry(
            id=model_id,
            port=port,
            context_length=int(body.get("context_length", 4096)),
            family=body.get("family", ""),
            quantization=body.get("quantization") or infer_quant(shard_files[0]),
            modality=body.get("modality", "text"),
            downloaded=False,
            hf_repo=repo_id,
            hf_filename=shard_files[0],
            hf_filenames=shard_files if len(shard_files) > 1 else [],
        )
        try:
            registry.add(entry)
        except (ValueError, TypeError) as exc:
            span.set_attribute("http.status_code", 400)
            raise _problem(400, "invalid-registration", "Invalid Registration", str(exc)) from exc

        downloads = _downloads_list(request)
        asyncio.create_task(
            _run_download(
                request.app.state,
                downloads,
                registry,
                config,
                model_id,
                repo_id,
                shard_files,
                body.get("hf_sha256", ""),
            )
        )

        span.set_attribute("http.status_code", 202)
        return {
            "model_id": model_id,
            "port": port,
            "hf_repo": repo_id,
            "shard_count": len(shard_files),
        }


@router.get("/v1/models/downloads", tags=["models"])
async def list_downloads(
    request: Request,
    _claims: Annotated[Claims, Depends(require_backend_registry_read)],
) -> dict[str, Any]:
    downloads = _downloads_list(request)
    return {"downloads": [_serialize(ds) for ds in downloads]}


def _matching(downloads: list[DownloadState], model_id: str) -> list[DownloadState]:
    prefix = f"{model_id} ["
    return [ds for ds in downloads if ds.model_id == model_id or ds.model_id.startswith(prefix)]


@router.post("/v1/models/downloads/{model_id}/cancel", tags=["models"])
async def cancel_download(
    model_id: str,
    request: Request,
    _claims: Annotated[Claims, Depends(require_backend_registry_write)],
) -> dict[str, Any]:
    downloads = _downloads_list(request)
    matches = _matching(downloads, model_id)
    if not matches:
        raise _problem(404, "not-found", "Not Found", f"No download in progress for {model_id!r}.")
    for ds in matches:
        if ds.status in _ACTIVE_STATUSES:
            ds.cancel_requested = True
    return {"cancelled": [ds.model_id for ds in matches if ds.status in _ACTIVE_STATUSES]}


@router.post("/v1/models/downloads/{model_id}/retry", tags=["models"])
async def retry_download(
    model_id: str,
    request: Request,
    _claims: Annotated[Claims, Depends(require_backend_registry_write)],
) -> dict[str, Any]:
    """Restart a failed/cancelled download from scratch — no byte-range
    resume exists anywhere in this codebase (downloader.py always starts
    from byte 0), so "retry" and "resume" are the same operation here."""
    with _tracer.start_as_current_span("models.download.retry", kind=SpanKind.INTERNAL) as span:
        span.set_attribute("model_id", model_id)
        registry: Registry = request.app.state.registry
        config: ManagerConfig = request.app.state.config
        entry = registry.get(model_id)
        if entry is None or not entry.hf_repo:
            span.set_attribute("http.status_code", 404)
            raise _problem(
                404, "not-found", "Not Found", f"No downloadable model registered as {model_id!r}."
            )

        downloads = _downloads_list(request)
        downloads[:] = [ds for ds in downloads if ds not in _matching(downloads, model_id)]

        shard_files = list(entry.hf_filenames) if entry.hf_filenames else [entry.hf_filename]
        asyncio.create_task(
            _run_download(
                request.app.state,
                downloads,
                registry,
                config,
                model_id,
                entry.hf_repo,
                shard_files,
                entry.hf_sha256,
            )
        )
        span.set_attribute("http.status_code", 202)
        return {"model_id": model_id, "shard_count": len(shard_files)}


@router.delete("/v1/models/{model_id}/downloaded", tags=["models"], status_code=204)
async def delete_downloaded_model(
    model_id: str,
    request: Request,
    _claims: Annotated[Claims, Depends(require_backend_registry_write)],
) -> Response:
    """Delete a downloaded model's on-disk file(s) and deregister it.

    Only applies to entries downloaded through this flow (downloaded=True) —
    a manually-registered local-path entry (path typed by hand, never
    downloaded here) is untouched by this endpoint; use the regular
    DELETE /v1/backends/{id} to just deregister without touching any file.
    """
    with _tracer.start_as_current_span("models.delete_downloaded", kind=SpanKind.INTERNAL) as span:
        span.set_attribute("model_id", model_id)
        registry: Registry = request.app.state.registry
        pid_dir: Path = request.app.state.pid_dir

        entry = registry.get(model_id)
        if entry is None:
            span.set_attribute("http.status_code", 404)
            raise _problem(404, "not-found", "Not Found", f"Model {model_id!r} not registered.")
        if not entry.downloaded:
            span.set_attribute("http.status_code", 400)
            raise _problem(
                400,
                "not-downloaded",
                "Not Downloaded",
                f"{model_id!r} was not downloaded through this flow — nothing to delete on disk.",
            )

        live = [p for p in await asyncio.to_thread(scan, pid_dir, {model_id}) if p.model_id]
        if live:
            span.set_attribute("http.status_code", 409)
            raise _problem(
                409,
                "lifecycle-conflict",
                "Lifecycle Conflict",
                f"Stop {model_id!r} before deleting its downloaded file.",
            )

        filenames = list(entry.hf_filenames) if entry.hf_filenames else [entry.hf_filename]
        for fname in filenames:
            if fname:
                (request.app.state.config.resolved_downloads_dir / fname).unlink(missing_ok=True)

        registry.remove(model_id)
        downloads = _downloads_list(request)
        downloads[:] = [ds for ds in downloads if ds not in _matching(downloads, model_id)]

        span.set_attribute("http.status_code", 204)
        return Response(status_code=204)
