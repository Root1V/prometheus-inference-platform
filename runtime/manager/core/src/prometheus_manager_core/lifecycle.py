"""Instance lifecycle: start, stop, pause, resume, restart, deregister.

Implements: memory/specs/008-llama-server-manager.md — AC-4, AC-5, AC-6, AC-6b, AC-6c, AC-6d,
            AC-7, AC-8, AC-9, AC-10, AC-17, AC-19
Implements: memory/specs/018-observability-telemetry.md — AC-3
"""

from __future__ import annotations

import os
import platform
import shutil
import signal
import socket
import subprocess
import time
from pathlib import Path

import httpx
import psutil

from .config import ManagerConfig
from .registry import Registry
from .scanner import ProcessState, _normalize_probe_host, scan
from .telemetry import get_logger

logger = get_logger(__name__)

_HEALTH_TIMEOUT = 2.0
_PORT_SCAN_RANGE = 100  # ports to probe above the preferred port


class LifecycleError(Exception):
    """Raised when a lifecycle operation cannot be completed safely."""


def _find_free_port(preferred: int) -> int:
    """Return the first free TCP port starting from *preferred*.

    Tries *preferred* first, then preferred+1 … preferred+_PORT_SCAN_RANGE.
    Raises LifecycleError if no free port is found in that range.
    """
    for candidate in range(preferred, preferred + _PORT_SCAN_RANGE):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("127.0.0.1", candidate))
                return candidate
            except OSError:
                continue
    raise LifecycleError(
        f"No free port found in range [{preferred}, {preferred + _PORT_SCAN_RANGE})"
    )


def start_instance(
    model_id: str,
    config: ManagerConfig,
    registry: Registry,
) -> ProcessState:
    """Spawn a new llama-server instance.

    Implements: memory/specs/008-llama-server-manager.md — AC-4, AC-5, AC-9, AC-10, AC-19
    """
    # AC-19: host enforcement
    config.validate()

    # AC-10: model must be in registry
    entry = registry.get(model_id)
    if entry is None:
        raise LifecycleError(f"Model '{model_id}' not found in registry")

    # AC-9: already running?
    existing = _find_running(model_id, config)
    if existing is not None:
        raise LifecycleError(f"Instance already running for {model_id} (PID {existing.pid})")

    # Find a free port, starting from the registry's preferred port (AC-19)
    port = _find_free_port(entry.port)
    if port != entry.port:
        logger.info(
            "lifecycle.port_remap",
            model_id=model_id,
            preferred=entry.port,
            assigned=port,
        )
    # Persist the chosen port back to the registry so the next start uses it
    # as the preferred value and so the gateway can discover the correct address.
    registry.update(model_id, port=port, backend_url=f"http://127.0.0.1:{port}")

    # Build command
    binary = str(config.resolved_binary)
    # Allow overriding the bind host for llama-server (default: 127.0.0.1).
    # In some Podman/VM networking setups the container needs the instance
    # to bind on 0.0.0.0 so `host.containers.internal` can reach it.
    bind_host = os.getenv("PROMETHEUS_LLAMA_BIND_HOST", "127.0.0.1")

    system = platform.system()
    has_nvidia = shutil.which("nvidia-smi") is not None

    if system == "Darwin":
        gpu_layers = "-1"
    elif has_nvidia:
        gpu_layers = "999"
    else:
        gpu_layers = "0"

    cmd = [
        binary,
        "--model",
        entry.path,
        "--alias",
        entry.id,
        "--port",
        str(port),
        "--host",
        bind_host,
        "--ctx-size",
        str(entry.context_length),
        "--metrics",
        "--n-gpu-layers",
        gpu_layers,
        "--threads",
        str(os.cpu_count() or 4),
    ]

    # Optimizaciones sólo para CUDA
    if has_nvidia:
        cmd.extend(
            [
                "--flash-attn",
                "on",
                "--batch-size",
                "8192",
                "--ubatch-size",
                "1024",
            ]
        )

    # Ensure directories exist
    log_dir = config.resolved_log_dir
    pid_dir = config.resolved_pid_dir
    log_dir.mkdir(parents=True, exist_ok=True)
    pid_dir.mkdir(parents=True, exist_ok=True)

    log_path = log_dir / f"{model_id}.log"
    pid_path = pid_dir / f"{model_id}.pid"

    logger.info("lifecycle.start", model_id=model_id, cmd=cmd)
    with open(log_path, "a") as log_fh:
        proc = subprocess.Popen(
            cmd,
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )

    # Write PID file
    pid_path.write_text(str(proc.pid))

    # AC-5: wait for /health to return 200 within start_timeout_s
    deadline = time.monotonic() + config.server.start_timeout_s
    # Resolve probe host using the same logic as the scanner so both paths
    # behave identically: proxy_host (PMGR_PROXY_HOST / manager.toml [api])
    # takes priority; otherwise bind_host is normalised to a connectable address.
    probe_host = _normalize_probe_host(bind_host, config.api.proxy_host)

    while time.monotonic() < deadline:
        time.sleep(1.0)
        # Check process is still alive
        if proc.poll() is not None:
            raise LifecycleError(
                f"Process for {model_id} exited unexpectedly (code {proc.returncode})"
            )
        try:
            resp = httpx.get(
                f"http://{probe_host}:{port}/health",
                timeout=_HEALTH_TIMEOUT,
            )
            if resp.status_code == 200:
                logger.info("lifecycle.ready", model_id=model_id, pid=proc.pid)
                # AC-8 (spec 010): mark model as discoverable when healthy
                registry.update(model_id, discovery=True)
                # Return the live state
                states = scan(pid_dir, {model_id}, config.api.proxy_host)
                for s in states:
                    if s.pid == proc.pid:
                        return s
                break
        except (httpx.ConnectError, httpx.ConnectTimeout):
            pass

    # Timeout — AC-5: kill the process
    try:
        proc.terminate()
        proc.wait(timeout=5)
    except Exception:
        proc.kill()
    finally:
        pid_path.unlink(missing_ok=True)

    raise LifecycleError(
        f"Timed out waiting for {model_id} to become healthy after {config.server.start_timeout_s}s"
    )


