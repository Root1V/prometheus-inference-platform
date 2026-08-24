"""Capacity estimation and warning thresholds.

Implements: memory/specs/008-llama-server-manager.md — AC-24, AC-25
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import psutil

_YELLOW_THRESHOLD = 0.85  # 85% — soft warning
_RED_THRESHOLD = 0.95  # 95% — hard block
_HEURISTIC_MULTIPLIER = 1.2  # file size × 1.2 when rss_estimate_mb absent


@dataclass
class CapacityWarning:
    """Result of a pre-launch capacity check.

    Implements: memory/specs/008-llama-server-manager.md — AC-24, AC-25
    """

    level: Literal["ok", "warning", "blocked"]
    current_rss_mb: float
    estimated_new_rss_mb: float
    total_ram_mb: float
    projected_pct: float
    message: str


def check_capacity(
    path: Path | str | None,
    rss_estimate_mb: int | None,
    current_rss_mb: float = 0.0,
) -> CapacityWarning:
    """Estimate RAM after adding a model and return a warning level.

    Implements: memory/specs/008-llama-server-manager.md — AC-24, AC-25
    """
    total_mb = psutil.virtual_memory().total / (1024 * 1024)

    if rss_estimate_mb is not None:
        new_rss = float(rss_estimate_mb)
    else:
        new_rss = _heuristic_estimate(path)

    projected_mb = current_rss_mb + new_rss
    pct = projected_mb / total_mb if total_mb > 0 else 0.0

    if pct > _RED_THRESHOLD:
        msg = (
            f"Starting this model would require {pct * 100:.0f}% RAM "
            f"({projected_mb:.0f} / {total_mb:.0f} MB). "
            "Host is likely to freeze or OOM. Cannot proceed safely."
        )
        return CapacityWarning(
            level="blocked",
            current_rss_mb=current_rss_mb,
            estimated_new_rss_mb=new_rss,
            total_ram_mb=total_mb,
            projected_pct=pct * 100,
            message=msg,
        )

    if pct > _YELLOW_THRESHOLD:
        msg = (
            f"Starting this model will bring RAM usage to {pct * 100:.0f}% "
            f"({projected_mb:.0f} / {total_mb:.0f} MB). "
            "This may cause system instability."
        )
        return CapacityWarning(
            level="warning",
            current_rss_mb=current_rss_mb,
            estimated_new_rss_mb=new_rss,
            total_ram_mb=total_mb,
            projected_pct=pct * 100,
            message=msg,
        )

    return CapacityWarning(
        level="ok",
        current_rss_mb=current_rss_mb,
        estimated_new_rss_mb=new_rss,
        total_ram_mb=total_mb,
        projected_pct=pct * 100,
        message="OK to start.",
    )


def _heuristic_estimate(path: Path | str | None) -> float:
    """Return estimated RAM in MiB from GGUF file size × 1.2."""
    if not path:
        return 0.0
    try:
        size_bytes = Path(path).stat().st_size
        return (size_bytes / (1024 * 1024)) * _HEURISTIC_MULTIPLIER
    except OSError:
        return 0.0
