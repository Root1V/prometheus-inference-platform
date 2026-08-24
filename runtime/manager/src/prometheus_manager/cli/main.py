"""Prometheus Manager CLI — pmgr.

Implements: memory/specs/008-llama-server-manager.md — AC-1…AC-26 (CLI surface)
Implements: memory/specs/018-observability-telemetry.md — AC-3
"""

from __future__ import annotations

import sys
from pathlib import Path

import click

from prometheus_manager.telemetry import configure_logging

# Configure structlog before any other manager imports (idempotent — AC-24)
configure_logging(service="manager")

# Inject OS native trust store (macOS Keychain, Windows CAPI, Linux system certs)
# so that requests/huggingface_hub work without a manual ca_bundle on corp machines
# that don't run Zscaler but still have a custom CA in the system keychain.
# See memory/specs/011-downloads-view-redesign.md — AC-22, AC-25
try:
    import truststore

    truststore.inject_into_ssl()
except ImportError:  # pragma: no cover
    pass  # truststore not installed — fall back to certifi
from rich.console import Console  # noqa: E402 — must follow the truststore/logging setup above
from rich.table import Table  # noqa: E402

console = Console()
err_console = Console(stderr=True, style="bold red")

# ── Config helper ─────────────────────────────────────────────────────────────


def _load(config_path: str | None):  # type: ignore[return]
    from prometheus_manager.config import load_config

    path = Path(config_path) if config_path else None
    try:
        return load_config(path)
    except ValueError as exc:
        err_console.print(f"[error] Configuration error: {exc}")
        sys.exit(1)


def _registry(config):
    from prometheus_manager.registry import Registry

    return Registry(config.resolved_registry_path)


# ── Root group ────────────────────────────────────────────────────────────────


@click.group()
@click.option(
    "--config",
    "-c",
    envvar="PMGR_CONFIG",
    default=None,
    metavar="FILE",
    help="Path to manager.toml (default: manager.toml in cwd or defaults).",
)
@click.pass_context
def cli(ctx: click.Context, config: str | None) -> None:
    """Prometheus Manager — manage llama-server instances."""
    ctx.ensure_object(dict)
    ctx.obj["config_path"] = config


# ── status ────────────────────────────────────────────────────────────────────


@cli.command("status")
@click.pass_context
def cmd_status(ctx: click.Context) -> None:
    """Show live status of all running llama-server instances.

    Implements: memory/specs/008-llama-server-manager.md — AC-1, AC-2
    """
    cfg = _load(ctx.obj["config_path"])
    from prometheus_manager.scanner import scan

    states = scan(cfg.resolved_pid_dir, proxy_host=cfg.api.proxy_host)

    if not states:
        console.print("[dim]No running llama-server instances found.[/dim]")
        return

    table = Table(title="Running llama-server instances")
    table.add_column("PID", style="cyan")
    table.add_column("Alias / ID", style="bold")
    table.add_column("Port")
    table.add_column("State")
    table.add_column("CPU %")
    table.add_column("RSS MB")
    table.add_column("Managed")

    for s in states:
        state_style = {
            "ready": "green",
            "loading": "yellow",
            "paused": "blue",
            "error": "red",
        }.get(s.state, "dim")
        table.add_row(
            str(s.pid),
            s.alias or s.model_id or "?",
            str(s.port),
            f"[{state_style}]{s.state}[/{state_style}]",
            f"{s.cpu_percent:.1f}",
            f"{s.rss_mb:.0f}",
            "✓" if s.managed else "orphan",
        )

    console.print(table)


# ── list ──────────────────────────────────────────────────────────────────────


@cli.command("list")
@click.pass_context
def cmd_list(ctx: click.Context) -> None:
    """List all models in the registry.

    Implements: memory/specs/008-llama-server-manager.md — AC-3
    """
    cfg = _load(ctx.obj["config_path"])
    reg = _registry(cfg)
    from prometheus_manager.scanner import scan

    states = scan(cfg.resolved_pid_dir, proxy_host=cfg.api.proxy_host)
    running_aliases = {s.alias for s in states if s.state in ("ready", "loading", "paused")}

    entries = reg.entries
    if not entries:
        console.print("[dim]Registry is empty.[/dim]")
        return

    table = Table(title="Model Registry")
    table.add_column("ID", style="bold")
    table.add_column("Port")
    table.add_column("Downloaded")
    table.add_column("Running")
    table.add_column("Path / HF Repo")

    for e in entries:
        running_mark = "[green]●[/green]" if e.id in running_aliases else "[dim]○[/dim]"
        dl_mark = "[green]✓[/green]" if e.downloaded else "[yellow]✗[/yellow]"
        location = e.path if e.path else (f"hf:{e.hf_repo}/{e.hf_filename}" if e.hf_repo else "—")
        table.add_row(e.id, str(e.port), dl_mark, running_mark, location)

    console.print(table)


