"""Registry view — catalog table with detail panel and discovery toggle.

Implements: memory/specs/010-registry-view-redesign.md — AC-3–AC-17
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from prometheus_manager_core.registry import RegistryEntry
from rich.markup import escape as _esc
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import DataTable, Static

from ..utils import fmt_size

if TYPE_CHECKING:
    from ..app import ManagerApp


def _fmt_ctx(context_length: int) -> str:
    """Format context length as e.g. '8K', '32K'. See: memory/specs/010 AC-11."""
    k = context_length // 1024
    return f"{k}K" if k > 0 else str(context_length)


def _fmt_ram(rss_mb: int | None) -> str:
    """Format rss_estimate_mb as GB string. See: memory/specs/010 AC-12, AC-13."""
    if rss_mb is None:
        return "—"
    return f"{rss_mb / 1024:.1f} GB"


def _fmt_source(entry: RegistryEntry) -> str:
    """Basename of path or hf:repo/file. See: memory/specs/010 AC-14, AC-15."""
    if entry.path:
        return Path(entry.path).name
    if entry.hf_repo:
        return f"hf:{entry.hf_repo}/{entry.hf_filename}"
    return "—"


class RegistryView(Vertical):
    """View 3: model catalog — one row per registry.yaml entry.

    Columns: ID · Family · Quant · Ctx · Est.RAM · Dl · Discovery · Source · Size
    Downloaded entries first (α-sorted), not-downloaded entries after separator.
    Implements: memory/specs/010-registry-view-redesign.md
    """

    BINDINGS = [
        Binding("a", "add", "Add", show=True),
        Binding("x", "delete", "Delete", show=True),
        Binding("w", "download", "Download", show=True),
        Binding("v", "toggle_discovery", "Discovery", show=True),
        Binding("enter", "toggle_detail", "Detail", show=True, priority=True),
    ]

    DEFAULT_CSS = """
    RegistryView {
        padding: 0;
    }
    RegistryView #reg-table-group {
        height: 1fr;
        border: solid $panel;
        border-title-color: $foreground-muted;
        border-title-style: bold;
        padding: 0;
    }
    RegistryView #reg-table-group DataTable {
        width: 100%;
        height: 100%;
    }
    RegistryView #reg-detail-group {
        height: auto;
        border: solid $panel;
        border-title-color: $foreground-muted;
        border-title-style: bold;
        padding: 0;
    }
    RegistryView #reg-detail-row1 {
        height: auto;
    }
    RegistryView #reg-identity-group {
        width: 2fr;
        height: auto;
        border: solid $panel;
        border-title-color: $foreground-muted;
        padding: 0 1;
    }
    RegistryView #reg-acquisition-group {
        width: 1fr;
        height: auto;
        border: solid $panel;
        border-title-color: $foreground-muted;
        padding: 0 1;
    }
    RegistryView #reg-spec-group {
        height: auto;
        border: solid $panel;
        border-title-color: $foreground-muted;
        padding: 0 1;
    }
    RegistryView #reg-deploy-group {
        height: auto;
        border: solid $panel;
        border-title-color: $foreground-muted;
        padding: 0 1;
    }
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._entries: list[RegistryEntry] = []
        # Row key → entry id mapping (includes separator row key "_sep_" → None)
        self._row_ids: list[str | None] = []
        self._detail_visible: bool = True  # visible by default

    def compose(self) -> ComposeResult:
        with Vertical(id="reg-table-group") as grp:
            grp.border_title = "Registry  [a]dd [x]delete [w]download [v]discovery [↵]detail"
            yield DataTable(id="registry-table")
        with Vertical(id="reg-detail-group") as grp:
            grp.border_title = "Detail"
            with Horizontal(id="reg-detail-row1"):
                with Vertical(id="reg-identity-group") as ig:
                    ig.border_title = "Identity"
                    yield Static("", id="reg-detail-identity")
                with Vertical(id="reg-acquisition-group") as ag:
                    ag.border_title = "Acquisition"
                    yield Static("", id="reg-detail-acquisition")
            with Vertical(id="reg-spec-group") as sg:
                sg.border_title = "Model Spec"
                yield Static("", id="reg-detail-spec")
            with Vertical(id="reg-deploy-group") as dg:
                dg.border_title = "Deployment"
                yield Static("", id="reg-detail-deploy")

    def on_mount(self) -> None:
        table = self.query_one("#registry-table", DataTable)
        table.cursor_type = "row"
        table.add_columns(
            "ID", "Family", "Quant", "Ctx", "Est.RAM", "Dl", "Discovery", "Source", "Size"
        )

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        idx = event.cursor_row
        if 0 <= idx < len(self._row_ids):
            model_id = self._row_ids[idx]
            if model_id is None:
                return  # separator row — ignore
            entry = next((e for e in self._entries if e.id == model_id), None)
            if entry and self._detail_visible:
                self._update_detail(entry)

    def refresh_data(self, entries: list[RegistryEntry]) -> None:
        # AC-3: one row per entry regardless of running state
        self._entries = entries
        table = self.query_one("#registry-table", DataTable)

        # Remember cursor position by entry id
        cursor_row = table.cursor_row
        prev_id = self._row_ids[cursor_row] if 0 <= cursor_row < len(self._row_ids) else None

        table.clear()
        self._row_ids = []

        # AC-4: downloaded first (α-sorted), not-downloaded after separator
        downloaded = sorted([e for e in entries if e.downloaded], key=lambda e: e.id)
        not_downloaded = sorted([e for e in entries if not e.downloaded], key=lambda e: e.id)

        for e in downloaded:
            self._add_entry_row(table, e)

        if not_downloaded:
            # Separator row
            table.add_row(
                "[dim]── not downloaded ──[/dim]",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                key="_sep_",
            )
            self._row_ids.append(None)
            for e in not_downloaded:
                self._add_entry_row(table, e, dim=True)

        # Restore cursor
        if prev_id:
            try:
                new_idx = next(i for i, rid in enumerate(self._row_ids) if rid == prev_id)
                table.move_cursor(row=new_idx)
            except StopIteration:
                pass

        # Refresh detail panel for the current row
        if self._detail_visible:
            cursor = table.cursor_row
            model_id = self._row_ids[cursor] if 0 <= cursor < len(self._row_ids) else None
            if model_id:
                entry = next((e for e in self._entries if e.id == model_id), None)
                if entry:
                    self._update_detail(entry)

    def _add_entry_row(self, table: DataTable[Any], e: RegistryEntry, *, dim: bool = False) -> None:
        dl = "✓" if e.downloaded else "✗"
        disc = "[green]● ON[/green]" if e.discovery else "[dim]○ OFF[/dim]"
        ctx = _fmt_ctx(e.context_length)
        ram = _fmt_ram(e.rss_estimate_mb)
        src = _fmt_source(e)
        size = fmt_size(e.path) if e.downloaded else "—"
        if dim:
            row = (
                f"[dim]{e.id}[/dim]",
                f"[dim]{e.family}[/dim]",
                f"[dim]{e.quantization}[/dim]",
                f"[dim]{ctx}[/dim]",
                f"[dim]{ram}[/dim]",
                f"[dim]{dl}[/dim]",
                disc,  # colour preserved even for not-downloaded rows
                f"[dim]{src}[/dim]",
                f"[dim]{size}[/dim]",
            )
        else:
            row = (e.id, e.family, e.quantization, ctx, ram, dl, disc, src, size)
        table.add_row(*row, key=e.id)
        self._row_ids.append(e.id)

    # ── Detail panel ─────────────────────────────────────────────────────────

    def _update_detail(self, entry: RegistryEntry) -> None:
        """Populate the four detail sub-panels from the selected registry entry."""
        grp = self.query_one("#reg-detail-group")
        grp.border_title = f"Detail — {entry.id}"

        # Identity — escape values to prevent Rich markup injection (spec 010 security)
        fname = Path(entry.path).name if entry.path else "—"
        if len(fname) > 60:
            fname = fname[:57] + "…"
        full_path = _esc(entry.path) if entry.path else "—"
        fname = _esc(fname)
        identity_lines = [
            f"[dim]Registry ID  [/dim]  {_esc(entry.id)}",
            f"[dim]Family       [/dim]  {_esc(entry.family) or '—'}",
            f"[dim]Quantization [/dim]  {_esc(entry.quantization) or '—'}",
            f"[dim]GGUF file    [/dim]  {fname}",
            f"[dim]Full path    [/dim]  {full_path}",
        ]
        self.query_one("#reg-detail-identity", Static).update("\n".join(identity_lines))

        # Acquisition — escape values to prevent Rich markup injection
        sha = "—"
        if entry.hf_sha256:
            sha = _esc(entry.hf_sha256[:14]) + "… ✓"
        file_size = fmt_size(entry.path) if entry.downloaded else "—"
        acq_lines = [
            f"[dim]HF repo      [/dim]  {_esc(entry.hf_repo) if entry.hf_repo else '—'}",
            f"[dim]HF filename  [/dim]  {_esc(entry.hf_filename) if entry.hf_filename else '—'}",
            f"[dim]SHA-256      [/dim]  {sha}",
            f"[dim]Downloaded   [/dim]  {'✓' if entry.downloaded else '✗'}",
            f"[dim]File size    [/dim]  {file_size}",
        ]
        self.query_one("#reg-detail-acquisition", Static).update("\n".join(acq_lines))

        # Model Spec
        spec_text = (
            f"[dim]Context window[/dim]  {entry.context_length:,} tokens    "
            f"[dim]Est. RAM[/dim]  {_fmt_ram(entry.rss_estimate_mb)}    "
            f"[dim]Log level[/dim]  {entry.log_level or 'info'}"
        )
        self.query_one("#reg-detail-spec", Static).update(spec_text)

        # Deployment
        disc_str = "[green]● ON[/green]" if entry.discovery else "[dim]○ OFF[/dim]"
        deploy_text = (
            f"[dim]Port[/dim]  {entry.port}    "
            f"[dim]Backend URL[/dim]  {entry.backend_url or f'http://127.0.0.1:{entry.port}'}    "
            f"[dim]Discovery[/dim]  {disc_str}"
        )
        self.query_one("#reg-detail-deploy", Static).update(deploy_text)

    # ── Actions ──────────────────────────────────────────────────────────────

    def action_toggle_detail(self) -> None:
        """Expand/collapse detail panel. See: memory/specs/010 AC-16, AC-17."""
        self._detail_visible = not self._detail_visible
        grp = self.query_one("#reg-detail-group")
        grp.display = self._detail_visible
        if self._detail_visible:
            table = self.query_one("#registry-table", DataTable)
            idx = table.cursor_row
            if 0 <= idx < len(self._row_ids):
                model_id = self._row_ids[idx]
                if model_id:
                    entry = next((e for e in self._entries if e.id == model_id), None)
                    if entry:
                        self._update_detail(entry)

    def action_toggle_discovery(self) -> None:
        """Toggle discovery flag for selected row. See: memory/specs/010 AC-7."""
        table = self.query_one("#registry-table", DataTable)
        idx = table.cursor_row
        if not (0 <= idx < len(self._row_ids)):
            return
        model_id = self._row_ids[idx]
        if not model_id:
            return
        cast("ManagerApp", self.app).action_registry_toggle_discovery(model_id)

    def action_add(self) -> None:
        cast("ManagerApp", self.app).action_registry_add()

    def action_delete(self) -> None:
        cast("ManagerApp", self.app).action_registry_delete()

    def action_download(self) -> None:
        cast("ManagerApp", self.app).action_registry_download()
