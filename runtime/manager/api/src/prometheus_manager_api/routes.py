"""API routes for the Prometheus Manager.

GET /health                 — liveness probe (no auth)
GET /v1/backends            — all registered models + live state (requires JWT)
GET /v1/backends/{id}       — single model + live state (requires JWT)
GET /v1/backends/{id}/logs  — tail that model's log file (requires JWT)

Implements: memory/specs/008-llama-server-manager.md — AC-11, AC-12, AC-13
Implements: docs/roadmap.md — RM-13 (live log viewer)
"""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from opentelemetry.trace import SpanKind
from prometheus_manager_core.registry import Registry, RegistryEntry
from prometheus_manager_core.scanner import ProcessState, scan
from prometheus_manager_core.telemetry import get_tracer

from .auth import require_backend_registry_read

router = APIRouter()

Claims = dict[str, Any]

_HTTP_PROBE_TIMEOUT = 2.0  # seconds
_BACKEND_PROBE_THRESHOLD = int(os.environ.get("OTEL_BACKEND_PROBE_SPAN_THRESHOLD", "10"))
_tracer = get_tracer("manager.api")


async def _probe_state(entry: RegistryEntry, proxy_host: str) -> str:
    """HTTP health probe for container mode — psutil unavailable inside containers.

    Replaces the backend_url host (127.0.0.1) with proxy_host so the container
    can reach llama-server processes running on the bare-metal host.
    """
    url = entry.backend_url.replace("127.0.0.1", proxy_host).replace("::1", proxy_host)
    try:
        async with httpx.AsyncClient(timeout=_HTTP_PROBE_TIMEOUT) as client:
            resp = await client.get(f"{url}/health")
        return "ready" if resp.status_code == 200 else "error"
    except Exception:
        return "stopped"


@router.get("/health", tags=["ops"])
async def health() -> dict[str, str]:
    """Liveness probe — no authentication required."""
    return {"status": "ok"}


@router.get("/v1/backends", tags=["backends"])
async def list_backends(
    request: Request,
    _claims: Annotated[Claims, Depends(require_backend_registry_read)],
    include_hidden: Annotated[
        bool, Query(description="RM-10: also include discovery=false entries (operator use).")
    ] = False,
) -> dict[str, Any]:
    """Return all registered backends with their live process state.

    Implements: memory/specs/008-llama-server-manager.md — AC-11
    Implements: docs/roadmap.md — RM-10 (include_hidden for the admin dashboard)
    """
    with _tracer.start_as_current_span("backend.list", kind=SpanKind.INTERNAL) as span:
        registry: Registry = request.app.state.registry
        # Always reload from disk so TUI changes appear immediately without restart.
        registry.reload()

        proxy_host: str = getattr(request.app.state, "proxy_host", "")
        # AC-18 (spec 010): only expose entries with discovery: true, unless the
        # caller explicitly asked for hidden ones too (RM-10 admin dashboard —
        # backend-registry:read is already an internal/operator credential, not
        # exposed to end users, so this isn't a new trust boundary).
        entries = (
            registry.entries if include_hidden else [e for e in registry.entries if e.discovery]
        )
        entry_ids = {e.id for e in entries}
        use_batch = len(entries) > _BACKEND_PROBE_THRESHOLD

        if proxy_host:
            # Container mode: prefer psutil-based scan when the container shares the
            # host PID namespace (e.g. podman `pid: "host"`). Fall back to HTTP
            # health probing if no live processes are found.
            pid_dir: Path = request.app.state.pid_dir
            live_procs = await asyncio.to_thread(scan, pid_dir, entry_ids)
            live = {proc.model_id: proc for proc in live_procs if proc.model_id}

            if live:
                # We have live ProcessState entries visible via psutil; merge them.
                backends = [
                    _merge(entry.__dict__, live.get(entry.id), pid_dir=pid_dir) for entry in entries
                ]
            else:
                # Fallback: HTTP probing when psutil cannot see host processes.
                if use_batch:
                    with _tracer.start_as_current_span(
                        "backend.probe.batch", kind=SpanKind.INTERNAL
                    ) as probe_span:
                        probe_span.set_attribute("model_count", len(entries))
                        backends = [
                            _merge(
                                entry.__dict__, await _probe_state(entry, proxy_host), proxy_host
                            )
                            for entry in entries
                        ]
                else:
                    backends = []
                    for entry in entries:
                        with _tracer.start_as_current_span(
                            "backend.probe", kind=SpanKind.INTERNAL
                        ) as probe_span:
                            probe_span.set_attribute("model_id", entry.id)
                            state = await _probe_state(entry, proxy_host)
                            probe_span.set_attribute("probe_result", state)
                            backends.append(_merge(entry.__dict__, state, proxy_host))
        else:
            # Bare-metal mode: psutil-based process scanning.
            pid_dir = request.app.state.pid_dir
            entry_ids = {e.id for e in entries}
            if use_batch:
                with _tracer.start_as_current_span(
                    "backend.probe.batch", kind=SpanKind.INTERNAL
                ) as probe_span:
                    probe_span.set_attribute("model_count", len(entries))
                    live = {
                        proc.model_id: proc
                        for proc in await asyncio.to_thread(scan, pid_dir, entry_ids)
                        if proc.model_id
                    }
            else:
                proc_list = await asyncio.to_thread(scan, pid_dir, entry_ids)
                live = {proc.model_id: proc for proc in proc_list if proc.model_id}
                for entry in entries:
                    proc = live.get(entry.id)
                    state_str = proc.state if proc else "stopped"
                    with _tracer.start_as_current_span(
                        "backend.probe", kind=SpanKind.INTERNAL
                    ) as probe_span:
                        probe_span.set_attribute("model_id", entry.id)
                        probe_span.set_attribute("probe_result", state_str)
            backends = [
                _merge(entry.__dict__, live.get(entry.id), pid_dir=pid_dir) for entry in entries
            ]

        span.set_attribute("backend_count", len(backends))
        span.set_attribute("http.status_code", 200)
        return {"backends": backends}


