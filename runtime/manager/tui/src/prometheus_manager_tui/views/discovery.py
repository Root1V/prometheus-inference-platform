"""Discovery view — HuggingFace model search & one-key download.

Implements: memory/specs/012-discovery-view-redesign.md — AC-1 through AC-16
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

# Pure helpers (also importable by tests) live in manager-core's hf_discovery
# module — RM-48 added an HTTP-facing consumer (manager-api) for the exact
# same logic, so the canonical version moved there; re-exported here under
# the original private names so this view's own call sites, app.py's
# discovery-download action, and this package's tests are all unaffected.
# auto_id/next_free_port/shard_filenames aren't called inside this module
# itself (only by app.py, via this re-export) — noqa'd rather than silently
# dropped.
from prometheus_manager_core.hf_discovery import auto_id as _auto_id  # noqa: F401
from prometheus_manager_core.hf_discovery import infer_quant as _infer_quant
from prometheus_manager_core.hf_discovery import next_free_port as _next_free_port  # noqa: F401
from prometheus_manager_core.hf_discovery import shard_filenames as _shard_filenames  # noqa: F401
from prometheus_manager_core.hf_discovery import ssl_env as _ssl_env
from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.events import Key
from textual.markup import escape as markup_escape
from textual.widgets import DataTable, Input, Static

# mypy (strict / no_implicit_reexport) requires re-exported imports to be
# explicitly declared — these four are consumed by app.py via this module.
__all__ = ["DiscoveryView", "_auto_id", "_infer_quant", "_next_free_port", "_shard_filenames"]


def _fmt_count(n: int | None) -> str:
    """Format downloads/likes as 'N', 'NM', or '~NM'."""
    if n is None:
        return "?"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.0f}K"
    return str(n)


def _fmt_date(dt: datetime | None) -> str:
    if dt is None:
        return "?"
    return dt.strftime("%Y-%m-%d")


# ── View ──────────────────────────────────────────────────────────────────────


class DiscoveryView(Vertical):
    """View 5: HuggingFace model search → file browse → one-key download.

    Implements: memory/specs/012-discovery-view-redesign.md
    """

    BINDINGS = [
        Binding("/", "focus_search", "Search", show=True),
        Binding("d", "download", "Download", show=True),
    ]

    DEFAULT_CSS = """
    DiscoveryView {
        padding: 0;
    }
    DiscoveryView #search-group {
        height: auto;
        border: solid $panel;
        border-title-color: $foreground-muted;
        border-title-style: bold;
        padding: 0 1;
    }
    DiscoveryView #search-input {
        border: tall $panel;
        background: $background;
        padding: 0 1;
    }
    DiscoveryView #search-input:focus {
        border: tall $primary;
    }
    DiscoveryView #search-status {
        height: 1;
        color: $foreground-muted;
    }
    DiscoveryView #results-group {
        height: 1fr;
        border: solid $panel;
        border-title-color: $foreground-muted;
        border-title-style: bold;
        padding: 0;
    }
    DiscoveryView #results-group DataTable {
        width: 100%;
        height: 100%;
    }
    DiscoveryView #files-group {
        height: auto;
        max-height: 12;
        border: solid $panel;
        border-title-color: $foreground-muted;
        border-title-style: bold;
        padding: 0;
    }
    DiscoveryView #files-group DataTable {
        width: 100%;
        height: auto;
    }
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._results: list[Any] = []  # list[ModelInfo]
        self._files: list[str] = []  # GGUF filenames for selected repo
        self._selected_repo: str = ""
        self._selected_file: str = ""
        self._fetching_repo: str = ""  # debounce: repo currently in-flight

    def compose(self) -> ComposeResult:
        with Vertical(id="search-group") as grp:
            grp.border_title = "Search — HuggingFace GGUF Models"
            yield Input(
                placeholder="Search HuggingFace for GGUF models…",
                id="search-input",
            )
            yield Static(
                "[dim]Type and press [bold]Enter[/bold] to search • "
                "[bold]Esc[/bold] to navigate results[/dim]",
                id="search-status",
            )
        with Vertical(id="results-group") as grp:
            grp.border_title = "Results"
            yield DataTable(id="results-table")
        with Vertical(id="files-group") as grp:
            grp.border_title = "Files"
            yield DataTable(id="files-table")

    def on_mount(self) -> None:
        rt = self.query_one("#results-table", DataTable)
        rt.cursor_type = "row"
        rt.add_columns("Repo", "↓Downloads", "★Likes", "Updated")

        ft = self.query_one("#files-table", DataTable)
        ft.cursor_type = "row"
        ft.add_columns("Filename", "Quant")

    # ── Input events ─────────────────────────────────────────────────────────

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "search-input":
            self.action_search()

    def on_key(self, event: Key) -> None:
        """Escape while the search input is focused → move to results table."""
        if event.key == "escape":
            inp = self.query_one("#search-input", Input)
            if inp == self.app.focused:
                self.query_one("#results-table", DataTable).focus()
                event.stop()

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        table = event.data_table
        if table.id == "results-table":
            row = event.cursor_row
            if 0 <= row < len(self._results):
                repo_id = self._results[row].id
                if repo_id != self._fetching_repo:
                    self._selected_repo = repo_id
                    self._selected_file = ""
                    self._fetch_files(repo_id)
        elif table.id == "files-table":
            row = event.cursor_row
            if 0 <= row < len(self._files):
                self._selected_file = self._files[row]

    # ── Search ───────────────────────────────────────────────────────────────

    def action_focus_search(self) -> None:
        """Focus the search input so the user can type a query — [/]."""
        self.query_one("#search-input", Input).focus()

    def action_search(self) -> None:
        query = self.query_one("#search-input", Input).value.strip()
        if not query:
            self.query_one("#search-status", Static).update(
                "[yellow]Enter a model name and press Enter to search[/yellow]"
            )
            return
        self.query_one("#search-status", Static).update("[yellow]Searching…[/yellow]")
        config = getattr(self.app, "_config", None)
        token = getattr(config, "hf_token", None)
        ca = getattr(config, "resolved_ca_bundle", None)
        self._worker_search(query, token, ca)

    @work(thread=True)
    def _worker_search(self, query: str, token: str | None, ca: Path | None) -> None:
        try:
            from huggingface_hub import list_models

            with _ssl_env(ca):
                results = list(list_models(filter="gguf", search=query, limit=30, token=token))
        except Exception as exc:
            self.app.call_from_thread(self._on_search_error, str(exc))
            return
        self.app.call_from_thread(self._on_search_results, results)

    def _on_search_results(self, results: list[Any]) -> None:
        self._results = results
        table = self.query_one("#results-table", DataTable)
        table.clear()
        for m in results:
            table.add_row(
                m.id,
                _fmt_count(getattr(m, "downloads", None)),
                _fmt_count(getattr(m, "likes", None)),
                _fmt_date(getattr(m, "lastModified", None)),
            )
        n = len(results)
        self.query_one("#search-status", Static).update(
            f"[green]{n} result{'s' if n != 1 else ''}[/green]" if n else "[dim]No results[/dim]"
        )

    def _on_search_error(self, msg: str) -> None:
        safe = markup_escape(msg[:120])
        self.query_one("#search-status", Static).update(f"[red]Search failed: {safe}[/red]")
        self.query_one("#results-table", DataTable).clear()

    # ── File fetch ───────────────────────────────────────────────────────────

    def _fetch_files(self, repo_id: str) -> None:
        self._fetching_repo = repo_id
        grp = self.query_one("#files-group", Vertical)
        grp.border_title = "Files — fetching…"
        self.query_one("#files-table", DataTable).clear()
        self._files = []
        config = getattr(self.app, "_config", None)
        token = getattr(config, "hf_token", None)
        ca = getattr(config, "resolved_ca_bundle", None)
        self._worker_fetch_files(repo_id, token, ca)

    @work(thread=True)
    def _worker_fetch_files(self, repo_id: str, token: str | None, ca: Path | None) -> None:
        try:
            from huggingface_hub import list_repo_files

            with _ssl_env(ca):
                all_files = list(list_repo_files(repo_id, token=token))
            gguf_files = [f for f in all_files if f.lower().endswith(".gguf")]
        except Exception as exc:
            self.app.call_from_thread(self._on_files_error, repo_id, str(exc))
            return
        self.app.call_from_thread(self._on_files_results, repo_id, gguf_files)

    def _on_files_results(self, repo_id: str, files: list[str]) -> None:
        self._fetching_repo = ""
        self._files = files
        grp = self.query_one("#files-group", Vertical)
        grp.border_title = f"Files — {repo_id}  [d] Download"
        table = self.query_one("#files-table", DataTable)
        table.clear()
        for f in files:
            table.add_row(f, _infer_quant(f))

        # Update GGUF count in results table
        self._update_results_gguf_count(repo_id, len(files))

    def _on_files_error(self, repo_id: str, msg: str) -> None:
        self._fetching_repo = ""
        grp = self.query_one("#files-group", Vertical)
        safe = markup_escape(msg[:60])
        grp.border_title = f"Files — error: {safe}"

    def _update_results_gguf_count(self, repo_id: str, count: int) -> None:
        """No-op: results table has no GGUF count column in the simplified layout."""

    # ── Download action ───────────────────────────────────────────────────────

    def action_download(self) -> None:
        """Delegate to app — AC-9."""
        self.app.action_discovery_download()  # type: ignore[attr-defined]
