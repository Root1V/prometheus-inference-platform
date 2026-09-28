"""Manager configuration loader.

Implements: memory/specs/008-llama-server-manager.md — AC-19 (llama-server host enforcement)
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Repo root — this file is <root>/runtime/manager/core/src/prometheus_manager_core/config.py.
# Used to anchor relative paths so they don't depend on the process's cwd.
_REPO_ROOT = Path(__file__).resolve().parents[5]

_DEFAULT_CONFIG = """
[api]
host = "0.0.0.0"
port = 8090
jwks_url = "http://127.0.0.1:9000/.well-known/jwks.json"
# Leave empty for bare-metal; set to host.containers.internal inside Podman.
proxy_host = ""

[server]
binary = "~/.local/bin/llama-server"
host = "127.0.0.1"
stop_timeout_s = 10
start_timeout_s = 60
log_dir = "runtime/logs"
pid_dir = "runtime/run"

# Per-backend launch command overrides. See docs/roadmap.md RM-06.
# llama_cpp uses [server].binary above, unchanged from before this section existed.
[backends.mlx]
binary = "mlx_lm.server"
start_timeout_s = 120

[backends.vllm]
binary = "vllm"
start_timeout_s = 300

[backends.sglang]
binary = "python3"
start_timeout_s = 300

# PRM-135: hf-serve — Hugging Face format weights via Transformers/Diffusers/
# Sentence Transformers. Heavy Python start (Hub resolve + torch load), so the
# timeout matches mlx's rather than llama.cpp's.
[backends.hf_serve]
binary = "hf-serve"
start_timeout_s = 300

# PRM-140: laya-serve — a System One decision model. Takes no flags at all;
# everything is environment (see lifecycle._laya_env). Install as `laya[serve]`,
# never plain `laya`: the bare package ships the entry point without fastapi or
# uvicorn. Preloads its checkpoints at startup like the other Python servers.
[backends.laya]
binary = "laya-serve"
start_timeout_s = 300

# RM-38: stable-diffusion.cpp's sd-server — image generation, not LLM
# completions. Model load + Metal shader compile is quick (a few seconds for
# a ~2GB model in local testing); 60s matches llama_cpp's own default.
[backends.sd_cpp]
binary = "sd-server"
start_timeout_s = 60

[registry]
path = "runtime/manager/registry.db"

# PRM-134: the list *of* nodes. Exactly one manager-api in the fleet sets
# coordinator = true; it opens fleet.db and serves /v1/fleet/nodes, and every
# other node leaves this false and behaves as before. Separate from registry.db
# above, which is per-node.
[fleet]
coordinator = false
path = "runtime/manager/fleet.db"

[downloads]
dir = "runtime/models"
hf_token_env = "HF_TOKEN"

[dashboard]
refresh_interval_s = 2

[tui]
theme = "catppuccin-latte"
log_file_path = "runtime/logs/manager.log"

