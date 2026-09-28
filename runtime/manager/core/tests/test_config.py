"""Tests for Config: AC-19 and memory/specs/011 AC-25 (CA bundle)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from prometheus_manager_core.config import (
    DownloadsConfig,
    ManagerConfig,
    RegistryConfig,
    ServerConfig,
    load_config,
)


class TestJwksUrlRM84:
    """RM-84: the manager rejected every token when its JWKS URL was wrong, and
    said "invalid or expired token" while doing it — which sends you looking at
    credentials instead of at the URL. Two defaults were wrong."""

    def test_the_default_jwks_url_points_at_a_path_the_auth_service_serves(self):
        """The ApiConfig default was /v1/jwks, which is a 404. A manager started
        without a manager.toml could therefore never authenticate anyone."""
        from prometheus_manager_core.config import ApiConfig

        assert ApiConfig().jwks_url.endswith("/.well-known/jwks.json")

    def test_the_embedded_defaults_agree_with_the_dataclass_default(self):
        """The TOML defaults and the dataclass default had drifted apart — one
        had the right path, the other did not — so which one applied depended on
        whether a config file happened to be found."""
        from prometheus_manager_core.config import ApiConfig

        assert load_config(path=None).api.jwks_url == ApiConfig().jwks_url

    def test_the_default_jwks_host_is_not_localhost(self):
        """localhost also resolves to ::1, and anything else bound to
        0.0.0.0:9000 on the machine answers there first — which is exactly how
        this broke, with another project's object store replying instead of the
        auth service."""
        cfg = load_config(path=None)
        assert "localhost" not in cfg.api.jwks_url
        assert "127.0.0.1" in cfg.api.jwks_url


class TestConfigAC19:
    """AC-19: llama-server must always bind to 127.0.0.1."""

    def test_AC19_default_config_passes_validation(self, default_config):
        """AC-19: default config with host=127.0.0.1 validates successfully."""
        default_config.validate()  # no exception

    def test_AC19_external_host_raises_value_error(self):
        """AC-19: host other than 127.0.0.1 raises ValueError."""
        cfg = ManagerConfig(server=ServerConfig(host="0.0.0.0"))
        with pytest.raises(ValueError, match="127.0.0.1"):
            cfg.validate()

    def test_AC19_load_config_falls_back_to_defaults(self):
        """AC-19: load_config with no file uses embedded defaults (127.0.0.1)."""
        cfg = load_config(path=None)
        assert cfg.server.host == "127.0.0.1"

    def test_AC19_load_config_with_toml_override(self, tmp_path: Path):
        """AC-19: valid toml override is merged into config."""
        toml_file = tmp_path / "manager.toml"
        toml_file.write_text("[api]\nport = 9999\n")
        cfg = load_config(path=toml_file)
        assert cfg.api.port == 9999
        assert cfg.server.host == "127.0.0.1"  # default preserved

    def test_AC19_toml_override_with_bad_host_rejected(self, tmp_path: Path):
        """AC-19: overriding host to 0.0.0.0 via toml raises ValueError."""
        toml_file = tmp_path / "manager.toml"
        toml_file.write_text('[server]\nhost = "0.0.0.0"\n')
        with pytest.raises(ValueError, match="127.0.0.1"):
            load_config(path=toml_file)


class TestBackendsConfig:
    """RM-08: per-backend launch command config — see docs/roadmap.md RM-06."""

    def test_default_binaries(self):
        cfg = load_config(path=None)
        assert cfg.resolved_backend_binary("llama_cpp") == os.path.expanduser(cfg.server.binary)
        assert cfg.resolved_backend_binary("mlx") == "mlx_lm.server"
        assert cfg.resolved_backend_binary("vllm") == "vllm"
        assert cfg.resolved_backend_binary("sglang") == "python3"

    def test_llama_cpp_binary_tilde_expanded(self):
        """subprocess.Popen doesn't expand '~' itself — start_instance would get
        FileNotFoundError against the documented default install path otherwise."""
        cfg = ManagerConfig(server=ServerConfig(binary="~/.local/bin/llama-server"))
        resolved = cfg.resolved_backend_binary("llama_cpp")
        assert "~" not in resolved
        assert resolved == os.path.expanduser("~/.local/bin/llama-server")

    def test_backend_binary_tilde_expanded(self, tmp_path: Path):
        toml_file = tmp_path / "manager.toml"
        toml_file.write_text('[backends.mlx]\nbinary = "~/venvs/mlx/bin/mlx_lm.server"\n')
        cfg = load_config(path=toml_file)
        resolved = cfg.resolved_backend_binary("mlx")
        assert "~" not in resolved
        assert resolved == os.path.expanduser("~/venvs/mlx/bin/mlx_lm.server")

    def test_unknown_backend_raises(self):
        cfg = load_config(path=None)
        with pytest.raises(ValueError, match="backends"):
            cfg.resolved_backend_binary("does-not-exist")

    def test_backend_start_timeouts_longer_than_llama_cpp(self):
        """vLLM/SGLang have heavier startup (model compile/warm-up) — RM-06."""
        cfg = load_config(path=None)
        assert cfg.resolved_backend_start_timeout_s("vllm") > cfg.server.start_timeout_s
        assert cfg.resolved_backend_start_timeout_s("sglang") > cfg.server.start_timeout_s

    def test_toml_override_for_mlx_binary(self, tmp_path: Path):
        toml_file = tmp_path / "manager.toml"
        toml_file.write_text('[backends.mlx]\nbinary = "/opt/venv/bin/mlx_lm.server"\n')
        cfg = load_config(path=toml_file)
        assert cfg.resolved_backend_binary("mlx") == "/opt/venv/bin/mlx_lm.server"
        # Unrelated backends keep their defaults.
        assert cfg.resolved_backend_binary("vllm") == "vllm"


class TestCABundleConfig:
    """memory/specs/011 — AC-25: resolved_ca_bundle property."""

    def test_AC25_empty_ca_bundle_returns_none(self):
        """AC-25: ca_bundle='' → resolved_ca_bundle is None."""
        cfg = ManagerConfig(downloads=DownloadsConfig(ca_bundle=""))
        assert cfg.resolved_ca_bundle is None

    def test_AC25_set_ca_bundle_returns_path(self, tmp_path: Path):
        """AC-25: ca_bundle set → resolved_ca_bundle is Path."""
        p = str(tmp_path / "bundle.pem")
        cfg = ManagerConfig(downloads=DownloadsConfig(ca_bundle=p))
        assert cfg.resolved_ca_bundle == Path(p)

    def test_AC25_default_downloads_config_has_empty_ca_bundle(self):
        """AC-25: DownloadsConfig default has ca_bundle='' and resolved is None."""
        cfg = ManagerConfig()
        assert cfg.downloads.ca_bundle == ""
        assert cfg.resolved_ca_bundle is None

    def test_AC25_toml_ca_bundle_loaded(self, tmp_path: Path):
        """AC-25: ca_bundle from manager.toml is loaded into config."""
        toml_file = tmp_path / "manager.toml"
        toml_file.write_text('[downloads]\nca_bundle = "/etc/pki/tls-ca-bundle.pem"\n')
        cfg = load_config(path=toml_file)
        assert cfg.downloads.ca_bundle == "/etc/pki/tls-ca-bundle.pem"
        assert cfg.resolved_ca_bundle == Path("/etc/pki/tls-ca-bundle.pem")


class TestRegistryPathResolution:
    """The registry path must not depend on the manager's cwd."""

    def test_default_registry_path_is_absolute(self):
        """A relative default resolves to an absolute path, not a cwd-relative one."""
        assert ManagerConfig().resolved_registry_path.is_absolute()

    def test_default_registry_path_is_the_repo_registry(self):
        """The default anchors to <repo root>/runtime/manager/registry.db."""
        repo_root = Path(__file__).resolve().parents[4]
        expected = repo_root / "runtime" / "manager" / "registry.db"
        assert ManagerConfig().resolved_registry_path == expected

    def test_registry_path_is_identical_from_any_cwd(self, tmp_path, monkeypatch):
        """Running from runtime/manager/ must not produce a nested registry.db."""
        cfg = ManagerConfig()
        from_repo_root = cfg.resolved_registry_path

        monkeypatch.chdir(tmp_path)
        assert cfg.resolved_registry_path == from_repo_root

        nested = tmp_path / "runtime" / "manager"
        nested.mkdir(parents=True)
        monkeypatch.chdir(nested)
        assert cfg.resolved_registry_path == from_repo_root

    def test_absolute_registry_path_is_left_alone(self, tmp_path: Path):
        """An absolute path — what PMGR_REGISTRY_PATH sets — is used unchanged."""
        abs_path = tmp_path / "elsewhere" / "registry.db"
        cfg = ManagerConfig(registry=RegistryConfig(path=str(abs_path)))
        assert cfg.resolved_registry_path == abs_path

    def test_env_override_with_absolute_path_is_left_alone(self, monkeypatch):
        """PMGR_REGISTRY_PATH=/data/registry.db is honoured verbatim (container path)."""
        monkeypatch.setenv("PMGR_REGISTRY_PATH", "/data/registry.db")
        assert load_config(path=None).resolved_registry_path == Path("/data/registry.db")


