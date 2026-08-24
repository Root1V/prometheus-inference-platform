"""Downloads view — active download queue with real-time progress.

Implements: memory/specs/008-llama-server-manager.md — AC-22f (View 4: Downloads)
Implements: memory/specs/011-downloads-view-redesign.md — AC-12–AC-20
"""

from __future__ import annotations

from typing import Any

from rich.markup import escape as _esc
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.widgets import DataTable, Static

_BAR_WIDTH = 16
_HISTORY_STATUSES = {"done", "failed", "cancelled"}
_ACTIVE_STATUSES = {"queued", "downloading", "verifying"}


def _progress_bar(progress: float) -> str:
    """Return a 16-char block progress bar. See memory/specs/011 — AC-12."""
    filled = round(progress * _BAR_WIDTH)
    filled = max(0, min(_BAR_WIDTH, filled))
    return "█" * filled + "░" * (_BAR_WIDTH - filled)


def _fmt_speed(bps: float) -> str:
    """Format bytes/s as human-readable. See memory/specs/011 — AC-13."""
    if bps <= 0:
        return "—"
    if bps >= 1_048_576:
        return f"{bps / 1_048_576:.1f} MB/s"
    if bps >= 1024:
        return f"{bps / 1024:.0f} KB/s"
    return f"{bps:.0f} B/s"


def _fmt_eta(eta: int | None) -> str:
    """Format ETA in Xm Ys. See memory/specs/011 — AC-14."""
    if eta is None or eta < 0:
        return "—"
    m, s = divmod(eta, 60)
    return f"{m}m {s:02d}s" if m else f"{s}s"


def _fmt_size(total: int, done: int) -> str:
    """Format downloaded/total in human-readable GB/MB."""
    if total <= 0:
        return "—"
    g = 1_073_741_824
    m = 1_048_576
    if total >= g:
        return f"{done / g:.1f}/{total / g:.1f} GB"
    return f"{done / m:.0f}/{total / m:.0f} MB"