@router.get("/v1/backends/{model_id}", tags=["backends"])
async def get_backend(
    model_id: str,
    request: Request,
    _claims: Annotated[Claims, Depends(require_backend_registry_read)],
) -> dict[str, Any]:
    """Return a single backend with its live process state.

    Implements: memory/specs/008-llama-server-manager.md — AC-11
    """
    with _tracer.start_as_current_span("backend.get", kind=SpanKind.INTERNAL) as span:
        span.set_attribute("model_id", model_id)
        registry: Registry = request.app.state.registry
        # Always reload from disk so TUI changes appear immediately without restart.
        registry.reload()

        # Validate model_id before using it in error messages to prevent log injection.
        from prometheus_manager_core.registry import _validate_id as _vid

        try:
            _vid(model_id)
        except ValueError:
            span.set_attribute("http.status_code", 404)
            raise HTTPException(
                status_code=404,
                detail={
                    "type": "https://prometheus.local/errors/not-found",
                    "title": "Not Found",
                    "status": 404,
                    "detail": "Backend not found.",
                },
            ) from None

        entry = registry.get(model_id)
        if entry is None:
            span.set_attribute("http.status_code", 404)
            raise HTTPException(
                status_code=404,
                detail={
                    "type": "https://prometheus.local/errors/not-found",
                    "title": "Not Found",
                    "status": 404,
                    "detail": f"Backend '{model_id}' not found.",
                },
            )

        proxy_host: str = getattr(request.app.state, "proxy_host", "")
        if proxy_host:
            # Try psutil scan first when possible (container with host PID namespace).
            pid_dir: Path = request.app.state.pid_dir
            live_procs = await asyncio.to_thread(scan, pid_dir, {model_id})
            live = {proc.model_id: proc for proc in live_procs if proc.model_id}
            proc = live.get(model_id)
            if proc is not None:
                result = _merge(entry.__dict__, proc, pid_dir=pid_dir)
            else:
                result = _merge(entry.__dict__, await _probe_state(entry, proxy_host), proxy_host)
        else:
            pid_dir = request.app.state.pid_dir
            live = {
                proc.model_id: proc
                for proc in await asyncio.to_thread(scan, pid_dir, {model_id})
                if proc.model_id
            }
            result = _merge(entry.__dict__, live.get(model_id), pid_dir=pid_dir)

        span.set_attribute("backend_state", result.get("state", "unknown"))
        span.set_attribute("http.status_code", 200)
        return result


def _tail_lines(path: Path, n: int) -> list[str]:
    """Return the last *n* lines of a text file. Simple whole-file read — fine for a
    single model's log; revisit with a seek-based tail if this ever proves too slow."""
    text = path.read_text(errors="replace")
    lines = text.splitlines()
    return lines[-n:]


