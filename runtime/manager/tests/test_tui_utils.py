"""Tests for tui.utils helper functions.

Implements: memory/specs/009-model-size-column.md — AC-9
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from prometheus_manager.tui.utils import fmt_size


class TestFmtSize:
    """Tests for fmt_size() — AC-2, AC-3, AC-4."""

    def test_empty_path_returns_dash(self) -> None:
        """AC-3: empty path → '—'."""
        assert fmt_size("") == "—"

    def test_nonexistent_file_returns_dash(self) -> None:
        """AC-4: path set but file missing → '—'."""
        assert fmt_size("/nonexistent/path/model.gguf") == "—"

    def test_gb_formatting(self, tmp_path: Path) -> None:
        """AC-2: files ≥ 1 GB → 'X.X GB'."""
        f = tmp_path / "large.gguf"
        # Write exactly 4_300_000_000 bytes via truncate (sparse file — no disk usage)
        f.touch()
        os.truncate(f, 4_300_000_000)
        assert fmt_size(str(f)) == "4.3 GB"

    def test_mb_formatting(self, tmp_path: Path) -> None:
        """AC-2: small files (< 1 GB) also shown in GB."""
        f = tmp_path / "small.gguf"
        f.touch()
        os.truncate(f, 737_000_000)
        assert fmt_size(str(f)) == "0.7 GB"

    def test_exactly_1gb_boundary(self, tmp_path: Path) -> None:
        """Boundary: exactly 1_000_000_000 bytes → '1.0 GB'."""
        f = tmp_path / "boundary.gguf"
        f.touch()
        os.truncate(f, 1_000_000_000)
        assert fmt_size(str(f)) == "1.0 GB"

    def test_just_below_1gb(self, tmp_path: Path) -> None:
        """Boundary: 999_999_999 bytes → '1.0 GB' (rounds up at 1 decimal)."""
        f = tmp_path / "below.gguf"
        f.touch()
        os.truncate(f, 999_999_999)
        assert fmt_size(str(f)) == "1.0 GB"

    def test_permission_error_returns_dash(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """AC-4: OSError (e.g. permission denied) → '—'."""
        f = tmp_path / "noperm.gguf"
        f.touch()

        def raise_oserror(self: Path) -> os.stat_result:  # type: ignore[override]
            raise OSError("permission denied")

        monkeypatch.setattr(Path, "stat", raise_oserror)
        assert fmt_size(str(f)) == "—"