[tracing]
# OTLP/HTTP endpoint for distributed traces. RM-91: this project no longer
# runs a collector of its own — point it at the one Argus exposes.
# Leave empty to fall back to the OTEL_EXPORTER_OTLP_ENDPOINT env var.
otlp_endpoint = "http://localhost:4318"
# Set to true to disable tracing without removing the endpoint.
disabled = false
"""


@dataclass
class ApiConfig:
    host: str = "0.0.0.0"
    port: int = 8090
    # /v1/jwks does not exist — the auth service serves /.well-known/jwks.json.
    # A manager started without a manager.toml fell back to this and rejected
    # every token, with no hint that the URL was the problem.
    jwks_url: str = "http://127.0.0.1:9000/.well-known/jwks.json"
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
class BackendConfig:
    """Launch command override for one non-llama_cpp backend.

    llama_cpp keeps using [server].binary — it predates this section and
    changing it would be an unrelated behavior change.
    """

    binary: str = ""
    start_timeout_s: int = 120


@dataclass
class BackendsConfig:
    mlx: BackendConfig = field(
        default_factory=lambda: BackendConfig(binary="mlx_lm.server", start_timeout_s=120)
    )
    vllm: BackendConfig = field(
        default_factory=lambda: BackendConfig(binary="vllm", start_timeout_s=300)
    )
    sglang: BackendConfig = field(
        default_factory=lambda: BackendConfig(binary="python3", start_timeout_s=300)
    )
    sd_cpp: BackendConfig = field(
        default_factory=lambda: BackendConfig(binary="sd-server", start_timeout_s=60)
    )
    hf_serve: BackendConfig = field(
        default_factory=lambda: BackendConfig(binary="hf-serve", start_timeout_s=300)
    )
    laya: BackendConfig = field(
        default_factory=lambda: BackendConfig(binary="laya-serve", start_timeout_s=300)
    )


@dataclass
class RegistryConfig:
    path: str = "runtime/manager/registry.db"


@dataclass
class FleetConfig:
    """Who owns the list *of* nodes — PRM-134.

    `manager-api` runs on every node, so "the manager owns the fleet" needs an
    answer to *which* manager. Exactly one is designated the coordinator here;
    it opens `path` and serves the fleet endpoints, and every other node runs
    with `coordinator = false` and behaves as it always did.

    That is Nomad's server/client split and Kubernetes' control-plane/node
    split: identical software, one configured role. A flag rather than an
    election because two nodes and a laptop do not need consensus, and a leader
    nobody chose is harder to reason about than one written in a file.

    `path` is a database of its own, never `registry.path`: that one is
    per-node — the models and instances on *this* host — and the node list is
    fleet-level with exactly one of it.
    """

    coordinator: bool = False
    path: str = "runtime/manager/fleet.db"

    # ── PRM-152: reporting in ────────────────────────────────────────────────
    # Where this node sends its heartbeat. The coordinator's own base URL, which
    # on the coordinator itself is left empty: it stamps its own row in process
    # rather than making an HTTP call to itself for a credential it would have
    # to be issued to talk to itself.
    coordinator_url: str = ""
    # Where this node mints its heartbeat token. Named explicitly rather than
    # derived from `api.jwks_url`: the two are the same service today and a
    # derived value that is right by coincidence is the failure mode this
    # codebase keeps meeting.
    auth_token_url: str = ""
    # Kubernetes' kubelet renews its Lease every 10s against a 40s duration. Ten
    # seconds against this fleet's 60s TTL (`fleet.DEFAULT_LIVENESS_TTL_S`) has
    # the same shape: a node misses several reports before it stops being
    # routable, so one slow moment is not an outage.
    heartbeat_interval_s: float = 10.0
    # PRM-151's coordinator-side probe. On until the fleet is confirmed reporting
    # in, then off — and off is the end state, not an option: while a probe also
    # stamps `last_seen_at`, "the node is down" and "I could not reach it" stay
    # indistinguishable, which is the ambiguity the heartbeat exists to end.
    sweep: bool = True

    @property
    def node_id(self) -> str:
        """This node's id in the fleet — `PMGR_FLEET_NODE_ID`.

        The coordinator assigns a UUID at registration, so a node cannot derive
        its own id and has to be told it. Read from the environment with the two
        credential parts below rather than written in this file, because the three
        travel together: "who I am" and "how I prove it" moved separately is how an
        operator ends up with a node reporting under an identity it no longer
        holds. It also keeps every per-node identity out of a tracked TOML.
        """
        return os.environ.get("PMGR_FLEET_NODE_ID", "")

    @property
    def client_id(self) -> str:
        """`PMGR_FLEET_CLIENT_ID` — one OAuth2 client per node (PRM-152)."""
        return os.environ.get("PMGR_FLEET_CLIENT_ID", "")

    @property
    def client_secret(self) -> str:
        """`PMGR_FLEET_CLIENT_SECRET`. Never in a file this repository tracks."""
        return os.environ.get("PMGR_FLEET_CLIENT_SECRET", "")

    @property
    def can_report_in(self) -> bool:
        """Is everything a heartbeat needs present? — PRM-152.

        A node missing any of it keeps serving inference and says so at startup,
        rather than refusing to start over a credential that is not on the
        inference path. PRM-147's lesson is that the *reason* has to be on the
        record — a missing environment variable that produces silence is exactly
        how two hours went into the wrong place.
        """
        return bool(self.node_id and self.client_id and self.client_secret)


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
    # OTLP/HTTP endpoint for distributed traces — a collector run elsewhere
    # (Argus) since RM-91. Falls back to OTEL_EXPORTER_OTLP_ENDPOINT if empty.
    otlp_endpoint: str = "http://localhost:4318"
    # Set to true to disable tracing without removing the endpoint (e.g. in CI).
    disabled: bool = False


@dataclass
class ManagerConfig:
    api: ApiConfig = field(default_factory=ApiConfig)
    server: ServerConfig = field(default_factory=ServerConfig)
    backends: BackendsConfig = field(default_factory=BackendsConfig)
    registry: RegistryConfig = field(default_factory=RegistryConfig)
    fleet: FleetConfig = field(default_factory=FleetConfig)
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
        """Same cwd-independence as resolved_registry_path (RM-75, PRM-165)."""
        path = Path(self.server.log_dir).expanduser()
        return path if path.is_absolute() else _REPO_ROOT / path

    @property
    def resolved_pid_dir(self) -> Path:
        """Same cwd-independence as resolved_registry_path (RM-75, PRM-165).

        A pid_dir that moves with the cwd is how a manager loses track of
        instances it started: it writes the pidfile under one directory and
        looks for it under another.
        """
        path = Path(self.server.pid_dir).expanduser()
        return path if path.is_absolute() else _REPO_ROOT / path

    @property
    def resolved_registry_path(self) -> Path:
        """Absolute path to registry.db, independent of the process's cwd.

        A bare Path("runtime/manager/registry.db") is resolved against
        whatever directory the manager happened to be started from, so
        running any registry command from runtime/manager/ silently created
        and wrote to a nested runtime/manager/runtime/manager/registry.db
        instead of the real one. Absolute paths — what the container sets via
        PMGR_REGISTRY_PATH — are used unchanged.
        """
        path = Path(self.registry.path).expanduser()
        return path if path.is_absolute() else _REPO_ROOT / path

    @property
    def resolved_fleet_path(self) -> Path:
        """Absolute path to fleet.db — PRM-134, same cwd-independence as above."""
        path = Path(self.fleet.path).expanduser()
        return path if path.is_absolute() else _REPO_ROOT / path

    @property
    def resolved_ca_bundle(self) -> Path | None:
        """Return the CA bundle path if configured, else None (use system trust store).

        See memory/specs/011-downloads-view-redesign.md — AC-25
        """
        if self.downloads.ca_bundle:
            path = Path(self.downloads.ca_bundle).expanduser()
            return path if path.is_absolute() else _REPO_ROOT / path
        return None

    @property
    def resolved_downloads_dir(self) -> Path:
        """Same cwd-independence as resolved_registry_path (RM-75, PRM-165).

        Downloaded weights are large; resolving this against the cwd means a
        model already on disk is re-fetched into a second tree rather than
        found.
        """
        path = Path(self.downloads.dir).expanduser()
        return path if path.is_absolute() else _REPO_ROOT / path

    @property
    def hf_token(self) -> str | None:
        return os.environ.get(self.downloads.hf_token_env)

    def _backend_config(self, backend: str) -> BackendConfig:
        by_name: dict[str, BackendConfig] = {
            "mlx": self.backends.mlx,
            "vllm": self.backends.vllm,
            "sglang": self.backends.sglang,
            "sd_cpp": self.backends.sd_cpp,
            "hf_serve": self.backends.hf_serve,
            "laya": self.backends.laya,
        }
        cfg = by_name.get(backend)
        if cfg is None:
            raise ValueError(f"No [backends.{backend}] config found")
        return cfg

    def resolved_backend_binary(self, backend: str) -> str:
        """Return the launch binary/command for *backend* ("llama_cpp", "mlx", ...).

        llama_cpp keeps using [server].binary; other backends come from
        [backends.<name>].binary. subprocess.Popen doesn't expand a leading
        "~" (that's shell syntax, not something exec() understands), so a
        default config's `binary = "~/.local/bin/llama-server"` — the
        documented install path — needs it expanded here or start_instance
        fails with FileNotFoundError.
        """
        if backend == "llama_cpp":
            return os.path.expanduser(self.server.binary)
        return os.path.expanduser(self._backend_config(backend).binary)

    def resolved_backend_start_timeout_s(self, backend: str) -> int:
        if backend == "llama_cpp":
            return self.server.start_timeout_s
        return self._backend_config(backend).start_timeout_s


def load_config(path: Path | None = None) -> ManagerConfig:
    """Load manager.toml; fall back to defaults if the file is absent."""
    raw: dict[str, Any] = tomllib.loads(_DEFAULT_CONFIG)
    if path is not None and path.exists():
        with open(path, "rb") as fh:
            override = tomllib.load(fh)
        _deep_merge(raw, override)

    backends_raw = raw.get("backends", {})
    cfg = ManagerConfig(
        api=ApiConfig(**raw.get("api", {})),
        server=ServerConfig(**raw.get("server", {})),
        backends=BackendsConfig(
            mlx=BackendConfig(**backends_raw.get("mlx", {})),
            vllm=BackendConfig(**backends_raw.get("vllm", {})),
            sglang=BackendConfig(**backends_raw.get("sglang", {})),
            sd_cpp=BackendConfig(**backends_raw.get("sd_cpp", {})),
            hf_serve=BackendConfig(**backends_raw.get("hf_serve", {})),
            laya=BackendConfig(**backends_raw.get("laya", {})),
        ),
        registry=RegistryConfig(**raw.get("registry", {})),
        fleet=FleetConfig(**raw.get("fleet", {})),  # PRM-134
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
    # PRM-134: the same treatment for the fleet, so a containerised coordinator
    # can be pointed at a mounted volume without editing the TOML inside it.
    if val := os.environ.get("PMGR_FLEET_PATH"):
        cfg.fleet.path = val
    if (val := os.environ.get("PMGR_FLEET_COORDINATOR", "").lower()) in ("true", "1", "yes"):
        cfg.fleet.coordinator = True
    elif val in ("false", "0", "no"):
        cfg.fleet.coordinator = False
    # PRM-152: the credential itself is read from the environment on every access
    # (see FleetConfig), so only the operational half is overridable here.
    if val := os.environ.get("PMGR_FLEET_COORDINATOR_URL"):
        cfg.fleet.coordinator_url = val
    if val := os.environ.get("PMGR_FLEET_AUTH_TOKEN_URL"):
        cfg.fleet.auth_token_url = val
    if (val := os.environ.get("PMGR_FLEET_SWEEP", "").lower()) in ("true", "1", "yes"):
        cfg.fleet.sweep = True
    elif val in ("false", "0", "no"):
        cfg.fleet.sweep = False
    cfg.validate()
    return cfg


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> None:
    for key, val in override.items():
        if isinstance(val, dict) and key in base and isinstance(base[key], dict):
            _deep_merge(base[key], val)
        else:
            base[key] = val
