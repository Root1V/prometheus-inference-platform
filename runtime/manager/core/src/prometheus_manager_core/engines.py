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


def _detect_one(backend: str, config: ManagerConfig) -> EngineStatus:
    binary = config.resolved_backend_binary(backend)
    if not binary:
        return EngineStatus(backend, False, binary, reason="no launch command configured")

    path = _resolve(binary)
    if path is None:
        reason = (
            f"{binary} does not exist or is not executable"
            if os.sep in binary
            else f"{binary} is not on PATH"
        )
        return EngineStatus(backend, False, binary, reason=reason)

    module = _MODULE_LAUNCHED.get(backend)
    if module is not None and not _module_importable(path, module):
        return EngineStatus(
            backend,
            False,
            binary,
            path=path,
            reason=f"{binary} found, but it cannot import {module}",
        )

    return EngineStatus(backend, True, binary, path=path)


def detect_engines(config: ManagerConfig) -> list[EngineStatus]:
    """Every backend this platform knows, and whether this machine has it.

    Never raises, and never omits a backend: the caller is a heartbeat, and a
    short list would be read as "these are the ones it has" rather than "these
    are the ones we managed to check".
    """
    results: list[EngineStatus] = []
    for backend in BACKENDS:
        try:
            results.append(_detect_one(backend, config))
        except Exception as exc:  # pragma: no cover — _detect_one swallows its own
            results.append(EngineStatus(backend, False, "", reason=f"probe failed: {exc}"))
    return results


def installed_backends(config: ManagerConfig) -> list[str]:
    """Just the names, in `BACKENDS` order — what the node reports as present."""
    return [status.backend for status in detect_engines(config) if status.installed]
