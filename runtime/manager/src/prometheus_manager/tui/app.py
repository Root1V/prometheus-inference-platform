"""Prometheus Manager TUI — 5-view Textual application.

Implements: memory/specs/008-llama-server-manager.md — AC-22, AC-22b–g, AC-24, AC-25, AC-26
"""

from __future__ import annotations

import logging
import re
import uuid
from typing import ClassVar

import psutil
import structlog
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.screen import ModalScreen
from textual.theme import Theme
from textual.widget import Widget
from textual.widgets import Button, ContentSwitcher, DataTable, Footer, Header, Label, Tab, Tabs

from ..capacity import check_capacity
from ..config import ManagerConfig
from ..lifecycle import (
    LifecycleError,
    deregister_instance,
    pause_instance,
    restart_instance,
    resume_instance,
    start_instance,
    stop_instance,
)
from ..registry import Registry, RegistryEntry
from ..scanner import scan
from ..telemetry import get_tracer
from .views.dashboard import DashboardView
from .views.discovery import (
    DiscoveryView,
    _auto_id,
    _infer_quant,
    _next_free_port,
    _shard_filenames,
)
from .views.downloads import DownloadsView
from .views.instances import InstancesView
from .views.registry import RegistryView
from .widgets.resource_bar import ResourceBar

logger = logging.getLogger(__name__)

_tracer = get_tracer("manager.tui")

# ── Theme ─────────────────────────────────────────────────────────────────────
# Reproduces the GitHub dark colour palette as a Textual Theme so that all CSS
# that uses `$primary`, `$panel`, `$foreground-muted`, etc. renders correctly
# out of the box AND stays live-switchable via the Ctrl+P theme picker.
_GITHUB_DARK_THEME = Theme(
    name="github-dark",
    primary="#58a6ff",  # blue links / active tabs
    secondary="#388bfd",  # lighter blue
    accent="#f78166",  # orange-red accent
    warning="#d29922",
    error="#f85149",
    success="#3fb950",
    background="#0d1117",  # deepest background
    surface="#161b22",  # cards / header / footer
    panel="#30363d",  # borders / dividers
    foreground="#e6edf3",
    dark=True,
    variables={
        # muted text (border titles, inactive tabs, detail labels)
        "foreground-muted": "#8b949e",
        # cursor row in DataTable
        "primary-muted": "#1c2d3c",
        # surface one step lighter (Button background)
        "surface-lighten-1": "#21262d",
        # semantic shades used by Buttons
        "primary-darken-1": "#1f6feb",
        "success-darken-1": "#1a7f37",
        "warning-darken-1": "#9e6a03",
        "error-darken-1": "#b62324",
    },
)


# ── Modals ────────────────────────────────────────────────────────────────────


class CapacityWarningModal(ModalScreen[bool]):
    """Yellow modal for 'warning' level capacity — user can override.

    Implements: memory/specs/008-llama-server-manager.md — AC-24 (soft warning modal)
    """

    def __init__(self, message: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self._message = message

    def compose(self) -> ComposeResult:
        with Widget(id="modal-container"):
            yield Label(f"[yellow]⚠ Capacity Warning[/yellow]\n\n{self._message}")
            yield Button("Start anyway", id="btn-yes", variant="warning")
            yield Button("Cancel", id="btn-no", variant="default")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "btn-yes")


