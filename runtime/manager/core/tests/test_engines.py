"""Engines are detected, not declared — PRM-264.

`engines` on a node was a list an operator ticked at registration. A node that
claimed `llama_cpp` without the binary was discovered the usual way: a start
failed, at the moment somebody wanted the model. Live proof before this
shipped — `lab` and `local` are two managers on one machine and declared
disjoint sets, `{hf_serve, laya, tei}` and `{llama_cpp, sd_cpp}`, while the
machine had all five.

The detection deliberately has no table of its own.
`config.resolved_backend_binary` already names the exact executable
`lifecycle.start_instance` runs, so asking whether *that* resolves asks the
same question the launch will ask — and an operator who points a backend at a
binary inside a virtualenv gets that one checked.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from prometheus_manager_core.config import ManagerConfig
from prometheus_manager_core.engines import detect_engines, installed_backends
from prometheus_manager_core.registry import BACKENDS


@pytest.fixture
def config(tmp_path: Path) -> ManagerConfig:
    """A config whose every backend points at something that does not exist."""
    cfg = ManagerConfig()
    cfg.server.binary = str(tmp_path / "nowhere" / "llama-server")
    for backend in BACKENDS:
        if backend == "llama_cpp":
            continue
        cfg._backend_config(backend).binary = str(tmp_path / "nowhere" / backend)
    return cfg


def _fake_binary(path: Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\nexit 0\n")
    path.chmod(0o755)
    return str(path)


# ── Every backend is answered for, present or not ────────────────────────────


def test_every_known_backend_is_reported(config: ManagerConfig) -> None:
    """A short list would be read as "these are the ones it has" rather than
    "these are the ones we managed to check"."""
    assert [s.backend for s in detect_engines(config)] == list(BACKENDS)


def test_an_absent_engine_says_why(config: ManagerConfig) -> None:
    """A bare 'no' sends an operator sends an operator looking in the wrong place: a
    binary missing from PATH and a configured path that does not exist have
    different fixes."""
    for status in detect_engines(config):
        assert status.installed is False
        assert status.reason, f"{status.backend} is absent and does not say why"
        assert status.binary in status.reason


def test_a_configured_path_is_checked_at_that_path(config: ManagerConfig, tmp_path: Path) -> None:
    """The point of reading the launch command rather than a name: an operator
    who installs llama.cpp inside a virtualenv is told about that one."""
    config.server.binary = _fake_binary(tmp_path / "venv" / "bin" / "llama-server")

    found = next(s for s in detect_engines(config) if s.backend == "llama_cpp")
    assert found.installed is True
    assert found.path == config.server.binary


def test_a_path_that_is_not_executable_is_not_installed(
    config: ManagerConfig, tmp_path: Path
) -> None:
    """exec() needs the bit, so detection does too — otherwise the launch
    fails on something this said was fine."""
    binary = Path(_fake_binary(tmp_path / "bin" / "llama-server"))
    binary.chmod(0o644)
    config.server.binary = str(binary)

    found = next(s for s in detect_engines(config) if s.backend == "llama_cpp")
    assert found.installed is False
    assert "not executable" in (found.reason or "")


def test_a_bare_name_is_looked_up_on_path(config: ManagerConfig, tmp_path: Path) -> None:
    bin_dir = tmp_path / "onpath"
    _fake_binary(bin_dir / "hf-serve")
    config._backend_config("hf_serve").binary = "hf-serve"

    old = os.environ["PATH"]
    os.environ["PATH"] = f"{bin_dir}{os.pathsep}{old}"
    try:
        found = next(s for s in detect_engines(config) if s.backend == "hf_serve")
    finally:
        os.environ["PATH"] = old

    assert found.installed is True
    assert found.path == str(bin_dir / "hf-serve")


# ── The one engine whose binary is not the engine ────────────────────────────


def test_sglang_is_not_reported_present_just_because_python_exists(config: ManagerConfig) -> None:
    """sglang launches as `python3 -m sglang.launch_server`, so its configured
    binary is the interpreter. `which python3` succeeds on every machine in
    the fleet, which would report sglang installed everywhere — the detector
    asks whether the module imports instead.
    """
    import sys

    config._backend_config("sglang").binary = sys.executable

    found = next(s for s in detect_engines(config) if s.backend == "sglang")
    assert found.path == sys.executable, "the interpreter itself did resolve"
    assert found.installed is False
    assert "sglang" in (found.reason or "")


def test_the_module_check_is_a_real_import_check(config: ManagerConfig) -> None:
    """The negative control for the test above: the same mechanism says yes
    for a module that *is* importable, so a False there means something."""
    import sys

    from prometheus_manager_core.engines import _module_importable

    assert _module_importable(sys.executable, "json") is True
    assert _module_importable(sys.executable, "no_such_module_anywhere") is False


# ── The convenience view ─────────────────────────────────────────────────────


def test_installed_backends_is_just_the_present_ones(config: ManagerConfig, tmp_path: Path) -> None:
    config.server.binary = _fake_binary(tmp_path / "bin" / "llama-server")
    assert installed_backends(config) == ["llama_cpp"]


def test_nothing_installed_is_an_empty_list_not_an_error(config: ManagerConfig) -> None:
    """`[]` means it looked and found none — a real answer, and the one that
    distinguishes a scanned node from an unreported one."""
    assert installed_backends(config) == []


# ── PRM-271: an engine this node cannot run ──────────────────────────────────
#
# "Installed" and "could run here" are independent questions, and reporting
# only the first made the panel suggest three impossible installs on an Intel
# iMac: MLX has no binary to put on x86_64, so "mlx_lm.server is not on PATH"
# was true and useless. PRM-133 wrote the rule — "an option that cannot work
# must not look like one that can" — and left the fact in a docstring.

_INTEL_IMAC = {
    "os": "Darwin",
    "arch": "x86_64",
    "accelerator": {"kind": "metal", "name": "Intel Iris Pro Graphics 6200"},
}
_APPLE_SILICON = {"os": "Darwin", "arch": "arm64", "accelerator": {"kind": "metal"}}
_NVIDIA_LINUX = {"os": "Linux", "arch": "x86_64", "accelerator": {"kind": "cuda"}}


def _applicable(config: ManagerConfig, profile: dict | None) -> dict[str, bool | None]:
    return {s.backend: s.applicable for s in detect_engines(config, profile)}


def test_mlx_is_ruled_out_on_an_intel_mac(config: ManagerConfig) -> None:
    """The report that started this item, asserted against the iMac's own
    profile as the fleet stored it."""
    assert _applicable(config, _INTEL_IMAC)["mlx"] is False


def test_mlx_is_available_on_apple_silicon(config: ManagerConfig) -> None:
    """The negative control. A rule that refuses everything is not a rule."""
    assert _applicable(config, _APPLE_SILICON)["mlx"] is True


def test_the_cuda_engines_are_ruled_out_without_an_nvidia_card(config: ManagerConfig) -> None:
    """`lifecycle.py` says of both: "NOT verified against a real install
    (needs CUDA)". That is where this comes from — not from a guess here."""
    on_mac = _applicable(config, _APPLE_SILICON)
    assert on_mac["vllm"] is False
    assert on_mac["sglang"] is False

    on_nvidia = _applicable(config, _NVIDIA_LINUX)
    assert on_nvidia["vllm"] is True
    assert on_nvidia["sglang"] is True