# ── start ─────────────────────────────────────────────────────────────────────


@cli.command("start")
@click.argument("model_id")
@click.option("--force", is_flag=True, help="Skip capacity warning prompt.")
@click.pass_context
def cmd_start(ctx: click.Context, model_id: str, force: bool) -> None:
    """Start a registered model instance.

    Implements: memory/specs/008-llama-server-manager.md — AC-4, AC-5, AC-9, AC-10, AC-24, AC-25
    """
    cfg = _load(ctx.obj["config_path"])
    reg = _registry(cfg)

    entry = reg.get(model_id)
    if entry is None:
        err_console.print(f"[error] Model '{model_id}' not found in registry.")
        sys.exit(1)

    # Capacity check (AC-24, AC-25)
    from prometheus_manager.capacity import check_capacity
    from prometheus_manager.scanner import scan

    live = scan(cfg.resolved_pid_dir, proxy_host=cfg.api.proxy_host)
    current_rss_mb = sum(s.rss_mb for s in live)
    cap = check_capacity(
        path=Path(entry.path) if entry.path else None,
        rss_estimate_mb=entry.rss_estimate_mb,
        current_rss_mb=current_rss_mb,
    )

    if cap.level == "blocked":
        err_console.print(
            f"[error] Cannot start '{model_id}': {cap.message} "
            f"(projected {cap.projected_pct:.0f}% RAM usage — hard limit 95%)."
        )
        sys.exit(1)

    if cap.level == "warning" and not force:
        console.print(
            f"[yellow]Warning:[/yellow] {cap.message} "
            f"(projected {cap.projected_pct:.0f}% RAM usage)."
        )
        if not click.confirm("Start anyway?", default=False):
            sys.exit(0)

    from prometheus_manager.lifecycle import LifecycleError, start_instance

    try:
        with console.status(f"Starting {model_id}…"):
            ps = start_instance(model_id, cfg, reg)
        console.print(f"[green]Started[/green] {model_id} — PID {ps.pid}, port {ps.port}")
    except LifecycleError as exc:
        err_console.print(f"[error] {exc}")
        sys.exit(1)


# ── stop ──────────────────────────────────────────────────────────────────────


@cli.command("stop")
@click.argument("model_id")
@click.pass_context
def cmd_stop(ctx: click.Context, model_id: str) -> None:
    """Stop a running model instance.

    Implements: memory/specs/008-llama-server-manager.md — AC-6, AC-7
    """
    cfg = _load(ctx.obj["config_path"])
    reg = _registry(cfg)
    from prometheus_manager.lifecycle import LifecycleError, stop_instance

    try:
        with console.status(f"Stopping {model_id}…"):
            stop_instance(model_id, cfg, reg)
        console.print(f"[green]Stopped[/green] {model_id}")
    except LifecycleError as exc:
        err_console.print(f"[error] {exc}")
        sys.exit(1)


# ── pause ─────────────────────────────────────────────────────────────────────


@cli.command("pause")
@click.argument("model_id")
@click.pass_context
def cmd_pause(ctx: click.Context, model_id: str) -> None:
    """Pause (SIGSTOP) a running instance.

    Implements: memory/specs/008-llama-server-manager.md — AC-6b
    """
    cfg = _load(ctx.obj["config_path"])
    from prometheus_manager.lifecycle import LifecycleError, pause_instance

    try:
        pause_instance(model_id, cfg)
        console.print(f"[blue]Paused[/blue] {model_id}")
    except LifecycleError as exc:
        err_console.print(f"[error] {exc}")
        sys.exit(1)


# ── resume ────────────────────────────────────────────────────────────────────


@cli.command("resume")
@click.argument("model_id")
@click.pass_context
def cmd_resume(ctx: click.Context, model_id: str) -> None:
    """Resume (SIGCONT) a paused instance.

    Implements: memory/specs/008-llama-server-manager.md — AC-6c
    """
    cfg = _load(ctx.obj["config_path"])
    from prometheus_manager.lifecycle import LifecycleError, resume_instance

    try:
        resume_instance(model_id, cfg)
        console.print(f"[green]Resumed[/green] {model_id}")
    except LifecycleError as exc:
        err_console.print(f"[error] {exc}")
        sys.exit(1)


# ── restart ───────────────────────────────────────────────────────────────────


