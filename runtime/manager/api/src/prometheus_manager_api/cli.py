"""Prometheus Manager API CLI — pmgr-api.

Thin entrypoint that loads config and starts the REST API with uvicorn.
Split out of the pmgr CLI (prometheus-manager-tui) so this package — the one
built into the container image — doesn't need click/rich for anything except
this one command, and pmgr (bare-metal) doesn't need fastapi/uvicorn at all.

Implements: memory/specs/008-llama-server-manager.md — AC-11
See: memory/roadmap.md — RM-05
"""

from __future__ import annotations

import sys
from pathlib import Path

import click
from prometheus_manager_core.config import ManagerConfig, load_config


def _load(config_path: str | None) -> ManagerConfig:
    path = Path(config_path) if config_path else None
    try:
        return load_config(path)
    except ValueError as exc:
        click.echo(f"[error] Configuration error: {exc}", err=True)
        sys.exit(1)


@click.command()
@click.option(
    "--config",
    "-c",
    "config_path",
    envvar="PMGR_CONFIG",
    default=None,
    metavar="FILE",
    help="Path to manager.toml (default: manager.toml in cwd or defaults).",
)
@click.option("--host", default=None, help="Override API bind host.")
@click.option("--port", default=None, type=int, help="Override API port.")
def cli(config_path: str | None, host: str | None, port: int | None) -> None:
    """Start the Manager REST API.

    Implements: memory/specs/008-llama-server-manager.md — AC-11
    """
    import uvicorn
    from prometheus_manager_core.registry import Registry

    from .app import app

    cfg = _load(config_path)
    reg = Registry(cfg.resolved_registry_path)

    bind_host = host or cfg.api.host
    bind_port = port or cfg.api.port

    app.state.registry = reg
    app.state.pid_dir = cfg.resolved_pid_dir
    app.state.jwks_url = cfg.api.jwks_url
    app.state.jwks_tls_verify = cfg.api.jwks_tls_verify
    app.state.proxy_host = (
        cfg.api.proxy_host
    )  # "" = bare-metal; "host.containers.internal" = container

    click.echo(f"Starting Manager API on {bind_host}:{bind_port} …")
    uvicorn.run(app, host=bind_host, port=bind_port, log_level="info")