def stop_instance(
    model_id: str,
    config: ManagerConfig,
    registry: Registry,
    *,
    _require_running: bool = True,
) -> None:
    """Gracefully stop a running instance.

    Implements: memory/specs/008-llama-server-manager.md — AC-6, AC-7, AC-8
    """
    existing = _find_running(model_id, config)
    if existing is None:
        if _require_running:
            raise LifecycleError(f"No running instance found for {model_id}")
        return  # already stopped — noop

    pid = existing.pid
    pid_path = config.resolved_pid_dir / f"{model_id}.pid"

    # AC-8 (PID integrity): confirm the PID file still matches
    _verify_pid_file(pid_path, pid, model_id)

    try:
        proc = psutil.Process(pid)
    except psutil.NoSuchProcess:
        pid_path.unlink(missing_ok=True)
        return

    logger.info("lifecycle.stop", model_id=model_id, pid=pid)
    proc.send_signal(signal.SIGTERM)
    try:
        proc.wait(timeout=config.server.stop_timeout_s)
    except psutil.TimeoutExpired:
        logger.warning("lifecycle.sigkill", model_id=model_id, pid=pid)
        proc.kill()
        proc.wait(timeout=5)

    pid_path.unlink(missing_ok=True)
    # AC-9 (spec 010): remove from discovery when stopped
    import contextlib

    with contextlib.suppress(Exception):
        registry.update(model_id, discovery=False)


def pause_instance(model_id: str, config: ManagerConfig) -> None:
    """Send SIGSTOP to pause an instance.

    Implements: memory/specs/008-llama-server-manager.md — AC-6b

    Note: pause does not clear discovery. The process stops responding; the
    gateway circuit breaker will trip after timeout. Clearing discovery would
    require a registry parameter — deferred to a future spec.
    """
    existing = _find_running(model_id, config)
    if existing is None:
        raise LifecycleError(f"No running instance found for {model_id}")
    try:
        os.kill(existing.pid, signal.SIGSTOP)
        logger.info("lifecycle.pause", model_id=model_id, pid=existing.pid)
    except ProcessLookupError as exc:
        raise LifecycleError(f"Could not pause {model_id}: {exc}") from exc


def resume_instance(model_id: str, config: ManagerConfig) -> None:
    """Send SIGCONT to resume a paused instance.

    Implements: memory/specs/008-llama-server-manager.md — AC-6c
    """
    pid_dir = config.resolved_pid_dir
    states = scan(pid_dir, proxy_host=config.api.proxy_host)
    for s in states:
        if s.alias == model_id or s.model_id == model_id:
            try:
                os.kill(s.pid, signal.SIGCONT)
                logger.info("lifecycle.resume", model_id=model_id, pid=s.pid)
                return
            except ProcessLookupError as exc:
                raise LifecycleError(f"Could not resume {model_id}: {exc}") from exc
    raise LifecycleError(f"No instance found for {model_id} (not running or paused)")


def restart_instance(
    model_id: str,
    config: ManagerConfig,
    registry: Registry,
) -> ProcessState:
    """Stop then start — returns new ProcessState.

    Implements: memory/specs/008-llama-server-manager.md — AC-8
    """
    stop_instance(model_id, config, registry, _require_running=False)
    return start_instance(model_id, config, registry)


def deregister_instance(
    model_id: str,
    config: ManagerConfig,
    registry: Registry,
) -> None:
    """Stop (if running) then remove from registry.

    Implements: memory/specs/008-llama-server-manager.md — AC-6d
    """
    stop_instance(model_id, config, registry, _require_running=False)
    if registry.get(model_id) is not None:
        registry.remove(model_id)


# ── helpers ──────────────────────────────────────────────────────────────────


def _find_running(model_id: str, config: ManagerConfig) -> ProcessState | None:
    states = scan(config.resolved_pid_dir, proxy_host=config.api.proxy_host)
    for s in states:
        if s.alias == model_id or s.model_id == model_id:
            return s
    return None


def _verify_pid_file(pid_path: Path, expected_pid: int, model_id: str) -> None:
    """Implements: memory/specs/008-llama-server-manager.md — security / PID integrity"""
    try:
        stored = int(pid_path.read_text().strip())
    except (FileNotFoundError, ValueError):
        return  # no PID file — unmanaged process, allow stop anyway
    if stored != expected_pid:
        raise LifecycleError(
            f"PID file mismatch for {model_id}: stored={stored}, running={expected_pid}. "
            "Possible PID reuse — aborting to avoid stopping wrong process."
        )
