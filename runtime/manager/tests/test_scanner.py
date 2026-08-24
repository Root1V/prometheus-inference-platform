"""Tests for ProcessScanner: AC-1, AC-2, AC-14."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import psutil

from prometheus_manager.scanner import _is_managed, scan

# ── AC-1: Discover running instances ──────────────────────────────────────────


class TestDiscovery:
    """AC-1: scan() discovers all running llama-server processes."""

    def test_AC1_returns_empty_when_no_llama_server(self, tmp_path: Path):
        """AC-1: no llama-server processes → empty list returned."""
        with patch("prometheus_manager.scanner.psutil.process_iter") as mock_iter:
            mock_iter.return_value = []
            result = scan(tmp_path)
        assert result == []

    def test_AC1_discovers_llama_server_process(self, tmp_path: Path):
        """AC-1: a llama-server process is detected and included in results."""
        mock_proc = MagicMock()
        mock_proc.info = {
            "pid": 12345,
            "name": "llama-server",
            "cmdline": [
                "/usr/local/bin/llama-server",
                "--model",
                "/models/test.gguf",
                "--alias",
                "test-model",
                "--port",
                "8080",
                "--host",
                "127.0.0.1",
            ],
            "cpu_percent": 15.0,
            "memory_info": MagicMock(rss=1024 * 1024 * 512),
            "status": "running",
            "create_time": datetime.now(tz=UTC).timestamp() - 120,
        }

        with (
            patch("prometheus_manager.scanner.psutil.process_iter", return_value=[mock_proc]),
            patch("prometheus_manager.scanner._probe_health", return_value="ready"),
        ):
            result = scan(tmp_path)

        assert len(result) == 1
        s = result[0]
        assert s.pid == 12345
        assert s.port == 8080
        assert s.alias == "test-model"
        assert s.model_path == "/models/test.gguf"
        assert s.state == "ready"

    def test_AC1_non_llama_process_ignored(self, tmp_path: Path):
        """AC-1: non-llama-server processes are not included."""
        mock_proc = MagicMock()
        mock_proc.info = {
            "pid": 99999,
            "name": "python3",
            "cmdline": ["python3", "app.py"],
            "cpu_percent": 1.0,
            "memory_info": MagicMock(rss=1024 * 1024 * 10),
            "status": "running",
            "create_time": datetime.now(tz=UTC).timestamp(),
        }

        with patch("prometheus_manager.scanner.psutil.process_iter", return_value=[mock_proc]):
            result = scan(tmp_path)

        assert result == []


# ── AC-2: State determination ─────────────────────────────────────────────────


class TestStateProbing:
    """AC-2: scan() returns correct state (loading/ready/error/paused)."""

    def _make_proc(self, status: str = "running") -> MagicMock:
        mock_proc = MagicMock()
        mock_proc.info = {
            "pid": 22222,
            "name": "llama-server",
            "cmdline": [
                "llama-server",
                "--model",
                "/m/test.gguf",
                "--alias",
                "m1",
                "--port",
                "9090",
                "--host",
                "127.0.0.1",
            ],
            "cpu_percent": 0.0,
            "memory_info": MagicMock(rss=0),
            "status": status,
            "create_time": datetime.now(tz=UTC).timestamp() - 60,
        }
        return mock_proc

    def test_AC2_state_ready_when_health_200(self, tmp_path: Path):
        """AC-2: state is 'ready' when /health returns 200."""
        with (
            patch(
                "prometheus_manager.scanner.psutil.process_iter", return_value=[self._make_proc()]
            ),
            patch("prometheus_manager.scanner._probe_health", return_value="ready"),
        ):
            result = scan(tmp_path)
        assert result[0].state == "ready"

    def test_AC2_state_loading_when_young_and_not_ready(self, tmp_path: Path):
        """AC-2: state is 'loading' for young process that is not yet listening."""
        with (
            patch(
                "prometheus_manager.scanner.psutil.process_iter", return_value=[self._make_proc()]
            ),
            patch("prometheus_manager.scanner._probe_health", return_value="loading"),
        ):
            result = scan(tmp_path)
        assert result[0].state == "loading"

    def test_AC2_state_paused_when_process_stopped(self, tmp_path: Path):
        """AC-2: state is 'paused' when OS reports STATUS_STOPPED."""
        proc = self._make_proc(status=psutil.STATUS_STOPPED)
        with patch("prometheus_manager.scanner.psutil.process_iter", return_value=[proc]):
            result = scan(tmp_path)
        assert result[0].state == "paused"

    def test_AC2_state_error_for_old_unreachable_process(self, tmp_path: Path):
        """AC-2: state is 'error' for old process that can't be reached."""
        with (
            patch(
                "prometheus_manager.scanner.psutil.process_iter", return_value=[self._make_proc()]
            ),
            patch("prometheus_manager.scanner._probe_health", return_value="error"),
        ):
            result = scan(tmp_path)
        assert result[0].state == "error"


# ── AC-14: Managed vs orphan ──────────────────────────────────────────────────


class TestManagedFlag:
    """AC-14: scan() correctly differentiates managed vs orphan processes."""

    def test_AC14_managed_when_pid_file_matches(self, tmp_path: Path):
        """AC-14: managed=True when PID file contains the running PID."""
        pid = 55555
        pid_file = tmp_path / "mymodel.pid"
        pid_file.write_text(str(pid))
        assert _is_managed(pid_file, pid) is True

    def test_AC14_orphan_when_no_pid_file(self, tmp_path: Path):
        """AC-14: managed=False when no PID file exists."""
        pid_file = tmp_path / "missing.pid"
        assert _is_managed(pid_file, 99) is False

    def test_AC14_orphan_when_pid_mismatch(self, tmp_path: Path):
        """AC-14: managed=False when PID file contains different PID."""
        pid_file = tmp_path / "model.pid"
        pid_file.write_text("11111")
        assert _is_managed(pid_file, 22222) is False

    def test_AC14_scan_marks_managed_correctly(self, tmp_path: Path):
        """AC-14: scan() sets managed=True for processes with matching PID file."""
        pid = 33333
        alias = "managed-model"
        pid_file = tmp_path / f"{alias}.pid"
        pid_file.write_text(str(pid))

        mock_proc = MagicMock()
        mock_proc.info = {
            "pid": pid,
            "name": "llama-server",
            "cmdline": [
                "llama-server",
                "--model",
                "/m/test.gguf",
                "--alias",
                alias,
                "--port",
                "8080",
                "--host",
                "127.0.0.1",
            ],
            "cpu_percent": 0.0,
            "memory_info": MagicMock(rss=0),
            "status": "running",
            "create_time": datetime.now(tz=UTC).timestamp(),
        }

        with (
            patch("prometheus_manager.scanner.psutil.process_iter", return_value=[mock_proc]),
            patch("prometheus_manager.scanner._probe_health", return_value="ready"),
        ):
            result = scan(tmp_path)

        assert result[0].managed is True
