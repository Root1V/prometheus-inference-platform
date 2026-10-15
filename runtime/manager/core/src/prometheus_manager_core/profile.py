"""What this machine is — PRM-263.

A node has always recorded *where* it is (`manager_url`), what the operator
calls it (`node_type`, free text) and what it costs per hour. Never what it
has. The one place the hardware was looked at is `capacity.py`, which reads
total RAM on every launch to warn at 85% and refuse at 95% — and then discards
it, so the coordinator can be told "this would OOM" at the instant a model
starts and cannot answer "what does this machine have" at any other moment.

This reports facts and judges none of them. Whether a profile is good enough
for a given model is PRM-265's question, and keeping the two apart is what
stops a reading ("no accelerator") from being stored as a verdict ("cannot
serve"), which ages differently and worse.

Every field is optional on purpose. A probe that cannot answer says `None`,
which is a different fact from zero — a machine with no CUDA reports
`accelerator.kind = "none"`, while a machine where the probe failed reports no
accelerator at all, and only the second is a reason to go and look.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import psutil

_PROBE_TIMEOUT_S = 3.0


@dataclass
class Accelerator:
    """What can do matrix maths here, if anything.

    `kind` is `"none"` when we looked and there is nothing — not when we could
    not look. `memory_mb` is `None` on unified-memory machines rather than a
    copy of system RAM: the same number under two names is the kind of thing
    that later disagrees with itself.
    """

    kind: str
    name: str | None = None
    memory_mb: int | None = None
    unified_memory: bool = False


@dataclass
class DeviceProfile:
    os: str
    os_release: str
    arch: str
    cpu_model: str | None = None
    cpu_cores_physical: int | None = None
    cpu_cores_logical: int | None = None
    ram_total_mb: int | None = None
    ram_available_mb: int | None = None
    disk_total_mb: int | None = None
    disk_free_mb: int | None = None
    disk_path: str | None = None
    accelerator: Accelerator | None = None
    # Which probes failed, named. An empty profile with no explanation is the
    # shape that gets read as "this machine has nothing".
    probe_errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _run(cmd: list[str]) -> str | None:
    try:
        out = subprocess.run(
            cmd, capture_output=True, text=True, timeout=_PROBE_TIMEOUT_S, check=False
        )
    except Exception:
        return None
    return out.stdout.strip() or None


def _cpu_model() -> str | None:
    system = platform.system()
    if system == "Darwin":
        return _run(["sysctl", "-n", "machdep.cpu.brand_string"])
    if system == "Linux":
        try:
            for line in Path("/proc/cpuinfo").read_text().splitlines():
                if line.startswith("model name"):
                    return line.split(":", 1)[1].strip()
        except Exception:
            return None
    return platform.processor() or None


def _mac_gpu_name() -> str | None:
    """The GPU on an Intel Mac, or None rather than a guess.

    `system_profiler` is slow enough to matter, which the five-minute cache on
    the caller is what makes acceptable. None when it does not answer in time:
    the accelerator is still reported as `metal`, just unnamed, because whether
    Metal exists and what the chip is called are different questions.
    """
    out = _run(["system_profiler", "-json", "SPDisplaysDataType"])
    if not out:
        return None
    try:
        import json as _json

        displays = _json.loads(out).get("SPDisplaysDataType") or []
        for card in displays:
            name = card.get("sppci_model") or card.get("_name")
            if name:
                return str(name)
    except Exception:
        return None
    return None


def _accelerator() -> tuple[Accelerator | None, str | None]:
    """(what we found, what went wrong) — never both."""
    system = platform.system()

    if system == "Darwin":
        # Apple Silicon shares one pool with the CPU, which is why the model a
        # machine can hold is bounded by RAM rather than by a separate VRAM
        # figure. An Intel Mac has Metal too, but a discrete or integrated GPU
        # behind it — reported as what it is, not judged here.
        unified = platform.machine() == "arm64"
        if unified:
            # On an SoC the CPU brand string *is* the GPU: "Apple M4 Max".
            return Accelerator(kind="metal", name=_cpu_model(), unified_memory=True), None
        # PRM-263 follow-up: on Intel it is not. The first build reported
        # "Intel(R) Core(TM) i5-5675R CPU" as the accelerator on a machine whose
        # GPU is an Iris Pro 6200 — a wrong name is worse than no name, because
        # the matcher downstream would be reasoning about the wrong silicon.
        return Accelerator(kind="metal", name=_mac_gpu_name(), unified_memory=False), None

    if shutil.which("nvidia-smi"):
        out = _run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"])
        if out is None:
            return None, "nvidia-smi found but did not answer"
        first = out.splitlines()[0]
        name, _, mem = first.partition(",")
        try:
            memory_mb = int(float(mem.strip()))
        except ValueError:
            memory_mb = None
        return Accelerator(kind="cuda", name=name.strip(), memory_mb=memory_mb), None

    if shutil.which("rocm-smi"):
        return Accelerator(kind="rocm", name=_run(["rocm-smi", "--showproductname"])), None

    return Accelerator(kind="none"), None


def device_profile(models_path: str | os.PathLike[str] | None = None) -> DeviceProfile:
    """Read this machine. Never raises: a half-answer beats an exception here,
    because the caller is a heartbeat and the alternative to a partial profile
    is no profile at all."""
    errors: list[str] = []
    profile = DeviceProfile(
        os=platform.system(),
        os_release=platform.release(),
        arch=platform.machine(),
        cpu_model=_cpu_model(),
    )

    try:
        profile.cpu_cores_physical = psutil.cpu_count(logical=False)
        profile.cpu_cores_logical = psutil.cpu_count(logical=True)
    except Exception as exc:
        errors.append(f"cpu: {exc}")

    try:
        memory = psutil.virtual_memory()
        profile.ram_total_mb = int(memory.total / (1024 * 1024))
        profile.ram_available_mb = int(memory.available / (1024 * 1024))
    except Exception as exc:
        errors.append(f"ram: {exc}")

    # The disk that matters is the one weights land on, not `/`. They are the
    # same mount on most machines and the whole question on the ones where a
    # model directory is an external drive.
    target = Path(models_path) if models_path else Path.cwd()
    try:
        probe = target if target.exists() else target.anchor or Path.cwd()
        usage = psutil.disk_usage(str(probe))
        profile.disk_total_mb = int(usage.total / (1024 * 1024))
        profile.disk_free_mb = int(usage.free / (1024 * 1024))
        profile.disk_path = str(probe)
    except Exception as exc:
        errors.append(f"disk: {exc}")

    try:
        accelerator, failure = _accelerator()
        profile.accelerator = accelerator
        if failure:
            errors.append(f"accelerator: {failure}")
    except Exception as exc:
        errors.append(f"accelerator: {exc}")

    profile.probe_errors = errors
    return profile
