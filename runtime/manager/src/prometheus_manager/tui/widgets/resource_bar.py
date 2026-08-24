"""Resource bar widget — single-line HOST CPU │ RAM │ GPU bar.

Spec design (AC-22b / AC-22c):
  HOST  CPU ████████░░  78%  │  RAM 9.2/16 GB ████████░░  57%  │  GPU ███░░░  34% 4/8GB

Colour thresholds: <70% green · 70–85% yellow · >85% red.
Implements: memory/specs/008-llama-server-manager.md — AC-22b, AC-22c
"""

from __future__ import annotations

import psutil
from textual.widget import Widget
from textual.widgets import Static


def _bar(pct: float, width: int = 18) -> str:
    """Render a Unicode block-fill bar of given character width."""
    filled = round(pct / 100 * width)
    return "█" * filled + "░" * (width - filled)


def _colour(pct: float) -> str:
    if pct < 70:
        return "green"
    elif pct < 85:
        return "yellow"
    return "red"


class ResourceBar(Widget):
    """Single-row host-resource bar: CPU │ RAM │ GPU (optional).

    Implements: memory/specs/008-llama-server-manager.md — AC-22b, AC-22c
    """

    DEFAULT_CSS = """
    ResourceBar {
        height: 1;
        padding: 0 1;
        background: $surface;
    }
    """

    def compose(self):
        yield Static("", id="resource-line")

    def on_mount(self) -> None:
        # Render once immediately with live data
        self._do_update(
            psutil.cpu_percent(),
            psutil.virtual_memory().percent,
            None,
        )

    def update(
        self,
        cpu_pct: float,
        ram_pct: float,
        gpu_pct: float | None = None,
        ram_used_gb: float | None = None,
        ram_total_gb: float | None = None,
        running: int = 0,
        ready: int = 0,
        errors: int = 0,
    ) -> None:
        self._do_update(
            cpu_pct, ram_pct, gpu_pct, ram_used_gb, ram_total_gb, running, ready, errors
        )

    def _do_update(
        self,
        cpu_pct: float,
        ram_pct: float,
        gpu_pct: float | None,
        ram_used_gb: float | None = None,
        ram_total_gb: float | None = None,
        running: int = 0,
        ready: int = 0,
        errors: int = 0,
    ) -> None:
        try:
            cpu_c = _colour(cpu_pct)
            cpu_seg = (
                f"[bold]CPU[/bold] [{cpu_c}]{_bar(cpu_pct)}[/{cpu_c}] "
                f"[{cpu_c}]{cpu_pct:.0f}%[/{cpu_c}]"
            )

            ram_c = _colour(ram_pct)
            if ram_used_gb is not None and ram_total_gb is not None:
                ram_label = f"{ram_used_gb:.1f}/{ram_total_gb:.0f} GB"
            else:
                mem = psutil.virtual_memory()
                ram_label = f"{(mem.total - mem.available) / 2**30:.1f}/{mem.total / 2**30:.0f} GB"
            ram_seg = (
                f"[bold]RAM[/bold] [{ram_c}]{_bar(ram_pct)}[/{ram_c}] "
                f"[{ram_c}]{ram_label} {ram_pct:.0f}%[/{ram_c}]"
            )

            sep = "  [dim]│[/dim]  "

            stats_seg = (
                f"[green]●[/green] Run:[bold]{running}[/bold]"
                f"  [dim]○[/dim] Rdy:[bold]{ready}[/bold]"
                f"  [red]✗[/red] Err:[bold]{errors}[/bold]"
            )

            if gpu_pct is not None:
                gpu_c = _colour(gpu_pct)
                gpu_seg = (
                    f"[bold]GPU[/bold] [{gpu_c}]{_bar(gpu_pct)}[/{gpu_c}] "
                    f"[{gpu_c}]{gpu_pct:.0f}%[/{gpu_c}]"
                )
                line = f"[dim]HOST[/dim]  {cpu_seg}{sep}{ram_seg}{sep}{gpu_seg}{sep}{stats_seg}"
            else:
                line = f"[dim]HOST[/dim]  {cpu_seg}{sep}{ram_seg}{sep}{stats_seg}"

            self.query_one("#resource-line", Static).update(line)
        except Exception:
            pass