@cli.command("restart")
@click.argument("model_id")
@click.pass_context
def cmd_restart(ctx: click.Context, model_id: str) -> None:
    """Restart a model instance (stop + start).

    Implements: memory/specs/008-llama-server-manager.md — AC-8
    """
    cfg = _load(ctx.obj["config_path"])
    reg = _registry(cfg)
    from prometheus_manager.lifecycle import LifecycleError, restart_instance

    try:
        with console.status(f"Restarting {model_id}…"):
            ps = restart_instance(model_id, cfg, reg)
        console.print(f"[green]Restarted[/green] {model_id} — PID {ps.pid}, port {ps.port}")
    except LifecycleError as exc:
        err_console.print(f"[error] {exc}")
        sys.exit(1)


# ── register ──────────────────────────────────────────────────────────────────


@cli.command("register")
@click.option("--id", "model_id", prompt="Model ID", help="Unique model identifier.")
@click.option("--path", "model_path", default="", prompt="GGUF path (leave blank if downloading)")
@click.option("--port", type=int, prompt="Port", help="TCP port for llama-server.")
@click.option("--context-length", type=int, default=4096, prompt="Context length")
@click.option("--family", default="", prompt="Model family (e.g. llama, mistral)")
@click.option("--quantization", default="", prompt="Quantization (e.g. Q4_0)")
@click.option("--hf-repo", default="", help="HuggingFace repo id.")
@click.option("--hf-filename", default="", help="Filename within the HF repo.")
@click.option("--hf-sha256", default="", help="Expected SHA-256 of the GGUF.")
@click.pass_context
def cmd_register(
    ctx: click.Context,
    model_id: str,
    model_path: str,
    port: int,
    context_length: int,
    family: str,
    quantization: str,
    hf_repo: str,
    hf_filename: str,
    hf_sha256: str,
) -> None:
    """Register a model in the registry.

    Implements: memory/specs/008-llama-server-manager.md — AC-3, AC-16, AC-17
    """
    cfg = _load(ctx.obj["config_path"])
    reg = _registry(cfg)

    from prometheus_manager.registry import RegistryEntry

    entry = RegistryEntry(
        id=model_id,
        path=model_path,
        port=port,
        context_length=context_length,
        family=family,
        quantization=quantization,
        hf_repo=hf_repo,
        hf_filename=hf_filename,
        hf_sha256=hf_sha256,
        downloaded=bool(model_path),
    )
    try:
        reg.add(entry)
        console.print(f"[green]Registered[/green] '{model_id}'")
    except ValueError as exc:
        err_console.print(f"[error] {exc}")
        sys.exit(1)


# ── unregister ────────────────────────────────────────────────────────────────


@cli.command("unregister")
@click.argument("model_id")
@click.option("--yes", is_flag=True, help="Skip confirmation.")
@click.pass_context
def cmd_unregister(ctx: click.Context, model_id: str, yes: bool) -> None:
    """Remove a model from the registry (must not be running).

    Implements: memory/specs/008-llama-server-manager.md — AC-17
    """
    cfg = _load(ctx.obj["config_path"])
    reg = _registry(cfg)

    from prometheus_manager.scanner import scan

    live = scan(cfg.resolved_pid_dir, proxy_host=cfg.api.proxy_host)
    running_aliases = {s.alias for s in live if s.state in ("ready", "loading", "paused")}
    if model_id in running_aliases:
        err_console.print(
            f"[error] Cannot unregister '{model_id}' — instance is currently running. "
            "Stop it first with: pmgr stop {model_id}"
        )
        sys.exit(1)

    if not yes and not click.confirm(f"Remove '{model_id}' from registry?", default=False):
        sys.exit(0)

    try:
        reg.remove(model_id)
        console.print(f"[green]Unregistered[/green] '{model_id}'")
    except KeyError:
        err_console.print(f"[error] Model '{model_id}' not found in registry.")
        sys.exit(1)


# ── download ──────────────────────────────────────────────────────────────────