@router.get("/v1/backends/{model_id}/logs", tags=["backends"])
async def get_backend_logs(
    model_id: str,
    request: Request,
    _claims: Annotated[Claims, Depends(require_backend_registry_read)],
    tail: Annotated[
        int, Query(ge=1, le=2000, description="Number of trailing lines to return.")
    ] = 200,
) -> dict[str, Any]:
    """Return the tail of a backend's log file (stdout+stderr, RM-13).

    Implements: docs/roadmap.md — RM-13 (live log viewer)
    """
    with _tracer.start_as_current_span("backend.logs", kind=SpanKind.INTERNAL) as span:
        span.set_attribute("model_id", model_id)
        registry: Registry = request.app.state.registry
        registry.reload()

        from prometheus_manager_core.registry import _validate_id as _vid

        try:
            _vid(model_id)
        except ValueError:
            span.set_attribute("http.status_code", 404)
            raise HTTPException(
                status_code=404,
                detail={
                    "type": "https://prometheus.local/errors/not-found",
                    "title": "Not Found",
                    "status": 404,
                    "detail": "Backend not found.",
                },
            ) from None

        if registry.get(model_id) is None:
            span.set_attribute("http.status_code", 404)
            raise HTTPException(
                status_code=404,
                detail={
                    "type": "https://prometheus.local/errors/not-found",
                    "title": "Not Found",
                    "status": 404,
                    "detail": f"Backend '{model_id}' not found.",
                },
            )

        config = request.app.state.config
        log_path: Path = config.resolved_log_dir / f"{model_id}.log"
        if not log_path.exists():
            span.set_attribute("http.status_code", 200)
            return {"model_id": model_id, "lines": []}

        lines = await asyncio.to_thread(_tail_lines, log_path, tail)
        span.set_attribute("line_count", len(lines))
        span.set_attribute("http.status_code", 200)
        return {"model_id": model_id, "lines": lines}


def _uptime_s(ps: ProcessState) -> float:
    now = datetime.now(tz=UTC)
    return float((now - ps.started_at).total_seconds())


def _file_size_bytes(entry: dict[str, Any]) -> int | None:
    """Total on-disk size of a downloaded model's file(s) — RM-48 follow-up.

    Sums every shard for a multi-part model (hf_filenames), all resolved
    relative to the single `path` field's own directory rather than
    reaching into ManagerConfig, so _merge() stays self-contained. None
    when the entry isn't downloaded, has no path, or a file is missing
    (e.g. deleted out-of-band) — the admin dashboard shows that as "?".
    """
    path = entry.get("path")
    if not entry.get("downloaded") or not path:
        return None
    base_dir = Path(path).parent
    filenames = entry.get("hf_filenames") or [Path(path).name]
    total = 0
    try:
        for filename in filenames:
            total += (base_dir / filename).stat().st_size
    except OSError:
        return None
    return total


def _merge(
    entry: dict[str, Any],
    ps: ProcessState | str | None,
    proxy_host: str = "",
    pid_dir: Path | None = None,
) -> dict[str, Any]:
    """Merge registry entry with live process state.

    Accepts either a ProcessState (bare-metal mode) or a state string
    from HTTP health probing (container mode).

    When proxy_host is set, backend_url is rewritten so that the gateway
    container can reach llama-server on the bare-metal host:
      http://127.0.0.1:<port>  →  http://host.containers.internal:<port>

    RM-10: when there's no live process and pid_dir is given, checks for a
    "{model_id}.error" marker written by lifecycle.py on a failed start —
    without it, a crashed-on-start model is indistinguishable from one
    that's simply never been started ("stopped" either way).
    """
    result: dict[str, Any] = dict(entry)
    result["file_size_bytes"] = _file_size_bytes(entry)
    # Rewrite backend_url for container consumers when proxy_host is set.
    if proxy_host and result.get("backend_url"):
        result["backend_url"] = (
            result["backend_url"].replace("127.0.0.1", proxy_host).replace("::1", proxy_host)
        )
    if isinstance(ps, str):
        # Container mode: state comes from HTTP health probe
        result["pid"] = None
        result["state"] = ps
        result["cpu_percent"] = 0.0
        result["rss_mb"] = 0.0
        result["uptime_s"] = 0.0
        result["gpu_percent"] = None
        result["gpu_vram_mb"] = None
        result["error_message"] = None
    elif ps is not None:
        result["pid"] = ps.pid
        result["state"] = ps.state
        result["cpu_percent"] = ps.cpu_percent
        result["rss_mb"] = ps.rss_mb
        result["uptime_s"] = _uptime_s(ps)
        result["gpu_percent"] = ps.gpu_percent
        result["gpu_vram_mb"] = ps.gpu_vram_mb
        result["error_message"] = None
    else:
        result["pid"] = None
        result["state"] = "stopped"
        result["cpu_percent"] = 0.0
        result["rss_mb"] = 0.0
        result["uptime_s"] = 0.0
        result["gpu_percent"] = None
        result["gpu_vram_mb"] = None
        result["error_message"] = None
        if pid_dir is not None:
            error_marker = pid_dir / f"{entry['id']}.error"
            if error_marker.exists():
                result["state"] = "error"
                result["error_message"] = error_marker.read_text()
    return result
