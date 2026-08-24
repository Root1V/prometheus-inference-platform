"""Tests for Config: AC-19 and memory/specs/011 AC-25 (CA bundle)."""

from __future__ import annotations

from pathlib import Path

import pytest

from prometheus_manager_core.config import DownloadsConfig, ManagerConfig, ServerConfig, load_config


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
    """RM-08: per-backend launch command config — see memory/wiki/inference-engines.md."""

    def test_default_binaries(self):
        cfg = load_config(path=None)
        assert cfg.resolved_backend_binary("llama_cpp") == cfg.server.binary
        assert cfg.resolved_backend_binary("mlx") == "mlx_lm.server"
        assert cfg.resolved_backend_binary("vllm") == "vllm"
        assert cfg.resolved_backend_binary("sglang") == "python3"

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
