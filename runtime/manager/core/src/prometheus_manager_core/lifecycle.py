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
from .registry import Registry, RegistryEntry
from .scanner import ProcessState, _normalize_probe_host, scan
from .telemetry import get_logger

logger = get_logger(__name__)

_HEALTH_TIMEOUT = 2.0
_PORT_SCAN_RANGE = 100  # ports to probe above the preferred port


def _error_marker_path(pid_dir: Path, model_id: str) -> Path:
    return pid_dir / f"{model_id}.error"


def _write_error_marker(pid_dir: Path, model_id: str, message: str) -> None:
    """Persist why the last start attempt failed, so the dashboard/CLI can
    show "error" instead of indistinguishable-from-never-started "stopped"
    once the crashed process is gone. Cleared on the next successful start
    or explicit stop — see _clear_error_marker.
    """
    pid_dir.mkdir(parents=True, exist_ok=True)
    _error_marker_path(pid_dir, model_id).write_text(message)


def _clear_error_marker(pid_dir: Path, model_id: str) -> None:
    _error_marker_path(pid_dir, model_id).unlink(missing_ok=True)


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


def _build_llama_cpp_cmd(binary: str, entry: RegistryEntry, port: int, bind_host: str) -> list[str]:
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

    # CUDA-only tuning.
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

    # RM-09: modality-specific flags. Only llama_cpp dispatches on modality
    # today — mlx/vllm/sglang accept the field but don't act on it yet.
    if entry.modality == "embedding":
        cmd.append("--embedding")
    elif entry.modality == "vision" and entry.mmproj_path:
        cmd.extend(["--mmproj", entry.mmproj_path])

    return cmd


def _build_mlx_cmd(binary: str, entry: RegistryEntry, port: int, bind_host: str) -> list[str]:
    """mlx_lm.server — verified against `mlx_lm.server --help` (mlx-lm on PyPI).

    No --alias or --ctx-size equivalent: mlx_lm.server derives context length
    from the model's own config.json and has no served-name concept — the
    manager tracks identity via the PID file instead (see scanner.py).
    """
    return [binary, "--model", entry.path, "--host", bind_host, "--port", str(port)]


def _build_vllm_cmd(binary: str, entry: RegistryEntry, port: int, bind_host: str) -> list[str]:
    """vllm serve — NOT verified against a real vllm install (needs CUDA; see
    memory/wiki/inference-engines.md RM-06). Flags per vLLM's documented CLI:
    model is a positional arg to the `serve` subcommand, --served-model-name
    registers entry.id as the OpenAI-API model name (llama.cpp's --alias
    equivalent), --max-model-len caps context length.
    """
    return [
        binary,
        "serve",
        entry.path,
        "--served-model-name",
        entry.id,
        "--host",
        bind_host,
        "--port",
        str(port),
        "--max-model-len",
        str(entry.context_length),
    ]


def _build_sglang_cmd(binary: str, entry: RegistryEntry, port: int, bind_host: str) -> list[str]:
    """python3 -m sglang.launch_server — NOT verified against a real sglang
    install (needs CUDA; see memory/wiki/inference-engines.md RM-06). Flags
    per SGLang's documented CLI: --model-path (not --model), --served-model-name
    for the OpenAI-API name, --context-length for max context.
    """
    return [
        binary,
        "-m",
        "sglang.launch_server",
        "--model-path",
        entry.path,
        "--served-model-name",
        entry.id,
        "--host",
        bind_host,
        "--port",
        str(port),
        "--context-length",
        str(entry.context_length),
    ]


_COMMAND_BUILDERS = {
    "llama_cpp": _build_llama_cpp_cmd,
    "mlx": _build_mlx_cmd,
    "vllm": _build_vllm_cmd,
    "sglang": _build_sglang_cmd,
}


def start_instance(
    model_id: str,
    config: ManagerConfig,
    registry: Registry,
) -> ProcessState:
    """Spawn a new inference server instance for the given model.

    Dispatches to the launch command for entry.backend (llama_cpp/mlx/vllm/
    sglang) — see memory/wiki/inference-engines.md (RM-06).

    Implements: memory/specs/008-llama-server-manager.md — AC-4, AC-5, AC-9, AC-10, AC-19
    """
    # AC-19: host enforcement
    config.validate()

    # AC-10: model must be in registry
    entry = registry.get(model_id)
    if entry is None:
        raise LifecycleError(f"Model '{model_id}' not found in registry")

    builder = _COMMAND_BUILDERS.get(entry.backend)
    if builder is None:
        raise LifecycleError(f"Unknown backend {entry.backend!r} for model '{model_id}'")

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
    binary = config.resolved_backend_binary(entry.backend)
    start_timeout_s = config.resolved_backend_start_timeout_s(entry.backend)
    # Allow overriding the bind host (default: 127.0.0.1).
    # In some Podman/VM networking setups the container needs the instance
    # to bind on 0.0.0.0 so `host.containers.internal` can reach it.
    bind_host = os.getenv("PROMETHEUS_LLAMA_BIND_HOST", "127.0.0.1")

    cmd = builder(binary, entry, port, bind_host)

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
    deadline = time.monotonic() + start_timeout_s
    # Resolve probe host using the same logic as the scanner so both paths
    # behave identically: proxy_host (PMGR_PROXY_HOST / manager.toml [api])
    # takes priority; otherwise bind_host is normalised to a connectable address.
    probe_host = _normalize_probe_host(bind_host, config.api.proxy_host)

    while time.monotonic() < deadline:
        time.sleep(1.0)
        # Check process is still alive
        if proc.poll() is not None:
            pid_path.unlink(missing_ok=True)
            message = f"Process for {model_id} exited unexpectedly (code {proc.returncode})"
            _write_error_marker(pid_dir, model_id, message)
            raise LifecycleError(message)
        try:
            resp = httpx.get(
                f"http://{probe_host}:{port}/health",
                timeout=_HEALTH_TIMEOUT,
            )
            if resp.status_code == 200:
                logger.info("lifecycle.ready", model_id=model_id, pid=proc.pid)
                # AC-8 (spec 010): mark model as discoverable when healthy
                registry.update(model_id, discovery=True)
                _clear_error_marker(pid_dir, model_id)
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

    message = f"Timed out waiting for {model_id} to become healthy after {start_timeout_s}s"
    _write_error_marker(pid_dir, model_id, message)
    raise LifecycleError(message)


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
        # A stop request — even a noop one from restart/deregister — is a
        # deliberate operator action, so it clears any stale "error" status
        # from a previous failed start (matches the success-path clear in
        # start_instance).
        _clear_error_marker(config.resolved_pid_dir, model_id)
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
        _clear_error_marker(config.resolved_pid_dir, model_id)
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
    _clear_error_marker(config.resolved_pid_dir, model_id)
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
