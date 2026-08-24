"""Manager configuration loader.

Implements: memory/specs/008-llama-server-manager.md — AC-19 (llama-server host enforcement)
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_DEFAULT_CONFIG = """
[api]
host = "0.0.0.0"
port = 8090
jwks_url = "http://localhost:9000/.well-known/jwks.json"
# Leave empty for bare-metal; set to host.containers.internal inside Podman.
proxy_host = ""

[server]
binary = "~/.local/bin/llama-server"
host = "127.0.0.1"
stop_timeout_s = 10
start_timeout_s = 60
log_dir = "runtime/logs"
pid_dir = "runtime/run"

[registry]
path = "runtime/manager/registry.yaml"

[downloads]
dir = "runtime/models"
hf_token_env = "HF_TOKEN"

[dashboard]
refresh_interval_s = 2

[tui]
theme = "catppuccin-latte"
log_file_path = "runtime/logs/manager.log"

[tracing]
# OTLP/HTTP endpoint for distributed traces.
# When running bare-metal, Tempo is reachable via Podman port-forward on localhost.
# Leave empty to fall back to OTEL_EXPORTER_OTLP_ENDPOINT env var or http://tempo:4318.
otlp_endpoint = "http://localhost:4318"
# Set to true to disable tracing without removing the endpoint.
disabled = false
"""


@dataclass
class ApiConfig:
    host: str = "0.0.0.0"
    port: int = 8090
    jwks_url: str = "http://localhost:9000/v1/jwks"
    # Disable TLS verification for internal calls when using self-signed certs (dev/Podman).
    jwks_tls_verify: bool = True
    # When set, the API uses HTTP health probing instead of psutil scanning.
    # Required in container mode because the container cannot see host processes.
    # Set to "host.containers.internal" when running inside Podman/Docker.
    proxy_host: str = ""


@dataclass
class ServerConfig:
    binary: str = "~/.local/bin/llama-server"
    host: str = "127.0.0.1"
    stop_timeout_s: int = 10
    start_timeout_s: int = 60
    log_dir: str = "runtime/logs"
    pid_dir: str = "runtime/run"


@dataclass
class RegistryConfig:
    path: str = "runtime/manager/registry.yaml"


@dataclass
class DownloadsConfig:
    dir: str = "runtime/models"
    hf_token_env: str = "HF_TOKEN"
    ca_bundle: str = ""  # See memory/specs/011-downloads-view-redesign.md — AC-25


@dataclass
class DashboardConfig:
    refresh_interval_s: int = 2


@dataclass
class TuiConfig:
    # Any Textual built-in theme name or a custom registered theme (e.g. github-dark).
    # Valid built-ins: textual-dark, textual-light, nord, gruvbox, catppuccin-mocha,
    # catppuccin-latte, catppuccin-frappe, catppuccin-macchiato, dracula, tokyo-night,
    # monokai, flexoki, solarized-light, solarized-dark, rose-pine, rose-pine-moon,
    # rose-pine-dawn, atom-one-dark, atom-one-light, github-dark (Prometheus custom).
    theme: str = "catppuccin-latte"
    # Where to persist TUI log output.  Stdout is silenced while Textual owns the
    # terminal; logs are written here instead.  Relative paths are resolved from cwd.
    # Empty string disables file logging (logs are discarded during TUI session).
    log_file_path: str = "runtime/logs/manager.log"


@dataclass
class TracingConfig:
    # OTLP/HTTP endpoint for distributed traces.
    # When running bare-metal, Tempo is reachable via Podman port-forward on localhost.
    # Falls back to OTEL_EXPORTER_OTLP_ENDPOINT env var if empty.
    otlp_endpoint: str = "http://localhost:4318"
    # Set to true to disable tracing without removing the endpoint (e.g. in CI).
    disabled: bool = False


@dataclass
class ManagerConfig:
    api: ApiConfig = field(default_factory=ApiConfig)
    server: ServerConfig = field(default_factory=ServerConfig)
    registry: RegistryConfig = field(default_factory=RegistryConfig)
    downloads: DownloadsConfig = field(default_factory=DownloadsConfig)
    dashboard: DashboardConfig = field(default_factory=DashboardConfig)
    tui: TuiConfig = field(default_factory=TuiConfig)
    tracing: TracingConfig = field(default_factory=TracingConfig)

    def validate(self) -> None:
        """Enforce security constraints.

        Implements: memory/specs/008-llama-server-manager.md — AC-19
        """
        if self.server.host != "127.0.0.1":
            raise ValueError(
                "Host must be 127.0.0.1 — external binding is not permitted. "
                f"Got: {self.server.host!r}"
            )

    @property
    def resolved_binary(self) -> Path:
        return Path(self.server.binary).expanduser()

    @property
    def resolved_log_dir(self) -> Path:
        return Path(self.server.log_dir)

    @property
    def resolved_pid_dir(self) -> Path:
        return Path(self.server.pid_dir)

    @property
    def resolved_registry_path(self) -> Path:
        return Path(self.registry.path)

    @property
    def resolved_ca_bundle(self) -> Path | None:
        """Return the CA bundle path if configured, else None (use system trust store).

        See memory/specs/011-downloads-view-redesign.md — AC-25
        """
        if self.downloads.ca_bundle:
            return Path(self.downloads.ca_bundle)
        return None

    @property
    def resolved_downloads_dir(self) -> Path:
        return Path(self.downloads.dir)

    @property
    def hf_token(self) -> str | None:
        return os.environ.get(self.downloads.hf_token_env)


def load_config(path: Path | None = None) -> ManagerConfig:
    """Load manager.toml; fall back to defaults if the file is absent."""
    raw: dict[str, Any] = tomllib.loads(_DEFAULT_CONFIG)
    if path is not None and path.exists():
        with open(path, "rb") as fh:
            override = tomllib.load(fh)
        _deep_merge(raw, override)

    cfg = ManagerConfig(
        api=ApiConfig(**raw.get("api", {})),
        server=ServerConfig(**raw.get("server", {})),
        registry=RegistryConfig(**raw.get("registry", {})),
        downloads=DownloadsConfig(**raw.get("downloads", {})),
        dashboard=DashboardConfig(**raw.get("dashboard", {})),
        tui=TuiConfig(**raw.get("tui", {})),
        tracing=TracingConfig(**raw.get("tracing", {})),
    )
    # Environment variable overrides — used by the containerised manager service.
    if val := os.environ.get("PMGR_PROXY_HOST"):
        cfg.api.proxy_host = val
    if val := os.environ.get("PMGR_JWKS_URL"):
        cfg.api.jwks_url = val
    if os.environ.get("PMGR_JWKS_TLS_VERIFY", "").lower() in ("false", "0", "no"):
        cfg.api.jwks_tls_verify = False
    if val := os.environ.get("PMGR_REGISTRY_PATH"):
        cfg.registry.path = val
    cfg.validate()
    return cfg


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> None:
    for key, val in override.items():
        if isinstance(val, dict) and key in base and isinstance(base[key], dict):
            _deep_merge(base[key], val)
        else:
            base[key] = val
