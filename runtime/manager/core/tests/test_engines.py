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