class DownloadsView(Vertical):
    """View 4: active and completed download queue.

    Implements: memory/specs/008-llama-server-manager.md — AC-22f
    Implements: memory/specs/011-downloads-view-redesign.md — AC-12–AC-20
    """

    BINDINGS = [
        Binding("c", "cancel_download", "Cancel", show=True),
        Binding("r", "retry_download", "Retry", show=True),
        Binding("x", "clear_history", "Clear history", show=True),
        Binding("enter", "toggle_detail", "Detail", show=True, priority=True),
    ]

    DEFAULT_CSS = """
    DownloadsView {
        padding: 0 1;
    }
    DownloadsView #dl-table-group {
        height: 1fr;
        border: solid $panel;
        border-title-color: $foreground-muted;
        border-title-style: bold;
    }
    DownloadsView DataTable {
        width: 100%;
        height: 100%;
    }
    DownloadsView #dl-detail-group {
        height: auto;
        max-height: 12;
        border: solid $panel;
        border-title-color: $foreground-muted;
        border-title-style: bold;
        padding: 0 1;
    }
    DownloadsView #dl-detail {
        height: auto;
        color: $foreground-muted;
    }
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._downloads: list[object] = []
        self._detail_visible: bool = True
        # track row order for cursor restore + key actions
        self._row_keys: list[int] = []  # index into self._downloads

    def compose(self) -> ComposeResult:
        with Vertical(id="dl-table-group") as grp:
            grp.border_title = "Downloads"
            yield DataTable(id="dl-table")
        with Vertical(id="dl-detail-group") as grp:
            grp.border_title = "Detail"
            yield Static("[dim]No download selected[/dim]", id="dl-detail")

    def on_mount(self) -> None:
        table = self.query_one("#dl-table", DataTable)
        table.add_columns("ID", "Progress", "Speed", "ETA", "Size", "Status")

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        self._update_detail()

    def refresh_data(self, downloads: list[object]) -> None:
        self._downloads = downloads
        table = self.query_one("#dl-table", DataTable)

        prev_cursor = table.cursor_row
        prev_key = self._row_keys[prev_cursor] if 0 <= prev_cursor < len(self._row_keys) else -1

        table.clear()
        self._row_keys = []

        # Active/queued first, then separator, then history — AC-15
        active = [
            i for i, d in enumerate(downloads) if getattr(d, "status", None) in _ACTIVE_STATUSES
        ]
        history = [
            i for i, d in enumerate(downloads) if getattr(d, "status", None) in _HISTORY_STATUSES
        ]

        for idx in active:
            self._add_row(table, downloads[idx], idx)

        if active and history:
            table.add_row(
                "[dim]─── History ───[/dim]",
                "",
                "",
                "",
                "",
                "",
                key="__sep__",
            )
            self._row_keys.append(-1)  # sentinel for separator

        for idx in history:
            self._add_row(table, downloads[idx], idx)

        # Restore cursor
        if prev_key >= 0 and prev_key in self._row_keys:
            restore = self._row_keys.index(prev_key)
            table.move_cursor(row=restore)

        self._update_detail()

    def _add_row(self, table: DataTable[Any], dl: object, idx: int) -> None:
        status = getattr(dl, "status", "queued")
        progress = getattr(dl, "progress", 0.0)
        speed = getattr(dl, "speed_bps", 0.0)
        eta = getattr(dl, "eta_seconds", None)
        total = getattr(dl, "total_bytes", 0)
        done = getattr(dl, "downloaded_bytes", 0)
        err = getattr(dl, "error", None)

        if status in _ACTIVE_STATUSES:
            bar = f"[cyan]{_progress_bar(progress)}[/cyan]"
        elif status == "done":
            bar = f"[green]{_progress_bar(1.0)}[/green]"
        elif status == "failed":
            bar = f"[red]✗ {_esc(err or 'failed')[:30]}[/red]"
        elif status == "cancelled":
            bar = "[dim]─── cancelled ────[/dim]"
        else:
            bar = "[dim]" + "░" * _BAR_WIDTH + "[/dim]"

        status_styles = {
            "done": "green",
            "failed": "red",
            "downloading": "yellow",
            "verifying": "yellow",
            "cancelled": "dim",
            "queued": "dim",
        }
        st = status_styles.get(status, "")
        styled_status = f"[{st}]{status}[/{st}]" if st else status

        table.add_row(
            _esc(dl.model_id),  # type: ignore[attr-defined]
            bar,
            _fmt_speed(speed) if status in _ACTIVE_STATUSES else "—",
            _fmt_eta(eta) if status in _ACTIVE_STATUSES else "—",
            _fmt_size(total, done),
            styled_status,
            key=str(idx),
        )
        self._row_keys.append(idx)

    def _update_detail(self) -> None:
        try:
            detail = self.query_one("#dl-detail", Static)
            if not self._detail_visible:
                return
            table = self.query_one("#dl-table", DataTable)
            row = table.cursor_row
            if not (0 <= row < len(self._row_keys)):
                detail.update("[dim]No download selected[/dim]")
                return
            idx = self._row_keys[row]
            if idx < 0 or idx >= len(self._downloads):
                detail.update("[dim]No download selected[/dim]")
                return
            dl = self._downloads[idx]

            started = getattr(dl, "started_at", None)
            started_str = started.astimezone().strftime("%H:%M:%S") if started else "—"
            dest = getattr(dl, "destination", None)
            dest_str = _esc(str(dest)) if dest else "—"
            err = getattr(dl, "error", None)
            err_str = _esc(err) if err else "—"
            total = getattr(dl, "total_bytes", 0)
            size_str = _fmt_size(total, total) if total else "—"
            sha = getattr(dl, "status", "") == "done" and getattr(dl, "expected_sha256", None)

            lines = [
                f"[bold]HF Repo[/bold]      {_esc(getattr(dl, 'hf_repo', '—'))}",
                f"[bold]Filename[/bold]     {_esc(getattr(dl, 'hf_filename', '—'))}",
                f"[bold]Destination[/bold]  {dest_str}",
                f"[bold]Started[/bold]      {started_str}",
                f"[bold]File size[/bold]    {size_str}",
                f"[bold]SHA-256[/bold]      {'verified' if sha else '—'}",
                f"[bold]Error[/bold]        {err_str}",
            ]
            detail.update("\n".join(lines))
        except Exception:
            pass

    def action_toggle_detail(self) -> None:
        """Toggle the detail panel. See memory/specs/011 — AC-17."""
        self._detail_visible = not self._detail_visible
        try:
            grp = self.query_one("#dl-detail-group")
            grp.display = self._detail_visible
            if self._detail_visible:
                self._update_detail()
        except Exception:
            pass

    def action_cancel_download(self) -> None:
        """Cancel the selected active download. See memory/specs/011 — AC-18."""
        dl = self._selected_download()
        if dl is None:
            return
        status = getattr(dl, "status", "")
        if status in _ACTIVE_STATUSES:
            dl.cancel_requested = True  # type: ignore[attr-defined]
            self.app.notify(f"Cancelling {dl.model_id}…", severity="warning")  # type: ignore[attr-defined]

    def action_retry_download(self) -> None:
        """Retry the selected failed/cancelled download. See memory/specs/011 — AC-19."""
        dl = self._selected_download()
        if dl is None:
            return
        status = getattr(dl, "status", "")
        if status in {"failed", "cancelled"}:
            self.app.action_downloads_retry(dl.model_id)  # type: ignore[attr-defined]

    def action_clear_history(self) -> None:
        """Clear all history entries. See memory/specs/011 — AC-20."""
        self.app.action_downloads_clear_history()  # type: ignore[attr-defined]

    def _selected_download(self) -> object | None:
        try:
            table = self.query_one("#dl-table", DataTable)
            row = table.cursor_row
            if 0 <= row < len(self._row_keys):
                idx = self._row_keys[row]
                if 0 <= idx < len(self._downloads):
                    return self._downloads[idx]
        except Exception:
            pass
        return None