@cli.command("download")
@click.argument("model_id")
@click.pass_context
def cmd_download(ctx: click.Context, model_id: str) -> None:
    """Download a GGUF from HuggingFace Hub.

    Implements: memory/specs/008-llama-server-manager.md — AC-20, AC-21
    """
    cfg = _load(ctx.obj["config_path"])
    reg = _registry(cfg)

    entry = reg.get(model_id)
    if entry is None:
        err_console.print(f"[error] Model '{model_id}' not found in registry.")
        sys.exit(1)
    if not entry.hf_repo:
        err_console.print(f"[error] No hf_repo configured for '{model_id}'.")
        sys.exit(1)

    from rich.progress import BarColumn, DownloadColumn, Progress, TimeRemainingColumn

    from prometheus_manager.downloader import DownloadError, DownloadState
    from prometheus_manager.downloader import download_model as _dl

    progress = Progress(
        "[progress.description]{task.description}",
        BarColumn(),
        DownloadColumn(),
        TimeRemainingColumn(),
        console=console,
    )
    task_id = progress.add_task(f"Downloading {model_id}…", total=None)

    def on_progress(state: DownloadState) -> None:
        if state.total_bytes:
            progress.update(
                task_id,
                total=state.total_bytes,
                completed=state.downloaded_bytes,
            )

    with progress:
        try:
            dest = _dl(
                model_id=model_id,
                hf_repo=entry.hf_repo,
                hf_filename=entry.hf_filename,
                dest_dir=cfg.resolved_downloads_dir,
                hf_token=cfg.hf_token,
                expected_sha256=entry.hf_sha256 or None,
                on_progress=on_progress,
            )
        except DownloadError as exc:
            err_console.print(f"[error] {exc}")
            sys.exit(1)

    reg.update(model_id, downloaded=True, path=str(dest))
    console.print(f"[green]Downloaded[/green] {model_id} → {dest}")


# ── deregister ────────────────────────────────────────────────────────────────


@cli.command("deregister")
@click.argument("model_id")
@click.option("--yes", is_flag=True, help="Skip confirmation.")
@click.pass_context
def cmd_deregister(ctx: click.Context, model_id: str, yes: bool) -> None:
    """Stop instance (if running) then remove from registry.

    Implements: memory/specs/008-llama-server-manager.md — AC-6d
    """
    cfg = _load(ctx.obj["config_path"])
    reg = _registry(cfg)

    if not yes and not click.confirm(f"Stop and deregister '{model_id}'?", default=False):
        sys.exit(0)

    from prometheus_manager.lifecycle import LifecycleError, deregister_instance

    try:
        deregister_instance(model_id, cfg, reg)
        console.print(f"[green]Deregistered[/green] '{model_id}'")
    except LifecycleError as exc:
        err_console.print(f"[error] {exc}")
        sys.exit(1)
    except KeyError:
        err_console.print(f"[error] Model '{model_id}' not found in registry.")
        sys.exit(1)


# ── serve ─────────────────────────────────────────────────────────────────────


@cli.command("serve")
@click.option("--host", default=None, help="Override API bind host.")
@click.option("--port", default=None, type=int, help="Override API port.")
@click.pass_context
def cmd_serve(ctx: click.Context, host: str | None, port: int | None) -> None:
    """Start the Manager REST API.

    Implements: memory/specs/008-llama-server-manager.md — AC-11
    """
    cfg = _load(ctx.obj["config_path"])
    reg = _registry(cfg)

    bind_host = host or cfg.api.host
    bind_port = port or cfg.api.port

    import uvicorn

    from prometheus_manager.api.app import app

    app.state.registry = reg
    app.state.pid_dir = cfg.resolved_pid_dir
    app.state.jwks_url = cfg.api.jwks_url
    app.state.jwks_tls_verify = cfg.api.jwks_tls_verify
    app.state.proxy_host = (
        cfg.api.proxy_host
    )  # "" = bare-metal; "host.containers.internal" = container

    console.print(f"Starting Manager API on {bind_host}:{bind_port} …")
    uvicorn.run(app, host=bind_host, port=bind_port, log_level="info")


# ── tui ───────────────────────────────────────────────────────────────────────


@cli.command("tui")
@click.pass_context
def cmd_tui(ctx: click.Context) -> None:
    """Open the interactive TUI dashboard.

    Implements: memory/specs/008-llama-server-manager.md — AC-22, AC-22b–g, AC-26
    """
    cfg = _load(ctx.obj["config_path"])
    reg = _registry(cfg)

    try:
        from prometheus_manager.tui.app import ManagerApp
    except ImportError as exc:
        err_console.print(f"[error] TUI dependencies not available: {exc}")
        sys.exit(1)

    # Silence stdout/stderr logging before Textual takes over the terminal.
    # Any text written to stdout after this point corrupts the TUI layout.
    # See: memory/specs/008-llama-server-manager.md (fix — TUI stdout logging corruption)
    import uuid as _uuid

    from prometheus_manager.telemetry import configure_tracing, redirect_logging_for_tui

    configure_tracing(
        service="manager",
        endpoint=cfg.tracing.otlp_endpoint or None,
        disabled=cfg.tracing.disabled,
        resource_attributes={"tui.session_id": str(_uuid.uuid4())},
    )
    redirect_logging_for_tui(log_file_path=cfg.tui.log_file_path or None)

    ManagerApp(config=cfg, registry=reg).run()
