"""What this machine can actually launch — PRM-264.

`engines` on a node was **declared**: an operator ticked boxes when registering
it, and `fleet.py` says a NULL means "never declared", not "none". A node that
claims `llama_cpp` without the binary is discovered the usual way — a start
fails, at the moment somebody wanted the model.

`scanner.py` already knows every engine's signature, but it reads *running
processes*. The question here is a different one about the same eight things:
what is installed. The answer does not come from a new table either —
`config.resolved_backend_binary` already names, per backend, the exact
executable `lifecycle.start_instance` will run. Asking whether *that* resolves
is the same question the launch will ask, which is the only version of it worth
answering. An operator who points a backend at a binary in a venv gets that
binary checked, for free.

This does NOT replace the declared list. PRM-151 is the precedent: `is_active`
collapsed an operator's intent and an observation into one boolean, and the
next observation erased the intent. Declared engines are intent ("use these
here"), detected engines are observation ("these exist here"), and they are
worth most exactly when they disagree — which is the case this item exists to
make visible.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Any

from .registry import BACKENDS

if TYPE_CHECKING:  # pragma: no cover
    from .config import ManagerConfig

_PROBE_TIMEOUT_S = 3.0

# sglang is launched as `python3 -m sglang.launch_server`, so its configured
# binary is the interpreter — and `which python3` succeeds everywhere, which
# would report sglang present on every machine in the fleet. The real question
# for a `-m` launch is whether the module imports, so it gets asked.
_MODULE_LAUNCHED: dict[str, str] = {"sglang": "sglang"}


@dataclass(frozen=True)
class EngineRequirement:
    """What a machine must be for an engine to start at all — PRM-271.

    Impossibility, never advisability. `arch != arm64` means there is no MLX to
    install; no CUDA device means vLLM does not come up. Both are checkable
    against a profile. "llama.cpp runs here" and "llama.cpp runs *well* here"
    are different claims and only the first is derivable from a reading — an
    Intel iMac reports `metal` for an Iris Pro 6200, and what that is worth is
    PRM-265's problem at best.

    `why` reads as the object of "needs ...", because that is the sentence an
    operator is owed when an option is not offered.
    """

    why: str
    arch: tuple[str, ...] | None = None
    os: tuple[str, ...] | None = None
    accelerator: tuple[str, ...] | None = None

    def met_by(self, profile: dict[str, Any]) -> bool:
        if self.arch is not None and profile.get("arch") not in self.arch:
            return False
        if self.os is not None and profile.get("os") not in self.os:
            return False
        if self.accelerator is not None:
            kind = (profile.get("accelerator") or {}).get("kind")
            if kind not in self.accelerator:
                return False
        return True


# Sourced from this repo, not invented here: `lifecycle.py`'s `_build_vllm_cmd`
# and `_build_sglang_cmd` both carry *"NOT verified against a real install
# (needs CUDA)"*, and PRM-133's engine survey lists `vllm`/`sglang` as
# "CUDA-only" and `llama_cpp, mlx, sd_cpp` as "the three that run here today"
# on an Apple Silicon node. PRM-133 also wrote the rule this implements: *"an
# option that cannot work must not look like one that can."*
#
# An engine absent from this table has no hardware constraint — it is CPU-
# capable everywhere this platform runs. Absence is the default on purpose: a
# constraint nobody can demonstrate should not be asserted.
_REQUIREMENTS: dict[str, EngineRequirement] = {
    "mlx": EngineRequirement(why="Apple Silicon", arch=("arm64",), os=("Darwin",)),
    "vllm": EngineRequirement(why="an NVIDIA (CUDA) accelerator", accelerator=("cuda",)),
    "sglang": EngineRequirement(why="an NVIDIA (CUDA) accelerator", accelerator=("cuda",)),
}

_FIND_SPEC = (
    "import importlib.util, sys; sys.exit(0 if importlib.util.find_spec('{module}') else 1)"
)


@dataclass(frozen=True)
class EngineStatus:
    """One engine, and whether this machine could start it.

    `reason` is set whenever `installed` is False, because "no" without a
    reason sends an operator looking in the wrong place: a binary missing from
    PATH and a configured path that does not exist have different fixes.
    """

    backend: str
    installed: bool
    #: The command that was checked — what `lifecycle` would execute.
    binary: str
    #: Where it resolved to, when it did. An absolute path tells a system
    #: install from one inside a virtualenv, which is the usual surprise.
    path: str | None = None
    reason: str | None = None
    #: PRM-271: whether this machine could run it at all. `None` means nobody
    #: could tell — no profile to check against — which is not the same as
    #: yes, and an impossibility nobody demonstrated must not be asserted.
    applicable: bool | None = None
    #: What the hardware would have to be, for the engines that need one.
    #: Set whether or not the requirement is met: it is the sentence an
    #: operator is owed either way.
    requires: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _resolve(binary: str) -> str | None:
    """The path `subprocess.Popen` would reach, or None.

    A command containing a separator is a path and is never looked up on PATH
    — that is exec()'s own rule, and detection has to use it or it answers a
    question the launch will not ask.
    """
    if not binary:
        return None
    if os.sep in binary:
        return binary if os.path.isfile(binary) and os.access(binary, os.X_OK) else None
    return shutil.which(binary)


def _module_importable(interpreter: str, module: str) -> bool:
    """Whether *interpreter* can import *module*, asked in a subprocess.

    Importing it here would load someone else's package into the manager, and
    a heavy one (sglang pulls CUDA) into a process whose job is to stay up.
    """
    try:
        probe = subprocess.run(
            [interpreter, "-c", _FIND_SPEC.format(module=module)],
            capture_output=True,
            timeout=_PROBE_TIMEOUT_S,
            check=False,
        )
    except Exception:
        return False
    return probe.returncode == 0


def _applicability(backend: str, profile: dict[str, Any] | None) -> tuple[bool | None, str | None]:
    """Whether this machine could run *backend*, and what it would need.

    No entry in the table is a real "yes" and needs no profile: the engine runs
    on CPU everywhere this platform runs. An entry with no profile to check is
    `None` — unknown, which is the honest answer and not the convenient one.
    """
    requirement = _REQUIREMENTS.get(backend)
    if requirement is None:
        return True, None
    if profile is None:
        return None, requirement.why
    return requirement.met_by(profile), requirement.why


def _detect_one(
    backend: str, config: ManagerConfig, profile: dict[str, Any] | None
) -> EngineStatus:
    applicable, requires = _applicability(backend, profile)

    def status(installed: bool, **kw: Any) -> EngineStatus:
        return EngineStatus(backend, installed, applicable=applicable, requires=requires, **kw)

    binary = config.resolved_backend_binary(backend)
    if not binary:
        return status(False, binary=binary, reason="no launch command configured")

    # Probed even when the hardware rules it out. An engine that is installed
    # and cannot run here is a real thing to be told about, and skipping the
    # probe would report it as simply absent.
    path = _resolve(binary)
    if path is None:
        reason = (
            f"{binary} does not exist or is not executable"
            if os.sep in binary
            else f"{binary} is not on PATH"
        )
        return status(False, binary=binary, reason=reason)

    module = _MODULE_LAUNCHED.get(backend)
    if module is not None and not _module_importable(path, module):
        return status(
            False,
            binary=binary,
            path=path,
            reason=f"{binary} found, but it cannot import {module}",
        )

    return status(True, binary=binary, path=path)


def detect_engines(
    config: ManagerConfig, profile: dict[str, Any] | None = None
) -> list[EngineStatus]:
    """Every backend this platform knows, and what this machine can do with it.

    Two independent questions per engine: whether it is installed, and whether
    this hardware could run it (PRM-271). Collapsing them is what made the
    panel suggest three impossible installs on an Intel iMac — MLX has no
    binary to put on an x86_64 Mac, so "not on PATH" was true and useless.

    `profile` is the node's own reading (PRM-263). Without one, applicability
    is reported as unknown rather than assumed.

    Never raises, and never omits a backend: the caller is a heartbeat, and a
    short list would be read as "these are the ones it has" rather than "these
    are the ones we managed to check".
    """
    results: list[EngineStatus] = []
    for backend in BACKENDS:
        try:
            results.append(_detect_one(backend, config, profile))
        except Exception as exc:  # pragma: no cover — _detect_one swallows its own
            results.append(EngineStatus(backend, False, "", reason=f"probe failed: {exc}"))
    return results


def installed_backends(config: ManagerConfig) -> list[str]:
    """Just the names, in `BACKENDS` order — what the node reports as present."""
    return [status.backend for status in detect_engines(config) if status.installed]
