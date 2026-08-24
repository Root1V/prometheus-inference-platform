"""Discovery view — HuggingFace model search & one-key download.

Implements: memory/specs/012-discovery-view-redesign.md — AC-1 through AC-16
"""

from __future__ import annotations

import contextlib
import os
import re
from collections.abc import Generator
from datetime import datetime
from pathlib import Path
from typing import Any

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.markup import escape as markup_escape
from textual.widgets import DataTable, Input, Static

# ── Pure helpers (also importable by tests) ───────────────────────────────────

_QUANT_RE = re.compile(
    r"(IQ\d[_A-Z0-9]*|Q\d[_A-Z0-9]*|F16|F32|BF16)",
    re.IGNORECASE,
)
_SLUG_RE = re.compile(r"[^a-z0-9]+")
# Matches HuggingFace multi-part shard naming: <prefix>NNNNN-of-MMMMM.gguf
# e.g. "Q4_0/DeepSeek-V3.2-Q4_0-00001-of-00008.gguf"
_SHARD_RE = re.compile(r"^(.*?-?)(\d{5})-of-(\d{5})(\.gguf)$", re.IGNORECASE)


def _infer_quant(filename: str) -> str:
    """Return quantization tag inferred from a GGUF filename.

    AC-12: covers Q4_K_M, Q8_0, IQ3_M, F16, F32, BF16; returns '?' if unknown.
    """
    m = _QUANT_RE.search(filename)
    return m.group(0).upper() if m else "?"


def _auto_id(filename: str, existing_ids: set[str] | None = None) -> str:
    """Slugify a GGUF filename into a registry-safe ID.

    AC-10: strips .gguf, lowercases, collapses non-alnum to '-', appends '-local'.
    Collision-resolves with -2, -3, … if existing_ids is provided.
    """
    name = re.sub(r"\.gguf$", "", filename, flags=re.IGNORECASE)
    slug = _SLUG_RE.sub("-", name.lower()).strip("-")
    base = (slug + "-local")[:63]
    if not existing_ids:
        return base
    candidate = base
    suffix = 2
    while candidate in existing_ids:
        candidate = f"{base[:59]}-{suffix}"
        suffix += 1
    return candidate


def _next_free_port(used_ports: set[int]) -> int:
    """Return the lowest port >= 8081 not already in use.

    AC-11: callers pass {e.port for e in registry.entries}.
    """
    port = 8081
    while port in used_ports:
        port += 1
    return port


def _shard_filenames(selected: str, all_files: list[str]) -> list[str]:
    """Return the full ordered list of shard files for a multi-part GGUF model.

    Detects the HuggingFace split-model naming pattern:
        <prefix>NNNNN-of-MMMMM.gguf  (e.g. Q4_0/Model-00001-of-00008.gguf)

    Collects all M sibling shards from *all_files* sharing the same prefix and
    total count, sorted by part number.  Returns ``[selected]`` unchanged when
    the filename does not match the pattern (single-file model).
    """
    m = _SHARD_RE.match(selected)
    if not m:
        return [selected]
    prefix, total, ext = m.group(1), m.group(3), m.group(4)
    shards: list[tuple[int, str]] = []
    for f in all_files:
        fm = _SHARD_RE.match(f)
        if (
            fm
            and fm.group(1) == prefix
            and fm.group(3) == total
            and fm.group(4).lower() == ext.lower()
        ):
            shards.append((int(fm.group(2)), f))
    shards.sort()
    return [f for _, f in shards] if shards else [selected]


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


@contextlib.contextmanager
def _ssl_env(ca: Path | None) -> Generator[None, None, None]:
    """Temporarily set REQUESTS_CA_BUNDLE for the duration of an HF API call.

    huggingface_hub uses requests under the hood, which honours this env var.
    Safe to call from a worker thread (restores original value on exit).
    """
    if ca is None:
        yield
        return
    key = "REQUESTS_CA_BUNDLE"
    old = os.environ.get(key)
    os.environ[key] = str(ca)
    try:
        yield
    finally:
        if old is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = old


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

    def on_key(self, event) -> None:  # type: ignore[override]
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
