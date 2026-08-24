"""Tests for Capacity checks: AC-24, AC-25."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from prometheus_manager.capacity import check_capacity


class TestCapacityAC24:
    """AC-24: soft warning at 85% projected RAM usage."""

    def test_AC24_ok_when_below_85_pct(self, tmp_path: Path):
        """AC-24: level=ok when projected usage below 85%."""
        model_path = tmp_path / "model.gguf"
        model_path.write_bytes(b"\x00" * (100 * 1024 * 1024))  # 100 MB

        total_ram = 8 * 1024  # 8 GB in MB
        current_rss = 100  # MB already used

        with patch("prometheus_manager.capacity.psutil.virtual_memory") as mock_vm:
            mock_vm.return_value = MagicMock(total=total_ram * 1024 * 1024)
            cap = check_capacity(model_path, rss_estimate_mb=None, current_rss_mb=current_rss)

        assert cap.level == "ok"

    def test_AC24_warning_when_between_85_and_95_pct(self):
        """AC-24: level=warning when projected RAM 85–94%."""
        total_mb = 8 * 1024  # 8 GB
        current_rss_mb = int(total_mb * 0.80)  # already at 80%
        estimated_new_mb = int(total_mb * 0.10)  # +10% → 90% projected

        with patch("prometheus_manager.capacity.psutil.virtual_memory") as mock_vm:
            mock_vm.return_value = MagicMock(total=total_mb * 1024 * 1024)
            cap = check_capacity(
                path=None,
                rss_estimate_mb=estimated_new_mb,
                current_rss_mb=current_rss_mb,
            )

        assert cap.level == "warning"
        assert 85.0 <= cap.projected_pct < 95.0

    def test_AC24_warning_has_message(self):
        """AC-24: warning includes human-readable message."""
        total_mb = 4 * 1024
        current_rss_mb = int(total_mb * 0.80)
        estimated_new_mb = int(total_mb * 0.10)

        with patch("prometheus_manager.capacity.psutil.virtual_memory") as mock_vm:
            mock_vm.return_value = MagicMock(total=total_mb * 1024 * 1024)
            cap = check_capacity(
                path=None,
                rss_estimate_mb=estimated_new_mb,
                current_rss_mb=current_rss_mb,
            )

        assert cap.message
        assert len(cap.message) > 0


class TestCapacityAC25:
    """AC-25: hard block at 95% projected RAM usage."""

    def test_AC25_blocked_when_at_or_above_95_pct(self):
        """AC-25: level=blocked when projected RAM ≥ 95%."""
        total_mb = 4 * 1024  # 4 GB
        current_rss_mb = int(total_mb * 0.90)  # at 90%
        estimated_new_mb = int(total_mb * 0.10)  # +10% → 100%

        with patch("prometheus_manager.capacity.psutil.virtual_memory") as mock_vm:
            mock_vm.return_value = MagicMock(total=total_mb * 1024 * 1024)
            cap = check_capacity(
                path=None,
                rss_estimate_mb=estimated_new_mb,
                current_rss_mb=current_rss_mb,
            )

        assert cap.level == "blocked"

    def test_AC25_blocked_includes_message(self):
        """AC-25: blocked result includes a message."""
        total_mb = 2 * 1024
        current_rss_mb = int(total_mb * 0.93)
        estimated_new_mb = int(total_mb * 0.10)

        with patch("prometheus_manager.capacity.psutil.virtual_memory") as mock_vm:
            mock_vm.return_value = MagicMock(total=total_mb * 1024 * 1024)
            cap = check_capacity(
                path=None,
                rss_estimate_mb=estimated_new_mb,
                current_rss_mb=current_rss_mb,
            )

        assert cap.level == "blocked"
        assert cap.message

    def test_AC24_25_estimated_from_file_size_when_no_estimate(self, tmp_path: Path):
        """AC-24/25: when rss_estimate_mb is None, estimate from file size × 1.2."""
        model_path = tmp_path / "large.gguf"
        file_mb = 500
        model_path.write_bytes(b"\x00" * (file_mb * 1024 * 1024))

        total_mb = 2 * 1024  # 2 GB
        current_rss_mb = 0

        with patch("prometheus_manager.capacity.psutil.virtual_memory") as mock_vm:
            mock_vm.return_value = MagicMock(total=total_mb * 1024 * 1024)
            cap = check_capacity(
                path=model_path,
                rss_estimate_mb=None,
                current_rss_mb=current_rss_mb,
            )

        # estimated = 500 × 1.2 = 600 MB, 600/2048 = 29.3% → ok
        assert cap.level == "ok"
        assert cap.estimated_new_rss_mb == pytest.approx(file_mb * 1.2, rel=0.01)