def test_an_engine_with_no_hardware_requirement_runs_anywhere(config: ManagerConfig) -> None:
    """Absence from the table is the default on purpose: a constraint nobody
    can demonstrate must not be asserted."""
    for profile in (_INTEL_IMAC, _APPLE_SILICON, _NVIDIA_LINUX):
        applicable = _applicable(config, profile)
        for backend in ("llama_cpp", "sd_cpp", "hf_serve", "tei", "laya"):
            assert applicable[backend] is True, f"{backend} was ruled out somewhere"


def test_no_profile_means_unknown_and_not_yes(config: ManagerConfig) -> None:
    """The distinction this codebase keeps making. An impossibility nobody
    checked is not the same as one that does not apply, and `True` here would
    put MLX back on the x86 recommendation list for any node that has not
    reported its hardware yet."""
    applicable = _applicable(config, None)
    assert applicable["mlx"] is None
    assert applicable["vllm"] is None
    # ...but an engine with no requirement is knowable without a profile.
    assert applicable["llama_cpp"] is True


def test_what_the_hardware_would_need_is_reported_either_way(config: ManagerConfig) -> None:
    """Set whether or not it is met: "needs Apple Silicon" is the sentence an
    operator is owed when the option is missing *and* when it is offered."""
    for profile in (_INTEL_IMAC, _APPLE_SILICON):
        by_backend = {s.backend: s for s in detect_engines(config, profile)}
        assert by_backend["mlx"].requires == "Apple Silicon"
        assert by_backend["llama_cpp"].requires is None, "a constraint was invented"


def test_an_installed_engine_the_hardware_cannot_run_is_still_reported_installed(
    config: ManagerConfig, tmp_path: Path
) -> None:
    """Rare and deliberately not hidden. The probe runs even when the hardware
    rules an engine out, because skipping it would report a present engine as
    merely absent — and "installed where it cannot run" is worth saying."""
    config._backend_config("mlx").binary = _fake_binary(tmp_path / "bin" / "mlx_lm.server")

    mlx = next(s for s in detect_engines(config, _INTEL_IMAC) if s.backend == "mlx")
    assert mlx.installed is True
    assert mlx.applicable is False


def test_every_requirement_names_a_backend_this_platform_has(config: ManagerConfig) -> None:
    """A requirement for an engine that no longer exists is silently dead, and
    a typo in a key is a constraint that never applies to anything."""
    from prometheus_manager_core.engines import _REQUIREMENTS

    assert set(_REQUIREMENTS) <= set(BACKENDS)
