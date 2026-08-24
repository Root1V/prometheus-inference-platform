"""Process scanner — discover and probe running llama-server instances.

Implements: memory/specs/008-llama-server-manager.md — AC-1, AC-2, AC-14
"""

from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import httpx
import psutil


State = Literal["loading", "ready", "error", "stopped", "paused", "unknown"]

_HEALTH_TIMEOUT = 2.0  # seconds
_YOUNG_PROCESS_THRESHOLD = 30  # seconds — loading grace period


@dataclass
class ProcessState:
    """Live state of a detected llama-server process.

    Implements: memory/specs/008-llama-server-manager.md — Data Model / ProcessState
    """

    pid: int
    model_id: str | None
    alias: str
    port: int
    model_path: str
    host: str
    state: State
    cpu_percent: float
    rss_mb: float
    started_at: datetime
    managed: bool
    gpu_percent: float | None = None
    gpu_vram_mb: float | None = None
    cpu_history: list[float] = field(default_factory=list)
    rss_history: list[float] = field(default_factory=list)


# In-process history buffer for sparklines (keyed by PID)
_history: dict[int, dict[str, list[float]]] = defaultdict(lambda: {"cpu": [], "rss": []})
_HISTORY_LEN = 10

# Per-PID Process object cache so that cpu_percent(interval=None) accumulates
# a meaningful baseline across scan() calls.  psutil's cpu_percent() always
# returns 0.0 on the very first call for a Process object (it needs to store
# the baseline CPU times first).  By caching our own Process objects we ensure
# the second and later scan() calls return the real inter-scan delta.
_proc_cache: dict[int, psutil.Process] = {}


def _normalize_probe_host(bind_host: str, proxy_host: str = "") -> str:
    """Resolve the host to use for HTTP health probing.

    Single source of truth used by both scan() and lifecycle.start_instance().

    proxy_host (PMGR_PROXY_HOST / config.api.proxy_host) takes priority — used
    when the Manager runs inside a container and must reach llama-server via a
    host alias (e.g. host.containers.internal).

    When proxy_host is not set, bind-addresses are normalised to a connectable
    loopback: 0.0.0.0 / "" → 127.0.0.1, :: → ::1.
    """
    if proxy_host:
        return proxy_host
    if bind_host in ("0.0.0.0", ""):
        return "127.0.0.1"
    if bind_host == "::":
        return "::1"
    return bind_host


def scan(
    pid_dir: Path,
    registry_ids: set[str] | None = None,
    proxy_host: str = "",
) -> list[ProcessState]:
    """Return ProcessState for every running llama-server process.

    proxy_host — pass config.api.proxy_host so the health probe uses the
    correct address when Manager runs inside a container.
    Comes from manager.toml [api] proxy_host, overrideable via PMGR_PROXY_HOST.

    Implements: memory/specs/008-llama-server-manager.md — AC-1, AC-2, AC-14
    """
    states: list[ProcessState] = []
    seen_pids: set[int] = set()

    for proc in psutil.process_iter(
        ["pid", "name", "cmdline", "memory_info", "status", "create_time"]
    ):
        try:
            info = proc.info
            cmdline: list[str] = info.get("cmdline") or []
            if not _is_llama_server(info.get("name", ""), cmdline):
                continue

            pid: int = info["pid"]
            alias = _extract_arg(cmdline, "--alias")
            port_str = _extract_arg(cmdline, "--port")
            port = int(port_str) if port_str.isdigit() else 0
            model_path = _extract_arg(cmdline, "--model")
            host = _extract_arg(cmdline, "--host") or "127.0.0.1"

            # cpu_percent(interval=None) returns 0.0 on the first call for a
            # Process object (it only stores the baseline).  Subsequent calls
            # on the SAME object return the real delta.  We maintain our own
            # _proc_cache so the baseline survives across scan() invocations.
            seen_pids.add(pid)
            if pid not in _proc_cache:
                try:
                    _proc_cache[pid] = psutil.Process(pid)
                    _proc_cache[pid].cpu_percent(interval=None)  # prime — returns 0
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
            try:
                cpu = _proc_cache[pid].cpu_percent(interval=None)
            except (psutil.NoSuchProcess, psutil.AccessDenied, KeyError):
                cpu = 0.0
            mem_info = info.get("memory_info")
            rss_mb = (mem_info.rss / (1024 * 1024)) if mem_info else 0.0
            create_time = info.get("create_time") or time.time()
            started_at = datetime.fromtimestamp(create_time, tz=timezone.utc)
            ps_status = info.get("status", "")

            # Determine managed state
            pid_file = pid_dir / f"{alias}.pid"
            managed = _is_managed(pid_file, pid)

            # Determine model_id from registry
            model_id: str | None = (
                alias if (registry_ids is not None and alias in registry_ids) else None
            )
            if model_id is None and alias:
                model_id = None  # orphan

            # Update history buffers
            _history[pid]["cpu"].append(cpu)
            _history[pid]["rss"].append(rss_mb)
            _history[pid]["cpu"] = _history[pid]["cpu"][-_HISTORY_LEN:]
            _history[pid]["rss"] = _history[pid]["rss"][-_HISTORY_LEN:]

            # Determine state
            if ps_status == psutil.STATUS_STOPPED:
                state: State = "paused"
            else:
                state = _probe_health(host, port, create_time, proxy_host)

            states.append(
                ProcessState(
                    pid=pid,
                    model_id=model_id,
                    alias=alias,
                    port=port,
                    model_path=model_path,
                    host=host,
                    state=state,
                    cpu_percent=cpu,
                    rss_mb=rss_mb,
                    started_at=started_at,
                    managed=managed,
                    cpu_history=list(_history[pid]["cpu"]),
                    rss_history=list(_history[pid]["rss"]),
                )
            )
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    # Evict stale PIDs from our cache to avoid memory leaks
    for stale_pid in list(_proc_cache.keys()):
        if stale_pid not in seen_pids:
            del _proc_cache[stale_pid]

    return states


def _is_llama_server(name: str, cmdline: list[str]) -> bool:
    if "llama-server" in name:
        return True
    return any("llama-server" in part for part in cmdline) and any(
        "--port" in part for part in cmdline
    )


def _extract_arg(cmdline: list[str], flag: str) -> str:
    for i, part in enumerate(cmdline):
        if part == flag and i + 1 < len(cmdline):
            return cmdline[i + 1]
        if part.startswith(f"{flag}="):
            return part.split("=", 1)[1]
    return ""


def _is_managed(pid_file: Path, pid: int) -> bool:
    """Implements: memory/specs/008-llama-server-manager.md — AC-14"""
    try:
        stored = int(pid_file.read_text().strip())
        return stored == pid
    except (FileNotFoundError, ValueError):
        return False


def _probe_health(host: str, port: int, create_time: float, proxy_host: str = "") -> State:
    if port == 0:
        return "unknown"
    probe_host = _normalize_probe_host(host, proxy_host)
    try:
        resp = httpx.get(
            f"http://{probe_host}:{port}/health",
            timeout=_HEALTH_TIMEOUT,
        )
        if resp.status_code == 200:
            return "ready"
        return "error"
    except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout):
        age = time.time() - create_time
        if age < _YOUNG_PROCESS_THRESHOLD:
            return "loading"
        return "error"
    except Exception:
        return "unknown"