class TestSiblingPathResolution:
    """PRM-165: the other manager paths must not depend on the cwd either."""

    # (attribute, relative default) — resolved_binary is deliberately absent:
    # see test_backend_binaries_are_left_bare_for_path_lookup below.
    CASES = [
        ("resolved_log_dir", "runtime/logs"),
        ("resolved_pid_dir", "runtime/run"),
        ("resolved_downloads_dir", "runtime/models"),
    ]

    @pytest.mark.parametrize("attr,relative", CASES)
    def test_default_anchors_to_the_repo_root(self, attr: str, relative: str):
        repo_root = Path(__file__).resolve().parents[4]
        resolved = getattr(ManagerConfig(), attr)
        assert resolved.is_absolute()
        assert resolved == repo_root / relative

    @pytest.mark.parametrize("attr,relative", CASES)
    def test_identical_from_any_cwd(self, attr: str, relative: str, tmp_path, monkeypatch):
        """Including from runtime/manager/ — the cwd that caused RM-75."""
        cfg = ManagerConfig()
        from_repo_root = getattr(cfg, attr)

        nested = tmp_path / "runtime" / "manager"
        nested.mkdir(parents=True)
        for cwd in (tmp_path, nested):
            monkeypatch.chdir(cwd)
            assert getattr(cfg, attr) == from_repo_root

    def test_absolute_log_and_pid_dirs_are_left_alone(self, tmp_path: Path):
        cfg = ManagerConfig(
            server=ServerConfig(
                host="127.0.0.1",
                log_dir=str(tmp_path / "logs"),
                pid_dir=str(tmp_path / "run"),
            )
        )
        assert cfg.resolved_log_dir == tmp_path / "logs"
        assert cfg.resolved_pid_dir == tmp_path / "run"

    def test_absolute_downloads_dir_is_left_alone(self, tmp_path: Path):
        cfg = ManagerConfig(downloads=DownloadsConfig(dir=str(tmp_path / "weights")))
        assert cfg.resolved_downloads_dir == tmp_path / "weights"

    def test_relative_ca_bundle_anchors_to_the_repo_root(self):
        repo_root = Path(__file__).resolve().parents[4]
        cfg = ManagerConfig(downloads=DownloadsConfig(ca_bundle="certs/corp.pem"))
        assert cfg.resolved_ca_bundle == repo_root / "certs" / "corp.pem"

    def test_backend_binaries_are_left_bare_for_path_lookup(self):
        """Anchoring these would break Popen: they are PATH lookups, not paths.

        `vllm`, `python3`, `mlx_lm.server` and friends are resolved by exec
        against PATH — turning them into <repo root>/vllm would make every
        non-llama_cpp backend fail to start.
        """
        cfg = ManagerConfig()
        for backend in ("mlx", "vllm", "sglang", "sd_cpp"):
            binary = cfg.resolved_backend_binary(backend)
            assert not binary.startswith("/"), f"{backend} binary became a path: {binary}"


