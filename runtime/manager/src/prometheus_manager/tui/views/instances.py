"""Instances view — table + capacity + model detail panel.

Spec layout (View 2):
  ┌─ Instances ── [s]tart [S]top [r]estart [p]ause [u]resume [x]deregister ──┐
  │  ID  PID  Port  State  CPU%  CPU▁▂▃  RAM GB  RAM▁▂▃  GPU%  M             │
  ├─ Capacity ───────────────────────────────────────────────────────────────  │
  │  Estimated RAM usage: 4.9/16 GB  ██░░░  31%  ✓ OK                        │
  ├─ Model Detail ── <model-id> ──────────────────────────────────────────────│
  │  ┌─ Identity ──────────────────┐  ┌─ Context & Limits ─────────────────┐  │
  │  └─────────────────────────────┘  └────────────────────────────────────┘  │
  │  ┌─ Runtime Metrics ──────────────────────────────────────────────────┐   │
  │  └────────────────────────────────────────────────────────────────────┘   │
  └─────────────────────────────────────────────────────────────────────────── ┘

Implements: memory/specs/008-llama-server-manager.md — AC-22c, AC-22d, AC-22e, AC-22f, AC-22g
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

import httpx
import psutil
from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import DataTable, Static

from ..utils import fmt_size

_SPARK_CHARS = " ▁▂▃▄▅▆▇█"


def _fmt_cpu(pct: float) -> str:
    """Format a CPU% value.  Show 1 decimal for values below 10 so that small
    but non-zero readings (e.g. 0.3%) are visible instead of rounding to '0%'."""
    if pct < 10.0:
        return f"{pct:.1f}%"
    return f"{pct:.0f}%"


def _sparkline(history: list[float], width: int = 7) -> str:
    """Render a Unicode block sparkline from a list of float samples."""
    if not history:
        return " " * width
    max_val = max(history) or 1.0
    samples = list(history[-width:])
    padded = [0.0] * (width - len(samples)) + samples
    return "".join(_SPARK_CHARS[int(v / max_val * 8)] for v in padded)


def _format_uptime(seconds: float) -> str:
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    if h:
        return f"{h}h {m}m {sec}s"
    if m:
        return f"{m}m {sec}s"
    return f"{sec}s"


def _parse_prometheus_metrics(text: str) -> dict[str, float]:
    """Parse Prometheus text format into a {metric_name: value} dict."""
    result: dict[str, float] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # Strip label part e.g. llamacpp:foo{label="x"} 1.5
        m = re.match(r"^([\w:]+)(?:\{[^}]*\})?\s+([\d.eE+\-]+)", line)
        if m:
            try:
                result[m.group(1)] = float(m.group(2))
            except ValueError:
                pass
    return result


def _detect_chat_template(template: str) -> str:
    """Derive a short human-readable label from a raw Jinja2 chat template string."""
    if not template:
        return "—"
    t = template.lower()
    if "<|start_header_id|>" in t or "<|eot_id|>" in t:
        return "llama3"
    if "<|im_start|>" in t:
        return "chatml"
    if "[inst]" in t or "[/inst]" in t:
        return "mistral"
    if "<|user|>" in t and "<|assistant|>" in t:
        return "zephyr"
    if "### human" in t or "### assistant" in t:
        return "alpaca"
    # Fallback: first meaningful 18 chars
    compact = " ".join(template.split())[:18]
    return compact + "…" if len(" ".join(template.split())) > 18 else compact


def _find_cmdline_arg(cmdline: list[str], *flags: str) -> str | None:
    """Return the value of the first matching --flag from a cmdline list."""
    for i, tok in enumerate(cmdline):
        for flag in flags:
            if tok == flag and i + 1 < len(cmdline):
                return cmdline[i + 1]
            if tok.startswith(flag + "="):
                return tok.split("=", 1)[1]
    return None


class InstancesView(Vertical):
    """View 2: instances table + capacity bar + model detail panel (vertical layout).

    Shows ALL registered models. Running instances first, then stopped.
    Implements: memory/specs/008-llama-server-manager.md — AC-22c, AC-22d, AC-22e, AC-22f, AC-22g
    """

    BINDINGS = [
        Binding("s", "start", "Start", show=True),
        Binding("x", "stop", "Stop", show=True),
        Binding("p", "pause", "Pause", show=True),
        Binding("u", "resume", "Resume", show=True),
        Binding("r", "restart", "Restart", show=True),
        Binding("ctrl+d", "deregister", "Deregister", show=True),
    ]

    DEFAULT_CSS = """
    InstancesView {
        padding: 0;
    }
    InstancesView #inst-group {
        height: 1fr;
        border: solid $panel;
        border-title-color: $foreground-muted;
        border-title-style: bold;
        padding: 0;
    }
    InstancesView #inst-group DataTable {
        width: 100%;
        height: 100%;
    }
    InstancesView #capacity-group {
        height: auto;
        border: solid $panel;
        border-title-color: $foreground-muted;
        border-title-style: bold;
        padding: 0 1;
    }
    InstancesView #capacity-line {
        height: 1;
    }
    InstancesView #detail-group {
        height: auto;
        border: solid $panel;
        border-title-color: $foreground-muted;
        border-title-style: bold;
        padding: 0;
    }
    InstancesView #detail-row1 {
        height: auto;
    }
    InstancesView #identity-group {
        width: 1fr;
        height: auto;
        border: solid $panel;
        border-title-color: $foreground-muted;
        padding: 0 1;
    }
    InstancesView #context-group {
        width: 1fr;
        height: auto;
        border: solid $panel;
        border-title-color: $foreground-muted;
        padding: 0 1;
    }
    InstancesView #metrics-group {
        height: 6;
        border: solid $panel;
        border-title-color: $foreground-muted;
        padding: 0 1;
    }
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._display_model_ids: list[str] = []
        self._running_by_id: dict = {}
        self._registry_entries: dict = {}
        self._selected_model_id: str = ""
        # Cache of fetched live data: model_id → (context_lines, metrics_text)
        self._live_cache: dict[str, tuple[list[str], str]] = {}

    def compose(self) -> ComposeResult:
        with Vertical(id="inst-group") as grp:
            grp.border_title = "Instances  [s]tart [S]top [r]estart [p]ause [u]resume [x]deregister"
            yield DataTable(id="instances-table")
        with Vertical(id="capacity-group") as grp:
            grp.border_title = "Capacity"
            yield Static("", id="capacity-line")
        with Vertical(id="detail-group") as grp:
            grp.border_title = "Model Detail"
            with Horizontal(id="detail-row1"):
                with Vertical(id="identity-group") as ig:
                    ig.border_title = "Identity"
                    yield Static("", id="detail-identity")
                with Vertical(id="context-group") as cg:
                    cg.border_title = "Context & Limits"
                    yield Static("", id="detail-context")
            with Vertical(id="metrics-group") as mg:
                mg.border_title = "Runtime Metrics"
                yield Static("[dim](select an instance)[/dim]", id="detail-metrics")

    def on_mount(self) -> None:
        table = self.query_one("#instances-table", DataTable)
        table.cursor_type = "row"
        table.add_columns(
            "ID",
            "PID",
            "Port",
            "State",
            "CPU%",
            "CPU▁▂▃",
            "RAM GB",
            "RAM▁▂▃",
            "GPU%",
            "M",
            "Size",
        )
        self._update_capacity()
        # Periodically refresh live llama.cpp data for the selected ready instance
        self.set_interval(5.0, self._periodic_refresh_live)

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        idx = event.cursor_row
        if 0 <= idx < len(self._display_model_ids):
            model_id = self._display_model_ids[idx]
            prev_id = self._selected_model_id
            self._selected_model_id = model_id
            state = self._running_by_id.get(model_id)
            entry = self._registry_entries.get(model_id)
            self._update_detail(model_id, state, entry)
            # Only fetch when the user navigates to a DIFFERENT row.
            # When move_cursor() from refresh_data fires this event for the same row,
            # skip — the 5 s periodic timer handles refreshes on unchanged selection.
            if model_id != prev_id and state is not None and getattr(state, "state", "") == "ready":
                self._live_cache.pop(model_id, None)  # clear so "fetching…" shows briefly
                self._fetch_live_data(model_id, state.port, state.pid, entry)

    def _periodic_refresh_live(self) -> None:
        """Timer callback: re-fetch live llama.cpp data every 5 s for the selected instance."""
        model_id = self._selected_model_id
        if not model_id:
            return
        state = self._running_by_id.get(model_id)
        if state is not None and getattr(state, "state", "") == "ready":
            entry = self._registry_entries.get(model_id)
            self._fetch_live_data(model_id, state.port, state.pid, entry)

    # ── Detail panel ──────────────────────────────────────────────────────

    def _update_detail(self, model_id: str, state, entry) -> None:
        """Populate Identity, Context & Limits, and Runtime Metrics sub-groups."""
        self.query_one("#detail-group").border_title = f"Model Detail ── {model_id}"

        now = datetime.now(tz=UTC)
        uptime_s = (now - state.started_at).total_seconds() if state and state.started_at else 0.0
        is_orphan = state is not None and entry is None
        is_running = state is not None

        # ── Identity ──────────────────────────────────────────────────────
        if is_orphan:
            identity_lines = [
                f"[dim]PID         [/dim]  {state.pid}",
                f"[dim]Port        [/dim]  {state.port}",
                f"[dim]Uptime      [/dim]  {_format_uptime(uptime_s)}",
                "",
                "[yellow]Not in registry — use [a] to register[/yellow]",
            ]
            context_lines = ["[dim](orphan — no registry entry)[/dim]"]
        else:
            # AC-19/AC-20 (spec 010): Identity shows only runtime fields.
            # Catalog metadata (family, quant, GGUF, HF repo, SHA-256) belongs in Registry view.
            pid_str = str(state.pid) if state else "—"
            port_str = str(state.port if state else (getattr(entry, "port", "—") if entry else "—"))
            state_str = str(getattr(state, "state", "stopped")) if state else "stopped"
            uptime_str = _format_uptime(uptime_s) if state else "—"
            identity_lines = [
                f"[dim]PID     [/dim]  {pid_str}",
                f"[dim]Port    [/dim]  {port_str}",
                f"[dim]State   [/dim]  {state_str}",
                f"[dim]Uptime  [/dim]  {uptime_str}",
            ]
            ctx_len = getattr(entry, "context_length", 0) or 0
            if is_running:
                cached = self._live_cache.get(model_id)
                if cached:
                    context_lines = cached[0]
                else:
                    context_lines = [
                        f"[dim]Context window[/dim]  {ctx_len:,} tokens",
                        f"[dim]Max tokens    [/dim]  {ctx_len:,}",
                        "[dim]n_ctx used    [/dim]  [dim]fetching…[/dim]",
                        "[dim]Active slots  [/dim]  [dim]fetching…[/dim]",
                        "[dim]Batch size    [/dim]  [dim]fetching…[/dim]",
                        "[dim]GPU layers    [/dim]  [dim]fetching…[/dim]",
                        "[dim]Threads       [/dim]  [dim]fetching…[/dim]",
                    ]
            else:
                context_lines = [
                    f"[dim]Context window[/dim]  {ctx_len:,} tokens",
                    f"[dim]Max tokens    [/dim]  {ctx_len:,}",
                    "[dim]n_ctx used    [/dim]  —",
                    "[dim]Active slots  [/dim]  —",
                    "[dim]Batch size    [/dim]  —",
                    "[dim]GPU layers    [/dim]  —",
                    "[dim]Threads       [/dim]  —",
                ]

        self.query_one("#detail-identity", Static).update("\n".join(identity_lines))
        self.query_one("#detail-context", Static).update("\n".join(context_lines))

        # ── Runtime Metrics ───────────────────────────────────────────────
        if is_orphan:
            metrics_text = "[dim]Not in registry[/dim]"
        elif not is_running:
            metrics_text = "[dim]― not running ―[/dim]"
        else:
            port = state.port if state else (getattr(entry, "port", 0) if entry else 0)
            uptime_str = _format_uptime(uptime_s)
            cached = self._live_cache.get(model_id)
            if cached:
                metrics_text = cached[1]
            else:
                metrics_text = "\n".join(
                    [
                        f"[dim]Uptime          [/dim] {uptime_str:<22}  [dim]Tokens served  [/dim] [dim]fetching…[/dim]",
                        f"[dim]Avg tokens/s    [/dim] [dim]fetching…[/dim]{'':10}  [dim]Requests active[/dim] [dim]fetching…[/dim]",
                        f"[dim]Prompt eval     [/dim] [dim]fetching…[/dim]{'':10}  [dim]Chat template  [/dim] [dim]fetching…[/dim]",
                        f"[dim]Token eval      [/dim] [dim]fetching…[/dim]{'':10}  "
                        f"[dim]Backend URL     [/dim] http://127.0.0.1:{port}",
                    ]
                )
        self.query_one("#detail-metrics", Static).update(metrics_text)

    # ── Live data fetch worker ────────────────────────────────────────────

    @work(exclusive=True, name="fetch-detail", thread=False)
    async def _fetch_live_data(self, model_id: str, port: int, pid: int, entry) -> None:
        """Async worker: fetch live metadata from llama.cpp and update detail panels.

        Endpoints used:
          GET /props   → n_ctx, total_slots (always available)
          GET /slots   → active slot count, chat_format, per-slot n_decoded
          GET /metrics → tokens/s, throughput (requires --metrics flag; 501 = graceful skip)
        Also reads psutil cmdline for --n-gpu-layers and --threads.
        """
        base = f"http://127.0.0.1:{port}"
        _t = httpx.Timeout(2.0)

        props: dict = {}
        slots_data: list = []
        metrics: dict[str, float] = {}

        async with httpx.AsyncClient(timeout=_t) as client:
            try:
                r = await client.get(f"{base}/props")
                if r.status_code == 200:
                    props = r.json()
            except Exception:
                pass

            try:
                r = await client.get(f"{base}/slots")
                if r.status_code == 200:
                    slots_data = r.json()
            except Exception:
                pass

            try:
                r = await client.get(f"{base}/metrics")
                if r.status_code == 200:
                    metrics = _parse_prometheus_metrics(r.text)
                # 501 = --metrics not enabled; gracefully ignored
            except Exception:
                pass

        # ── GPU layers + threads + batch size from process cmdline ─────────
        gpu_layers_str = "—"
        threads_str = "—"
        batch_size_str = "—"
        try:
            cmdline = psutil.Process(pid).cmdline()
            ngl = _find_cmdline_arg(cmdline, "-ngl", "--n-gpu-layers", "--gpu-layers")
            if ngl is not None:
                gpu_layers_str = ngl
            tt = _find_cmdline_arg(cmdline, "-t", "--threads")
            if tt is not None:
                threads_str = tt
            bs = _find_cmdline_arg(cmdline, "-b", "--batch-size", "--ubatch-size")
            if bs is not None:
                batch_size_str = bs
        except Exception:
            pass

        # ── Derived values ────────────────────────────────────────────────
        n_ctx: int = int(props.get("default_generation_settings", {}).get("n_ctx", 0) or 0)
        total_slots: int = int(props.get("total_slots", 0) or 0)
        active_slots: int = sum(1 for s in (slots_data or []) if s.get("is_processing", False))

        # Chat template: read full Jinja2 string from /props and detect format name
        chat_format = _detect_chat_template(props.get("chat_template", "") or "")

        # n_ctx used via KV cache ratio
        kv_ratio = metrics.get("llamacpp:kv_cache_usage_ratio")
        if kv_ratio is not None and n_ctx:
            n_ctx_used_str = f"{int(kv_ratio * n_ctx):,} / {n_ctx:,}  {kv_ratio * 100:.0f}%"
        else:
            n_ctx_used_str = "—"

        # Throughput
        gen_tps = metrics.get("llamacpp:predicted_tokens_seconds", 0.0)
        prompt_tps = metrics.get("llamacpp:prompt_tokens_seconds", 0.0)
        tokens_total = metrics.get("llamacpp:tokens_predicted_total")
        req_active = int(metrics.get("llamacpp:requests_processing", 0))

        gen_ms_str = f"{1000 / gen_tps:.1f} ms/tok" if gen_tps > 0 else "—"
        prompt_ms_str = f"{1000 / prompt_tps:.1f} ms/tok" if prompt_tps > 0 else "—"
        gen_tps_str = f"{gen_tps:.1f} tk/s" if gen_tps > 0 else "—"
        tokens_str = f"{int(tokens_total):,}" if tokens_total is not None else "—"

        ctx_len = getattr(entry, "context_length", 0) or n_ctx or 0

        context_lines = [
            f"[dim]Context window[/dim]  {ctx_len:,} tokens",
            f"[dim]Max tokens    [/dim]  {ctx_len:,}",
            f"[dim]n_ctx used    [/dim]  {n_ctx_used_str}",
            f"[dim]Active slots  [/dim]  {active_slots} / {total_slots}",
            f"[dim]Batch size    [/dim]  {batch_size_str}",
            f"[dim]GPU layers    [/dim]  {gpu_layers_str}",
            f"[dim]Threads       [/dim]  {threads_str}",
        ]

        now = datetime.now(tz=UTC)
        state = self._running_by_id.get(model_id)
        uptime_s = (now - state.started_at).total_seconds() if state and state.started_at else 0.0
        uptime_str = _format_uptime(uptime_s)
        port_val = state.port if state else port

        metrics_text = "\n".join(
            [
                f"[dim]Uptime          [/dim] {uptime_str:<22}  [dim]Tokens served  [/dim] {tokens_str}",
                f"[dim]Avg tokens/s    [/dim] {gen_tps_str:<22}  [dim]Requests active[/dim] {req_active}",
                f"[dim]Prompt eval     [/dim] {prompt_ms_str:<22}  [dim]Chat template  [/dim] {chat_format}",
                f"[dim]Token eval      [/dim] {gen_ms_str:<22}  "
                f"[dim]Backend URL     [/dim] http://127.0.0.1:{port_val}",
            ]
        )

        # Store in cache so _update_detail uses live values without re-fetching
        self._live_cache[model_id] = (context_lines, metrics_text)

        # Only update widgets if the user is still on this model
        if self._selected_model_id != model_id:
            return
        try:
            self.query_one("#detail-context", Static).update("\n".join(context_lines))
            self.query_one("#detail-metrics", Static).update(metrics_text)
        except Exception:
            pass

    # ── Capacity bar ──────────────────────────────────────────────────────

    def _update_capacity(self) -> None:
        try:
            vm = psutil.virtual_memory()
            total_gb = vm.total / 2**30
            # Use total-available to match Activity Monitor (vm.used excludes
            # compressed/inactive memory on macOS, so it underreports)
            used_gb = (vm.total - vm.available) / 2**30
            pct = vm.percent
            filled = round(pct / 100 * 10)
            bar = "█" * filled + "░" * (10 - filled)
            color = "green" if pct < 70 else ("yellow" if pct < 85 else "red")
            if pct < 85:
                note = "[green]✓ OK to start more instances[/green]"
            elif pct < 95:
                note = "[yellow]⚠ Warning: high memory usage[/yellow]"
            else:
                note = "[red]✗ Critical: insufficient memory[/red]"
            line = (
                f"Estimated RAM usage: {used_gb:.1f} / {total_gb:.1f} GB  "
                f"[{color}]{bar}[/{color}]  {pct:.0f}%   {note}"
            )
            self.query_one("#capacity-line", Static).update(line)
        except Exception:
            pass

    # ── Table refresh ─────────────────────────────────────────────────────

    def refresh_data(self, states: list, registry_entries: dict) -> None:
        self._running_by_id = {(s.alias or s.model_id or ""): s for s in states}
        self._registry_entries = registry_entries

        # Evict cached live data for instances that are no longer running
        for mid in list(self._live_cache.keys()):
            if mid not in self._running_by_id:
                del self._live_cache[mid]

        table = self.query_one("#instances-table", DataTable)
        # Preserve the currently selected model so we can restore cursor after rebuild
        prev_selection = self._selected_model_id
        table.clear()
        self._display_model_ids = []
        seen: set[str] = set()

        # Running instances first, then stopped
        running_entries = [
            (eid, e) for eid, e in registry_entries.items() if eid in self._running_by_id
        ]
        stopped_entries = [
            (eid, e) for eid, e in registry_entries.items() if eid not in self._running_by_id
        ]

        for entry_id, entry in running_entries + stopped_entries:
            self._display_model_ids.append(entry_id)
            seen.add(entry_id)
            s = self._running_by_id.get(entry_id)
            if s is not None:
                state_style = {
                    "ready": "green",
                    "loading": "yellow",
                    "paused": "blue",
                    "error": "red",
                    "unknown": "magenta",
                }.get(s.state, "dim")
                gpu_cell = f"{s.gpu_percent:.0f}%" if s.gpu_percent is not None else "—"
                size_cell = fmt_size(entry.path) if entry and entry.downloaded else "—"
                table.add_row(
                    entry_id,
                    str(s.pid),
                    str(s.port),
                    f"[{state_style}]● {s.state}[/{state_style}]",
                    _fmt_cpu(s.cpu_percent),
                    _sparkline(s.cpu_history),
                    f"{s.rss_mb / 1024:.2f}",
                    _sparkline(s.rss_history),
                    gpu_cell,
                    "✓",
                    size_cell,
                )
            else:
                size_cell = fmt_size(entry.path) if entry and entry.downloaded else "—"
                table.add_row(
                    entry_id,
                    "—",
                    str(entry.port) if entry.port else "—",
                    "[dim]○ stopped[/dim]",
                    "—",
                    " " * 7,
                    "—",
                    " " * 7,
                    "—",
                    "✓",
                    size_cell,
                )

        # Orphan processes: running but not in registry
        # AC-15: memory/specs/012-discovery-view-redesign.md — add separator before orphan rows
        orphans = [s for s in states if (s.alias or s.model_id or "?") not in seen]
        if orphans:
            sep = "─── Unmanaged processes ───"
            self._display_model_ids.append(sep)
            table.add_row(
                f"[dim]{sep}[/dim]",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
            )
        for s in orphans:
            mid = s.alias or s.model_id or "?"
            self._display_model_ids.append(mid)
            gpu_cell = f"{s.gpu_percent:.0f}%" if s.gpu_percent is not None else "—"
            table.add_row(
                mid,
                str(s.pid),
                str(s.port),
                f"[yellow]⚠ {s.state}[/yellow]",
                _fmt_cpu(s.cpu_percent),
                _sparkline(s.cpu_history),
                f"{s.rss_mb / 1024:.2f}",
                _sparkline(s.rss_history),
                gpu_cell,
                "[red]✗[/red]",
                "—",
            )

        self._update_capacity()

        # Restore cursor to the previously selected row (prevents jump to row 0)
        if prev_selection and prev_selection in self._display_model_ids:
            restore_idx = self._display_model_ids.index(prev_selection)
            table.move_cursor(row=restore_idx)
        elif self._display_model_ids:
            # First population: set the selection and kick off a live fetch.
            # We must do this here because by the time on_data_table_row_highlighted
            # fires from add_row, _selected_model_id is already set to this value,
            # so the model_id != prev_id guard would block the fetch.
            first_id = self._display_model_ids[0]
            self._selected_model_id = first_id
            s = self._running_by_id.get(first_id)
            if s is not None and getattr(s, "state", "") == "ready":
                e = self._registry_entries.get(first_id)
                self._fetch_live_data(first_id, s.port, s.pid, e)

        # Refresh detail panel for the currently selected model
        if self._selected_model_id:
            s = self._running_by_id.get(self._selected_model_id)
            e = self._registry_entries.get(self._selected_model_id)
            if s or e:
                self._update_detail(self._selected_model_id, s, e)

    # ── Lifecycle action stubs ────────────────────────────────────────────

    def action_start(self) -> None:
        self.app.action_start_selected()

    def action_stop(self) -> None:
        self.app.action_stop_selected()

    def action_pause(self) -> None:
        self.app.action_pause_selected()

    def action_resume(self) -> None:
        self.app.action_resume_selected()

    def action_restart(self) -> None:
        self.app.action_restart_selected()

    def action_deregister(self) -> None:
        self.app.action_deregister_selected()
