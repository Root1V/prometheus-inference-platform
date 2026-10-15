"""PRM-263: the node says what it is.

The Linux paths are exercised by substitution rather than by a Linux machine,
which is the honest limit of this file: it proves the parsing and the branch
choice, not that `nvidia-smi` on a real card prints what we think it does.
That second thing is what the laptop is for, and saying so here stops a green
suite from being read as a claim it never made.
"""

from __future__ import annotations

from prometheus_manager_core import profile as mod


def test_this_machine_answers_the_questions_a_match_will_ask():
    """The fields PRM-265 needs, read from whatever host runs the suite."""
    p = mod.device_profile()
    assert p.os and p.arch
    assert p.ram_total_mb and p.ram_total_mb > 0
    assert p.disk_free_mb is not None and p.disk_free_mb >= 0
    assert p.accelerator is not None
    assert p.probe_errors == [], f"a probe failed on this host: {p.probe_errors}"


def test_a_machine_with_no_accelerator_says_none_rather_than_nothing(monkeypatch):
    """`none` and absent are different facts and only one is worth acting on:
    "we looked and there is no GPU" is a profile, "we could not look" is a
    machine to go and check."""
    monkeypatch.setattr(mod.platform, "system", lambda: "Linux")
    monkeypatch.setattr(mod.shutil, "which", lambda _: None)

    accelerator, failure = mod._accelerator()
    assert accelerator is not None and accelerator.kind == "none"
    assert failure is None


def test_a_cuda_card_reports_its_own_memory(monkeypatch):
    monkeypatch.setattr(mod.platform, "system", lambda: "Linux")
    monkeypatch.setattr(
        mod.shutil, "which", lambda name: "/usr/bin/nvidia-smi" if name == "nvidia-smi" else None
    )
    monkeypatch.setattr(mod, "_run", lambda cmd: "NVIDIA GeForce RTX 4070 Laptop GPU, 8188")

    accelerator, failure = mod._accelerator()
    assert failure is None
    assert accelerator is not None
    assert accelerator.kind == "cuda"
    assert accelerator.name == "NVIDIA GeForce RTX 4070 Laptop GPU"
    assert accelerator.memory_mb == 8188
    assert accelerator.unified_memory is False


def test_a_probe_that_runs_but_says_nothing_is_an_error_not_an_absence(monkeypatch):
    """`nvidia-smi` present and silent means a driver problem, which is a thing
    to report. Returning `kind: none` there would describe a machine with a GPU
    as a machine without one."""
    monkeypatch.setattr(mod.platform, "system", lambda: "Linux")
    monkeypatch.setattr(
        mod.shutil, "which", lambda name: "/usr/bin/nvidia-smi" if name == "nvidia-smi" else None
    )
    monkeypatch.setattr(mod, "_run", lambda cmd: None)

    accelerator, failure = mod._accelerator()
    assert accelerator is None
    assert failure and "nvidia-smi" in failure


def test_unified_memory_is_not_reported_twice(monkeypatch):
    """On Apple Silicon the GPU pool is system RAM. Copying `ram_total_mb` into
    `memory_mb` would be the same number under two names, free to drift the day
    one of them is computed differently."""
    monkeypatch.setattr(mod.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(mod.platform, "machine", lambda: "arm64")

    accelerator, _ = mod._accelerator()
    assert accelerator is not None
    assert accelerator.unified_memory is True
    assert accelerator.memory_mb is None


def test_an_intel_mac_is_metal_without_unified_memory(monkeypatch):
    """The iMac in the fleet. Metal exists, the memory does not come from the
    same pool, and neither fact is a verdict about what it can serve."""
    monkeypatch.setattr(mod.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(mod.platform, "machine", lambda: "x86_64")

    accelerator, _ = mod._accelerator()
    assert accelerator is not None and accelerator.kind == "metal"
    assert accelerator.unified_memory is False


def test_a_broken_probe_is_named_and_the_rest_of_the_profile_survives(monkeypatch):
    """A heartbeat cannot raise. A profile missing one field and saying which is
    worth more than no profile at all."""

    def boom():
        raise OSError("no /proc here")

    monkeypatch.setattr(mod.psutil, "virtual_memory", boom)
    p = mod.device_profile()

    assert p.ram_total_mb is None
    assert any(e.startswith("ram:") for e in p.probe_errors)
    assert p.os and p.arch, "one failed probe emptied the whole profile"


def test_the_disk_measured_is_the_one_weights_land_on(tmp_path):
    """Not `/`: the same mount on most machines, and the whole question on one
    where the model directory is an external drive."""
    p = mod.device_profile(models_path=tmp_path)
    assert p.disk_path == str(tmp_path)
    assert p.disk_total_mb and p.disk_total_mb > 0