# ── PRM-135: adding a backend is four coordinated edits, and nothing checked ──


def test_every_backend_resolves_a_binary_and_a_timeout():
    """`BACKENDS` is the list; `_backend_config` is a second, hand-kept copy.

    Adding `hf_serve` meant editing four places — the tuple, the default TOML,
    `BackendsConfig`, `load_config` — and `_backend_config`'s dict a fifth.
    Missing the last three imported, typechecked and passed every test; it
    failed at `start_instance`, on a real request, with
    `No [backends.hf_serve] config found`.

    This is the assertion that would have caught it before the launch did.
    """
    from prometheus_manager_core.config import ManagerConfig
    from prometheus_manager_core.registry import BACKENDS

    cfg = ManagerConfig()
    missing: list[str] = []
    for backend in BACKENDS:
        try:
            binary = cfg.resolved_backend_binary(backend)
            timeout = cfg.resolved_backend_start_timeout_s(backend)
        except ValueError as exc:
            missing.append(f"{backend}: {exc}")
            continue
        if not binary:
            missing.append(f"{backend}: empty binary")
        if timeout <= 0:
            missing.append(f"{backend}: non-positive start timeout")
    assert not missing, f"backends in BACKENDS that cannot be launched: {missing}"


def test_every_backend_has_a_command_builder():
    """The other half of the same trap: a backend the registry accepts and
    `lifecycle` has no way to launch. `start_instance` raises for it, but only
    once someone tries.
    """
    from prometheus_manager_core.lifecycle import _COMMAND_BUILDERS
    from prometheus_manager_core.registry import BACKENDS

    assert set(_COMMAND_BUILDERS) == set(BACKENDS)


