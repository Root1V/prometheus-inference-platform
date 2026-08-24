"""Dashboard view — summary + compact instance table + active downloads.

Spec layout (AC-22b):
  ● Running: 2  ○ Stopped: 9  ↓ Downloading: 1  ✓ Downloaded: 4  ✗ Missing: 7
  ┌─ Instances ─────────────────────────────────────────────────────────────┐
  │ ID  State  Port  CPU%  CPU▁▂▃  RAM GB  RAM▁▂▃  GPU%                    │
  ├─ Active Downloads ──────────────────────────────────────────────────────┤
  │ mistral-7b  ████████░░  62%  2.3/3.7 GB  ETA 1m 48s                    │
  └─────────────────────────────────────────────────────────────────────────┘

Implements: memory/specs/008-llama-server-manager.md — AC-22b, AC-22c
"""

from __future__ import annotations

import contextlib
from typing import Any

from prometheus_manager_core.downloader import DownloadState
from prometheus_manager_core.registry import RegistryEntry
from prometheus_manager_core.scanner import ProcessState
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import DataTable, Static

from ..utils import fmt_size

_SPARK_CHARS = " ▁▂▃▄▅▆▇█"


def _sparkline(history: list[float], width: int = 10) -> str:
    """Render a 10-char Unicode block sparkline from a list of float samples."""
    if not history:
        return " " * width
    max_val = max(history) or 1.0
    samples = list(history[-width:])
    padded = [0.0] * (width - len(samples)) + samples
    return "".join(_SPARK_CHARS[int(v / max_val * 8)] for v in padded)


