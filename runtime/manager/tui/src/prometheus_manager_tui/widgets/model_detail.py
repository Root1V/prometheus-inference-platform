"""Model detail panel — identity, context & limits, runtime metrics.

Implements: memory/specs/008-llama-server-manager.md — AC-22d (Model Detail panel)
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.widget import Widget
from textual.widgets import Label, Static


class ModelDetail(Widget):
    """Right-side panel showing registry + runtime detail for a selected instance.

    Implements: memory/specs/008-llama-server-manager.md — AC-22d
    """

    DEFAULT_CSS = """
    ModelDetail {
        width: 1fr;
        height: 100%;
        border: round $primary;
        padding: 1;
        overflow-y: auto;
    }
    ModelDetail .section-title {
        text-style: bold underline;
        margin-top: 1;
    }
    ModelDetail .kv-row {
        layout: horizontal;
        height: 1;
    }
    ModelDetail .kv-key {
        width: 18;
        color: $text-muted;
    }
    """

    def compose(self) -> ComposeResult:
        yield Label("(no instance selected)", id="detail-placeholder")

    def show(
        self,
        *,
        # Identity
        model_id: str,
        family: str = "",
        quantization: str = "",
        path: str = "",
        size_mb: float | None = None,
        hf_repo: str = "",
        sha256: str = "",
        # Context
        context_length: int = 0,
        # Runtime
        state: str = "",
        pid: int | None = None,
        uptime_s: float = 0.0,
        cpu_pct: float = 0.0,
        rss_mb: float = 0.0,
        port: int = 0,
    ) -> None:
        self.remove_children()
        rows: list[Widget] = []

        def _row(key: str, value: str) -> Widget:
            w = Widget(classes="kv-row")
            _ = w._nodes  # lazy construct
            return w

        def _section(title: str) -> Label:
            return Label(title, classes="section-title")

        def _kv(key: str, value: str) -> Static:
            return Static(f"[dim]{key:<18}[/dim]{value}")

        rows.append(_section("Identity"))
        rows.append(_kv("ID", model_id))
        rows.append(_kv("Family", family or "—"))
        rows.append(_kv("Quantization", quantization or "—"))
        rows.append(_kv("GGUF path", path or "—"))
        if size_mb is not None:
            rows.append(_kv("Size", f"{size_mb:.0f} MB"))
        rows.append(_kv("HF repo", hf_repo or "—"))
        if sha256:
            rows.append(_kv("SHA-256", sha256[:16] + "…"))

        rows.append(_section("Context & Limits"))
        rows.append(_kv("n_ctx", str(context_length)))
        rows.append(_kv("Port", str(port)))

        rows.append(_section("Runtime Metrics"))
        rows.append(_kv("State", state))
        rows.append(_kv("PID", str(pid) if pid else "—"))
        uptime_str = _format_uptime(uptime_s) if uptime_s else "—"
        rows.append(_kv("Uptime", uptime_str))
        rows.append(_kv("CPU %", f"{cpu_pct:.1f}"))
        rows.append(_kv("RSS MB", f"{rss_mb:.0f}"))

        self.mount(*rows)

    def clear(self) -> None:
        self.remove_children()
        self.mount(Label("(no instance selected)", id="detail-placeholder"))


def _format_uptime(seconds: float) -> str:
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    if h:
        return f"{h}h {m}m {sec}s"
    if m:
        return f"{m}m {sec}s"
    return f"{sec}s"