def test_every_backend_is_recognisable_by_the_scanner():
    """And the third: a process the manager started and cannot find again.

    RM-86: the scanner is how a restarted manager re-adopts running instances.
    A backend with no signature leaves its processes orphaned — running, billed
    for, and invisible.
    """
    from prometheus_manager_core.registry import BACKENDS
    from prometheus_manager_core.scanner import _BACKEND_SIGNATURES

    assert {sig.backend for sig in _BACKEND_SIGNATURES} == set(BACKENDS)


# ── PRM-152: a node's credential ─────────────────────────────────────────────


def test_a_nodes_identity_is_read_from_the_environment(monkeypatch):
    from prometheus_manager_core.config import FleetConfig

    monkeypatch.setenv("PMGR_FLEET_NODE_ID", "11111111-1111-4111-8111-111111111111")
    monkeypatch.setenv("PMGR_FLEET_CLIENT_ID", "client-local")
    monkeypatch.setenv("PMGR_FLEET_CLIENT_SECRET", "s3cret")

    cfg = FleetConfig()
    assert cfg.node_id == "11111111-1111-4111-8111-111111111111"
    assert cfg.client_id == "client-local"
    assert cfg.client_secret == "s3cret"
    assert cfg.can_report_in


def test_a_node_credential_cannot_be_written_into_a_tracked_config():
    """`manager.toml` and `manager-lab.toml` are tracked in this repository, which
    is public. The three identity facts are properties over the environment rather
    than fields, so `client_secret = "..."` in the TOML is not quietly ignored —
    `load_config` passes the section through as keyword arguments, so it fails at
    startup, which is the only safe direction for that mistake to fail in."""
    import dataclasses

    from prometheus_manager_core.config import FleetConfig

    fields = {f.name for f in dataclasses.fields(FleetConfig)}
    assert fields.isdisjoint({"node_id", "client_id", "client_secret"})
    with pytest.raises(TypeError):
        FleetConfig(client_secret="would-be-committed")  # type: ignore[call-arg]


def test_the_fleet_environment_overrides_apply(tmp_path, monkeypatch):
    from prometheus_manager_core.config import load_config

    path = tmp_path / "manager.toml"
    path.write_text('[fleet]\ncoordinator = true\ncoordinator_url = "http://from-file:8090"\n')

    monkeypatch.setenv("PMGR_FLEET_COORDINATOR_URL", "http://from-env:8090")
    monkeypatch.setenv("PMGR_FLEET_AUTH_TOKEN_URL", "http://auth:9000/oauth2/token")
    monkeypatch.setenv("PMGR_FLEET_SWEEP", "false")

    cfg = load_config(path)
    assert cfg.fleet.coordinator_url == "http://from-env:8090"
    assert cfg.fleet.auth_token_url == "http://auth:9000/oauth2/token"
    assert cfg.fleet.sweep is False


def test_the_sweep_is_on_until_an_operator_turns_it_off():
    """PRM-151's probe stays the fallback by default: a fleet upgraded to this
    code must not lose its liveness because no node reports in yet."""
    from prometheus_manager_core.config import FleetConfig

    assert FleetConfig().sweep is True