class DashboardView(Vertical):
    """View 1: summary stats, compact instance table, active downloads.

    Implements: memory/specs/008-llama-server-manager.md — AC-22b
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        # Tracks row order so cursor can be restored after table.clear().
        # The key is read synchronously from table.cursor_row BEFORE clear(),
        # avoiding async RowHighlighted timing races.
        self._display_row_keys: list[str] = []

    DEFAULT_CSS = """
    DashboardView {
        padding: 0;
    }
    DashboardView #dash-summary-group {
        height: auto;
        padding: 0 1;
        border: solid $panel;
        border-title-color: $foreground-muted;
        border-title-style: bold;
    }
    DashboardView #dash-table-group {
        height: 1fr;
        border: solid $panel;
        border-title-color: $foreground-muted;
        border-title-style: bold;
        padding: 0;
    }
    DashboardView #dash-downloads-group {
        height: auto;
        padding: 0 1;
        border: solid $panel;
        border-title-color: $foreground-muted;
        border-title-style: bold;
    }
    DashboardView #dash-summary {
        height: 1;
        color: $foreground-muted;
    }
    DashboardView #dash-downloads {
        height: auto;
        color: $foreground-muted;
    }
    DashboardView DataTable {
        width: 100%;
        height: 100%;
    }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="dash-summary-group") as grp:
            grp.border_title = "Summary"
            yield Static("", id="dash-summary")
        with Vertical(id="dash-table-group") as grp:
            grp.border_title = "Instances (compact)"
            yield DataTable(id="dash-table")
        with Vertical(id="dash-downloads-group") as grp:
            grp.border_title = "Active Downloads"
            yield Static("[dim]No active downloads[/dim]", id="dash-downloads")

    def on_mount(self) -> None:
        table = self.query_one("#dash-table", DataTable)
        table.add_columns(
            "ID",
            "State",
            "Port",
            "CPU%",
            "CPU▁▂▃",
            "RAM GB",
            "RAM▁▂▃",
            "Size",
        )
        self.call_after_refresh(self._stretch_id_column)

    def on_resize(self) -> None:
        self.call_after_refresh(self._stretch_id_column)

    def _stretch_id_column(self) -> None:
        """Make the ID column fill all remaining horizontal space."""
        try:
            table = self.query_one("#dash-table", DataTable)
            cols = list(table.columns.values())
            group = self.query_one("#dash-table-group")
            avail = group.content_size.width
            if not cols or avail == 0:
                return
            other_w = sum(col.get_render_width(table) for col in cols[1:])
            stretch = max(avail - other_w - 2, 10)
            cols[0].width = stretch
            cols[0].content_width = stretch
            table.refresh(layout=True)
        except Exception:
            pass

    def refresh_data(
        self,
        states: list[ProcessState],
        registry_entries: dict[str, RegistryEntry],
        downloads: list[DownloadState],
    ) -> None:
        """Called by the app's polling timer.

        Args:
            states: list of ProcessState (running/detected processes)
            registry_entries: dict[id, RegistryEntry] from registry
            downloads: list of DownloadState objects
        """
        # ── Summary stats ──────────────────────────────────────────────────
        # key on alias — model_id is None when scan() is called without registry_ids
        running_ids = {s.alias or s.model_id or "" for s in states}
        n_running = len(states)
        n_stopped = sum(1 for e in registry_entries.values() if e.id not in running_ids)
        downloading_ids = {
            d.model_id for d in downloads if getattr(d, "status", "") == "downloading"
        }
        n_downloading = len(downloading_ids)
        n_downloaded = sum(1 for e in registry_entries.values() if e.downloaded)
        n_missing = sum(
            1 for e in registry_entries.values() if not e.downloaded and e.id not in downloading_ids
        )
        with contextlib.suppress(Exception):
            self.query_one("#dash-summary", Static).update(
                f"[green]●[/green] Running:[bold]{n_running}[/bold]"
                f"  [dim]○[/dim] Stopped:[bold]{n_stopped}[/bold]"
                f"  [cyan]↓[/cyan] Downloading:[bold]{n_downloading}[/bold]"
                f"  [green]✓[/green] Downloaded:[bold]{n_downloaded}[/bold]"
                f"  [red]✗[/red] Missing:[bold]{n_missing}[/bold]"
            )

        # ── Instances table ────────────────────────────────────────────────
        table = self.query_one("#dash-table", DataTable)

        # Read the cursor position SYNCHRONOUSLY before table.clear() resets it.
        # Using table.cursor_row directly avoids the async RowHighlighted timing
        # race: clear() and add_row() post RowHighlighted to the message queue;
        # those events are processed after refresh_data() returns and can
        # overwrite a stale key into _selected_row_key before the move_cursor
        # event corrects it.
        prev_cursor = table.cursor_row
        prev_selection = (
            self._display_row_keys[prev_cursor]
            if 0 <= prev_cursor < len(self._display_row_keys)
            else ""
        )

        table.clear()
        self._display_row_keys = []

        # key on alias — model_id is None when scan() is called without registry_ids
        state_map = {(s.alias or s.model_id or ""): s for s in states}

        # Show all registry entries with live data overlay (running first, then stopped)
        all_ids = [eid for eid in registry_entries if eid in state_map] + [
            eid for eid in registry_entries if eid not in state_map
        ]
        # Also include orphan processes not in registry
        for s in states:
            key = s.alias or s.model_id or f"orphan:{s.port}"
            if key not in registry_entries:
                all_ids.append(key)

        seen: set[str] = set()
        for model_id in all_ids:
            if model_id in seen:
                continue
            seen.add(model_id)
            s = state_map.get(model_id)
            entry = registry_entries.get(model_id)

            if s is not None:
                state_style = {
                    "ready": "green",
                    "loading": "yellow",
                    "paused": "blue",
                    "error": "red",
                    "unknown": "magenta",
                }.get(s.state, "dim")
                state_cell = f"[{state_style}]● {s.state}[/{state_style}]"
                port_cell = str(s.port)
                cpu_val = f"{s.cpu_percent:.0f}%"
                cpu_spark = _sparkline(s.cpu_history)
                ram_mb = f"{s.rss_mb / 1024:.2f}"
                ram_spark = _sparkline(s.rss_history)
            else:
                state_cell = "[dim]○ stopped[/dim]"
                port_cell = str(entry.port) if entry else "—"
                cpu_val = "—"
                cpu_spark = " " * 10
                ram_mb = "—"
                ram_spark = " " * 10

            self._display_row_keys.append(model_id)
            size_cell = fmt_size(entry.path) if entry and entry.downloaded else "—"
            table.add_row(
                model_id,
                state_cell,
                port_cell,
                cpu_val,
                cpu_spark,
                ram_mb,
                ram_spark,
                size_cell,
            )

        # Restore cursor to the previously selected row (prevents jump to row 0)
        if prev_selection and prev_selection in self._display_row_keys:
            restore_idx = self._display_row_keys.index(prev_selection)
            table.move_cursor(row=restore_idx)

        self.call_after_refresh(self._stretch_id_column)

        # ── Active downloads ───────────────────────────────────────────────
        # See memory/specs/011-downloads-view-redesign.md — AC-21 (16-char bar, speed, ETA)
        active = [
            d
            for d in downloads
            if getattr(d, "status", "") in ("downloading", "verifying", "queued")
        ]
        dl_widget = self.query_one("#dash-downloads", Static)
        if active:
            lines = []
            for d in active:
                total = getattr(d, "total_bytes", 0) or 0
                done = getattr(d, "downloaded_bytes", 0) or 0
                progress = (done / total) if total > 0 else 0.0
                filled = round(progress * 16)
                filled = max(0, min(16, filled))
                bar = "█" * filled + "░" * (16 - filled)
                speed = getattr(d, "speed_bps", 0.0)
                eta = getattr(d, "eta_seconds", None)
                if speed >= 1_048_576:
                    speed_str = f"{speed / 1_048_576:.1f} MB/s"
                elif speed >= 1024:
                    speed_str = f"{speed / 1024:.0f} KB/s"
                elif speed > 0:
                    speed_str = f"{speed:.0f} B/s"
                else:
                    speed_str = "—"
                if eta is not None and eta >= 0:
                    m, s = divmod(eta, 60)
                    eta_str = f"{m}m {s:02d}s" if m else f"{s}s"
                else:
                    eta_str = "—"
                status = getattr(d, "status", "queued")
                lines.append(
                    f"[cyan]↓[/cyan] {d.model_id}  [cyan]{bar}[/cyan]  {speed_str}  "
                    f"ETA {eta_str}  [{status}]"
                )
            dl_widget.update("\n".join(lines))
        else:
            dl_widget.update("[dim]No active downloads[/dim]")