class CapacityBlockedModal(ModalScreen[None]):
    """Red modal for 'blocked' level capacity — cannot start.

    Implements: memory/specs/008-llama-server-manager.md — AC-25 (hard block modal)
    """

    def __init__(self, message: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self._message = message

    def compose(self) -> ComposeResult:
        with Widget(id="modal-container"):
            yield Label(f"[red]✖ Cannot Start — Insufficient Memory[/red]\n\n{self._message}")
            yield Button("OK", id="btn-ok", variant="error")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(None)


class ConfirmModal(ModalScreen[bool]):
    """Generic confirmation dialog for destructive lifecycle actions.

    Implements: memory/specs/008-llama-server-manager.md — AC-22f (confirmation dialogs)
    """

    BINDINGS = [
        Binding("enter", "confirm", "Confirm"),
        Binding("escape", "cancel", "Cancel"),
    ]

    def __init__(self, action: str, model_id: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self._action = action
        self._model_id = model_id

    def compose(self) -> ComposeResult:
        with Widget(id="modal-container"):
            yield Label(f"{self._action}", id="modal-title")
            yield Label(
                f"Are you sure you want to [bold]{self._action.lower()}[/bold] "
                f"[cyan]{self._model_id}[/cyan]?"
            )
            with Widget(id="modal-buttons"):
                yield Button("Cancel", id="btn-cancel", variant="default")
                yield Button(self._action, id="btn-confirm", variant="primary")

    def action_confirm(self) -> None:
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "btn-confirm")


# ── Main App ──────────────────────────────────────────────────────────────────


class ManagerApp(App):
    """Prometheus Manager TUI — 5-tab application.

    Tabs: Dashboard · Instances · Registry · Downloads · Discovery
    Implements: memory/specs/008-llama-server-manager.md — AC-22 (5 views)
    """

    TITLE = "Prometheus Manager"
    DARK = True
    CSS = """
    /* ── Global ──────────────────────────────────────────────────────────── */
    Screen {
        background: $background;
        color: $foreground;
    }
    Header {
        background: $surface;
        color: $primary;
        text-style: bold;
    }
    Footer {
        background: $surface;
        color: $foreground-muted;
    }

    /* ── Tabs ────────────────────────────────────────────────────────────── */
    Tabs {
        background: $surface;
        height: 3;
    }
    Tab {
        color: $foreground-muted;
    }
    Tab.-active {
        color: $primary;
        text-style: bold;
    }
    /* When Tabs widget has keyboard focus Textual renders the active tab as a
       block cursor using $block-cursor-foreground / $block-cursor-background.
       In several themes (e.g. catppuccin-latte) $block-cursor-foreground is
       "auto 87%" which can resolve to an invisible colour.  Force an explicit
       inverted pair ($background text on $primary background) so the label is
       always readable regardless of the active theme. */
    Tabs:focus .-active {
        background: $primary;
        color: $background;
        text-style: bold;
    }
    /* Main content switcher fills remaining height */
    #main-switcher {
        background: $background;
        height: 1fr;
    }

    /* ── DataTable ───────────────────────────────────────────────────────── */
    DataTable {
        background: $background;
        color: $foreground;
    }
    /* Force every DataTable to fill its parent container's full width/height */
    DashboardView DataTable,
    InstancesView DataTable,
    RegistryView DataTable,
    DownloadsView DataTable {
        width: 100%;
        height: 1fr;
    }
    DataTable > .datatable--header {
        background: $surface;
        color: $primary;
        text-style: bold;
    }
    DataTable > .datatable--cursor {
        background: $primary-muted;
        color: $foreground;
    }
    DataTable > .datatable--hover {
        background: $boost;
    }
    DataTable > .datatable--fixed {
        background: $surface;
        color: $primary;
    }

    /* ── OptionList / CommandPalette ─────────────────────────────────────── */
    /* Textual's default uses $block-cursor-foreground = "auto 87%" which is
       not always resolved correctly against the $block-cursor-background.
       Force explicit inverted pair to guarantee contrast in every theme. */
    OptionList > .option-list--option-highlighted {
        background: $primary-muted;
        color: $foreground;
        text-style: bold;
    }
    OptionList:focus > .option-list--option-highlighted {
        background: $primary;
        color: $background;
        text-style: bold;
    }

    /* ── Buttons ─────────────────────────────────────────────────────────── */
    Button {
        background: $surface-lighten-1;
        color: $foreground;
        border: tall $panel;
    }
    Button:hover {
        background: $panel;
    }
    Button.-primary {
        background: $primary-darken-1;
        border: tall $primary;
        color: $background;
    }
    Button.-primary:hover {
        background: $primary;
    }
    Button.-success {
        background: $success-darken-1;
        border: tall $success;
        color: $background;
    }
    Button.-warning {
        background: $warning-darken-1;
        border: tall $warning;
        color: $background;
    }
    Button.-error {
        background: $error-darken-1;
        border: tall $error;
        color: $background;
    }
    Button.-error:hover {
        background: $error;
    }

    /* ── ResourceBar ─────────────────────────────────────────────────────── */
    ResourceBar {
        height: 2;
        background: $surface;
        border-bottom: solid $panel;
    }

    /* ── Modals ──────────────────────────────────────────────────────────── */
    CapacityWarningModal {
        align: center middle;
    }
    CapacityWarningModal #modal-container {
        width: 60;
        height: auto;
        border: thick $warning;
        background: $surface;
        padding: 2;
    }
    CapacityBlockedModal {
        align: center middle;
    }
    CapacityBlockedModal #modal-container {
        width: 60;
        height: auto;
        border: thick $error;
        background: $surface;
        padding: 2;
    }
    ConfirmModal {
        align: center middle;
    }
    ConfirmModal #modal-container {
        width: 54;
        height: auto;
        border: thick $primary;
        background: $surface;
        padding: 2;
    }
    ConfirmModal #modal-title {
        text-style: bold;
        color: $primary;
        margin-bottom: 1;
    }
    ConfirmModal #modal-buttons {
        layout: horizontal;
        align: right middle;
        height: auto;
        margin-top: 1;
    }
    ConfirmModal #modal-buttons Button {
        margin-left: 1;
    }
    """

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("q", "quit", "Quit", show=True),
        Binding("1", "switch_tab('dashboard')", "Dashboard"),
        Binding("2", "switch_tab('instances')", "Instances"),
        Binding("3", "switch_tab('registry')", "Registry"),
        Binding("4", "switch_tab('downloads')", "Downloads"),
        Binding("5", "switch_tab('discovery')", "Discovery"),
    ]

    def __init__(self, config: ManagerConfig, registry: Registry, **kwargs) -> None:
        super().__init__(**kwargs)
        self._config = config
        self._model_registry = registry
        self._downloads: list = []

    def compose(self) -> ComposeResult:
        yield Header()
        yield Tabs(
            Tab("[1]Dashboard", id="tab-dashboard"),
            Tab("[2]Instances", id="tab-instances"),
            Tab("[3]Registry", id="tab-registry"),
            Tab("[4]Downloads", id="tab-downloads"),
            Tab("[5]Discovery", id="tab-discovery"),
            id="main-tabs",
        )
        yield ResourceBar(id="resource-bar")
        with ContentSwitcher(id="main-switcher", initial="view-dashboard"):
            yield DashboardView(id="view-dashboard")
            yield InstancesView(id="view-instances")
            yield RegistryView(id="view-registry")
            yield DownloadsView(id="view-downloads")
            yield DiscoveryView(id="view-discovery")
        yield Footer()

    def on_mount(self) -> None:
        # Bind a session-scoped trace_id for all log events outside of _poll()
        # (on_mount, lifecycle actions triggered by buttons, etc.).
        structlog.contextvars.bind_contextvars(
            service="manager",
            component="tui",
            trace_id=f"tui-session-{str(uuid.uuid4())[:8]}",
        )
        # Register the custom github-dark theme so it is always available in
        # the Ctrl+P picker, then activate the configured startup theme.
        self.register_theme(_GITHUB_DARK_THEME)
        self.theme = self._config.tui.theme
        self.set_interval(
            self._config.dashboard.refresh_interval_s,
            self._poll,
        )

    def watch_theme(self, theme: str) -> None:
        """Display the active theme name in the Header sub-title."""
        self.sub_title = theme

    def _poll(self) -> None:
        """Periodic refresh of all views with live data."""
        # Renew trace_id per poll cycle so log lines are correlatable per refresh.
        structlog.contextvars.bind_contextvars(
            trace_id=f"tui-poll-{str(uuid.uuid4())[:8]}",
        )
        try:
            states = scan(self._config.resolved_pid_dir, proxy_host=self._config.api.proxy_host)
        except Exception:
            states = []

        registry_entries = {e.id: e for e in self._model_registry.entries}

        # Security (spec 010): reconcile stale discovery flags after crashes.
        # If a process died without going through stop_instance(), registry.yaml
        # may still have discovery=True. Clear it so the gateway doesn't route to
        # a dead backend.
        running_ids = {s.alias or s.model_id for s in states if s.alias or s.model_id}
        for entry in list(registry_entries.values()):
            if entry.discovery and entry.id not in running_ids:
                try:
                    self._model_registry.update(entry.id, discovery=False)
                    registry_entries[entry.id] = self._model_registry.get(entry.id)
                except Exception:
                    pass

        # Dashboard
        try:
            self.query_one("#view-dashboard", DashboardView).refresh_data(
                states, registry_entries, self._downloads
            )
        except Exception:
            pass

        # Instances
        try:
            self.query_one("#view-instances", InstancesView).refresh_data(states, registry_entries)
        except Exception:
            pass

        # Registry
        try:
            self.query_one("#view-registry", RegistryView).refresh_data(
                list(registry_entries.values())
            )
        except Exception:
            pass

        # Downloads
        try:
            self.query_one("#view-downloads", DownloadsView).refresh_data(self._downloads)
        except Exception:
            pass

        # Resource bar host metrics + summary stats (AC-22b, AC-22c)
        try:
            mem = psutil.virtual_memory()
            cpu = psutil.cpu_percent()
            ram_pct = mem.percent
            # Use total-available to match Activity Monitor (vm.used excludes
            # compressed/inactive memory on macOS, so it underreports)
            ram_used_gb = (mem.total - mem.available) / 2**30
            ram_total_gb = mem.total / 2**30
            running_count = len(states)
            ready_count = sum(1 for s in states if s.state == "ready")
            error_count = sum(1 for s in states if s.state == "error")
            self.query_one("#resource-bar", ResourceBar).update(
                cpu,
                ram_pct,
                None,
                ram_used_gb,
                ram_total_gb,
                running=running_count,
                ready=ready_count,
                errors=error_count,
            )
        except Exception:
            pass

    def action_switch_tab(self, tab_id: str) -> None:
        try:
            self.query_one("#main-tabs", Tabs).active = f"tab-{tab_id}"
            self.query_one("#main-switcher", ContentSwitcher).current = f"view-{tab_id}"
        except Exception:
            pass

    def on_tabs_tab_activated(self, event: Tabs.TabActivated) -> None:
        """Switch ContentSwitcher to match the activated tab."""
        if event.tab:
            view_id = str(event.tab.id).replace("tab-", "view-")
            try:
                self.query_one("#main-switcher", ContentSwitcher).current = view_id
            except Exception:
                pass
            # Re-focus search input when switching to Discovery tab
            if view_id == "view-discovery":
                try:
                    from textual.widgets import Input as _Input

                    self.query_one("#view-discovery").query_one("#search-input", _Input).focus()
                except Exception:
                    pass

    # ── lifecycle actions (delegated from views) ──────────────────────────────

    def _selected_instance_id(self) -> str | None:
        try:
            view = self.query_one("#view-instances", InstancesView)
            table = view.query_one("#instances-table", DataTable)
            row = table.cursor_row
            if 0 <= row < len(view._display_model_ids):
                return view._display_model_ids[row]
        except Exception:
            pass
        return None

    def _bind_worker_ctx(self, action: str) -> None:
        """Bind service + a fresh trace_id in the current worker thread.

        @work(thread=True) workers run in OS threads that do NOT inherit
        the contextvars from the main Textual thread.  Every worker must
        call this at its very first line so lifecycle log events carry a
        meaningful trace_id instead of 'none'.
        """
        structlog.contextvars.bind_contextvars(
            service="manager",
            component="tui",
            trace_id=f"tui-{action}-{str(uuid.uuid4())[:8]}",
        )

    def action_start_selected(self) -> None:
        model_id = self._selected_instance_id()
        if not model_id:
            return

        def _on_confirmed(confirmed: bool) -> None:
            if confirmed:
                self._start_with_capacity_check(model_id)

        self.push_screen(ConfirmModal("Start", model_id), _on_confirmed)

    @work(exclusive=False, thread=True)
    def _start_with_capacity_check(self, model_id: str) -> None:
        self._bind_worker_ctx("start")
        entry = self._model_registry.get(model_id)
        if entry is None:
            self.notify(f"Model '{model_id}' not in registry", severity="error")
            return

        from pathlib import Path

        from ..scanner import scan as _scan

        live = _scan(self._config.resolved_pid_dir, proxy_host=self._config.api.proxy_host)
        current_rss = sum(s.rss_mb for s in live)
        cap = check_capacity(
            path=Path(entry.path) if entry.path else None,
            rss_estimate_mb=entry.rss_estimate_mb,
            current_rss_mb=current_rss,
        )

        if cap.level == "blocked":
            self.call_from_thread(self._show_capacity_blocked, cap.message)
            return

        if cap.level == "warning":
            # Show warning modal on main thread, then start if confirmed
            self.call_from_thread(self._show_capacity_warning, model_id, cap.message)
            return

        self._do_start(model_id)

    def _show_capacity_blocked(self, message: str) -> None:
        self.push_screen(CapacityBlockedModal(message))

    def _show_capacity_warning(self, model_id: str, message: str) -> None:
        def on_result(confirmed: bool) -> None:
            if confirmed:
                self._do_start(model_id)

        self.push_screen(CapacityWarningModal(message), on_result)

    @work(exclusive=False, thread=True)
    def _do_start(self, model_id: str) -> None:
        from opentelemetry.trace import SpanKind, StatusCode

        self._bind_worker_ctx("start")
        with _tracer.start_as_current_span("model.start", kind=SpanKind.INTERNAL) as span:
            span.set_attribute("model_id", model_id)
            try:
                ps = start_instance(model_id, self._config, self._model_registry)
                span.set_attribute("llama_pid", ps.pid)
                span.set_status(StatusCode.OK)
                self.call_from_thread(
                    self.notify, f"Started {model_id} (PID {ps.pid})", severity="information"
                )
            except LifecycleError as exc:
                span.set_status(StatusCode.ERROR, str(exc))
                self.call_from_thread(self.notify, str(exc), severity="error")

    def action_stop_selected(self) -> None:
        model_id = self._selected_instance_id()
        if not model_id:
            return

        def _on_confirmed(confirmed: bool) -> None:
            if confirmed:
                self._do_stop(model_id)

        self.push_screen(ConfirmModal("Stop", model_id), _on_confirmed)

    @work(exclusive=False, thread=True)
    def _do_stop(self, model_id: str) -> None:
        from opentelemetry.trace import SpanKind, StatusCode

        self._bind_worker_ctx("stop")
        with _tracer.start_as_current_span("model.stop", kind=SpanKind.INTERNAL) as span:
            span.set_attribute("model_id", model_id)
            try:
                stop_instance(model_id, self._config, self._model_registry)
                span.set_attribute("exit_code", 0)
                span.set_status(StatusCode.OK)
                self.call_from_thread(self.notify, f"Stopped {model_id}", severity="information")
            except LifecycleError as exc:
                span.set_attribute("exit_code", -1)
                span.set_status(StatusCode.ERROR, str(exc))
                self.call_from_thread(self.notify, str(exc), severity="error")

    def action_pause_selected(self) -> None:
        model_id = self._selected_instance_id()
        if model_id:
            self._do_pause(model_id)

    @work(exclusive=False, thread=True)
    def _do_pause(self, model_id: str) -> None:
        self._bind_worker_ctx("pause")
        try:
            pause_instance(model_id, self._config)
            self.call_from_thread(self.notify, f"Paused {model_id}", severity="information")
        except LifecycleError as exc:
            self.call_from_thread(self.notify, str(exc), severity="error")

    def action_resume_selected(self) -> None:
        model_id = self._selected_instance_id()
        if model_id:
            self._do_resume(model_id)

    @work(exclusive=False, thread=True)
    def _do_resume(self, model_id: str) -> None:
        self._bind_worker_ctx("resume")
        try:
            resume_instance(model_id, self._config)
            self.call_from_thread(self.notify, f"Resumed {model_id}", severity="information")
        except LifecycleError as exc:
            self.call_from_thread(self.notify, str(exc), severity="error")

    def action_restart_selected(self) -> None:
        model_id = self._selected_instance_id()
        if not model_id:
            return

        def _on_confirmed(confirmed: bool) -> None:
            if confirmed:
                self._do_restart(model_id)

        self.push_screen(ConfirmModal("Restart", model_id), _on_confirmed)

    @work(exclusive=False, thread=True)
    def _do_restart(self, model_id: str) -> None:
        self._bind_worker_ctx("restart")
        try:
            ps = restart_instance(model_id, self._config, self._model_registry)
            self.call_from_thread(
                self.notify, f"Restarted {model_id} (PID {ps.pid})", severity="information"
            )
        except LifecycleError as exc:
            self.call_from_thread(self.notify, str(exc), severity="error")

    def action_deregister_selected(self) -> None:
        model_id = self._selected_instance_id()
        if not model_id:
            return

        def _on_confirmed(confirmed: bool) -> None:
            if confirmed:
                self._do_deregister(model_id)

        self.push_screen(ConfirmModal("Deregister", model_id), _on_confirmed)

    @work(exclusive=False, thread=True)
    def _do_deregister(self, model_id: str) -> None:
        self._bind_worker_ctx("deregister")
        try:
            deregister_instance(model_id, self._config, self._model_registry)
            self.call_from_thread(self.notify, f"Deregistered {model_id}", severity="information")
        except (LifecycleError, KeyError) as exc:
            self.call_from_thread(self.notify, str(exc), severity="error")

    # ── registry view actions ─────────────────────────────────────────────────

    def action_registry_add(self) -> None:
        self.notify("Use 'pmgr register' CLI command to add models.", severity="information")

    def action_registry_toggle_discovery(self, model_id: str) -> None:
        """Toggle discovery flag for a registry entry. See: memory/specs/010 AC-7."""
        entry = self._model_registry.get(model_id)
        if entry is None:
            return
        new_val = not entry.discovery
        try:
            self._model_registry.update(model_id, discovery=new_val)
            state_str = "ON" if new_val else "OFF"
            self.notify(f"Discovery {state_str} for '{model_id}'", severity="information")
        except Exception as exc:
            self.notify(str(exc), severity="error")

    def action_registry_delete(self) -> None:
        try:
            view = self.query_one("#view-registry", RegistryView)
            table = view.query_one("#registry-table")
            row = table.cursor_row
            if 0 <= row < len(view._entries):
                model_id = view._entries[row].id
                self._do_unregister(model_id)
        except Exception:
            pass

    @work(exclusive=False, thread=True)
    def _do_unregister(self, model_id: str) -> None:
        self._bind_worker_ctx("unregister")
        try:
            self._model_registry.remove(model_id)
            self.call_from_thread(
                self.notify, f"Removed '{model_id}' from registry", severity="information"
            )
        except Exception as exc:
            self.call_from_thread(self.notify, str(exc), severity="error")

    def action_registry_download(self) -> None:
        try:
            view = self.query_one("#view-registry", RegistryView)
            table = view.query_one("#registry-table")
            row = table.cursor_row

            # Use _row_ids (same mapping used by the table) to get the model_id.
            # Direct _entries[row] is wrong when a separator row shifts the index.
            if not (0 <= row < len(view._row_ids)):
                self.notify("No model selected", severity="warning")
                return
            model_id = view._row_ids[row]
            if model_id is None:
                self.notify("No model selected", severity="warning")
                return

            entry = next((e for e in view._entries if e.id == model_id), None)
            if entry is None:
                self.notify("No model selected", severity="warning")
                return

            # Guard: already downloaded
            if getattr(entry, "downloaded", False):
                self.notify(
                    f"'{model_id}' is already downloaded",
                    severity="warning",
                )
                return

            # Guard: no HF metadata
            if not getattr(entry, "hf_repo", None):
                self.notify(
                    f"'{model_id}' has no HF repo configured — "
                    "add hf_repo and hf_filename to registry.yaml",
                    severity="error",
                )
                return

            # Guard: already in download queue
            _active = {"queued", "downloading", "verifying"}
            already = any(
                (d.model_id == model_id or d.model_id.startswith(f"{model_id} ["))
                and getattr(d, "status", "") in _active
                for d in self._downloads
            )
            if already:
                self.notify(f"'{model_id}' is already downloading", severity="warning")
                return

            self.notify(f"Queuing download for '{model_id}'…", severity="information")
            self._do_download(model_id)
        except Exception as exc:
            self.notify(f"Download error: {exc}", severity="error")

    @work(exclusive=False, thread=True)
    def _do_download(self, model_id: str) -> None:
        from opentelemetry.trace import SpanKind, StatusCode
        from urllib.parse import urlparse

        self._bind_worker_ctx("download")
        from ..downloader import DownloadError, DownloadState, download_model

        entry = self._model_registry.get(model_id)
        if entry is None or not entry.hf_repo:
            self.call_from_thread(
                self.notify, f"No HF repo configured for '{model_id}'", severity="error"
            )
            return

        # Derive the download host from the HF repo for the span (no full URL — AC-27)
        _hf_base = "https://huggingface.co"
        _download_url_host = urlparse(_hf_base).hostname or "huggingface.co"

        filenames = entry.hf_filenames if entry.hf_filenames else [entry.hf_filename]
        total = len(filenames)

        with _tracer.start_as_current_span("model.download", kind=SpanKind.INTERNAL) as span:
            span.set_attribute("model_id", model_id)
            span.set_attribute("download_url_host", _download_url_host)

            # Register all shard states as "queued" up front so the downloads view
            # shows them immediately. See memory/specs/011-downloads-view-redesign.md
            shard_states: list[DownloadState] = []
            for idx, hf_filename in enumerate(filenames):
                label = f"{model_id} [{idx + 1}/{total}]" if total > 1 else model_id
                ds = DownloadState(
                    model_id=label,
                    hf_repo=entry.hf_repo,
                    hf_filename=hf_filename,
                    status="queued",
                )
                shard_states.append(ds)
                self._downloads.append(ds)

            first_dest = None
            for ds in shard_states:

                def on_progress(state: DownloadState, _ds: DownloadState = ds) -> None:
                    _ds.total_bytes = state.total_bytes
                    _ds.downloaded_bytes = state.downloaded_bytes
                    _ds.status = state.status
                    _ds.error = state.error
                    _ds.speed_bps = state.speed_bps
                    _ds.eta_seconds = state.eta_seconds
                    # Bridge: UI sets _ds.cancel_requested; downloader checks state.cancel_requested
                    if _ds.cancel_requested:
                        state.cancel_requested = True

                try:
                    dest = download_model(
                        model_id=ds.model_id,
                        hf_repo=entry.hf_repo,
                        hf_filename=ds.hf_filename,
                        dest_dir=self._config.resolved_downloads_dir,
                        hf_token=self._config.hf_token,
                        expected_sha256=(entry.hf_sha256 or None) if total == 1 else None,
                        on_progress=on_progress,
                        ca_bundle=self._config.resolved_ca_bundle,
                    )
                    if ds.status != "cancelled" and first_dest is None:
                        first_dest = dest
                except DownloadError as exc:
                    ds.status = "failed"
                    ds.error = str(exc)
                    span.set_status(StatusCode.ERROR, str(exc))
                    self.call_from_thread(self.notify, str(exc), severity="error")
                    # Cancel all remaining queued shards
                    for remaining in shard_states:
                        if remaining.status == "queued":
                            remaining.status = "cancelled"
                    return

                # Propagate user-requested cancel to remaining shards
                if ds.status == "cancelled":
                    for remaining in shard_states:
                        if remaining.status == "queued":
                            remaining.status = "cancelled"
                    return

            # All shards done — update registry once
            span.set_attribute(
                "model_size_bytes", sum(s.total_bytes for s in shard_states if s.total_bytes)
            )
            if all(s.status == "done" for s in shard_states) and first_dest is not None:
                self._model_registry.update(model_id, downloaded=True, path=str(first_dest))
                suffix = f" ({total} parts)" if total > 1 else ""
                span.set_status(StatusCode.OK)
                self.call_from_thread(
                    self.notify, f"Downloaded {model_id}{suffix}", severity="information"
                )

    def action_downloads_retry(self, model_id: str) -> None:
        """Retry a failed/cancelled download. See memory/specs/011 — AC-19."""
        # Strip shard label "model-id [N/M]" to get the base registry model_id
        base_id = re.sub(r" \[\d+/\d+\]$", "", model_id)
        self._downloads = [
            d
            for d in self._downloads
            if d.model_id != base_id and not d.model_id.startswith(f"{base_id} [")
        ]
        self._do_download(base_id)

    def action_downloads_clear_history(self) -> None:
        """Remove all done/failed/cancelled entries. See memory/specs/011 — AC-20."""
        self._downloads = [
            d
            for d in self._downloads
            if getattr(d, "status", "") not in {"done", "failed", "cancelled"}
        ]

    # ── discovery actions ─────────────────────────────────────────────────────
    # See: memory/specs/012-discovery-view-redesign.md — AC-9

    def action_discovery_download(self) -> None:
        """Auto-ID + auto-port, add to registry, queue download, switch tab."""
        try:
            view = self.query_one("#view-discovery", DiscoveryView)
        except Exception:
            return
        if not view._selected_file:
            self.notify("Select a GGUF file first", severity="warning")
            return
        shard_files = _shard_filenames(view._selected_file, view._files)
        existing_ids = {e.id for e in self._model_registry.entries}
        model_id = _auto_id(shard_files[0], existing_ids)
        used_ports = {e.port for e in self._model_registry.entries}
        port = _next_free_port(used_ports)
        quant = _infer_quant(shard_files[0])
        entry = RegistryEntry(
            id=model_id,
            port=port,
            context_length=4096,
            hf_repo=view._selected_repo,
            hf_filename=shard_files[0],
            hf_filenames=shard_files if len(shard_files) > 1 else [],
            quantization=quant,
            downloaded=False,
        )
        try:
            self._model_registry.add(entry)
        except Exception as exc:
            self.notify(f"Registry error: {exc}", severity="error")
            return
        self._do_download(model_id)
        self.action_switch_tab("downloads")
        self.notify(f"Queued download: {model_id}", severity="information")
