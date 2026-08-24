"""Shared TUI utilities.

Implements: memory/specs/009-model-size-column.md — AC-9
"""

from __future__ import annotations

from pathlib import Path


def fmt_size(path: str) -> str:
    """Return human-readable file size for *path* always in GB.

    Returns ``"—"`` when *path* is empty or the file does not exist.

    Examples::

        fmt_size("/models/llama3.gguf")  # "4.3 GB"
        fmt_size("/models/tiny.gguf")    # "0.7 GB"
        fmt_size("")                     # "—"
        fmt_size("/nonexistent")         # "—"

    Implements: memory/specs/009-model-size-column.md — AC-2, AC-3, AC-4
    """
    if not path:
        return "—"
    try:
        size = Path(path).stat().st_size
    except OSError:
        return "—"
    return f"{size / 1_000_000_000:.1f} GB"
