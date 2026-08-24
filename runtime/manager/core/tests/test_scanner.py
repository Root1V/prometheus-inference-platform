"""Tests for ProcessScanner: AC-1, AC-2, AC-14."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import psutil

from prometheus_manager_core.scanner import (
    _extract_model_path,
    _is_managed,
    _match_backend,
    _pid_file_map,
    scan,
)

# ── RM-08: multi-backend process recognition ────────────────────────────────────


class TestMatchBackend:
    """_match_backend recognizes llama.cpp/mlx/vllm/sglang cmdlines.

    vllm/sglang matching is unverified against real processes — see
    memory/wiki/inference-engines.md (RM-06); these tests cover the parsing
    logic against documented CLI shapes only.
    """

    def test_llama_cpp(self):
        sig = _match_backend(
            "llama-server", ["llama-server", "--model", "m.gguf", "--port", "8080"]
        )
        assert sig is not None and sig.backend == "llama_cpp"

    def test_mlx(self):
        sig = _match_backend(
            "mlx_lm.server", ["mlx_lm.server", "--model", "mlx-community/m", "--port", "8080"]
        )
        assert sig is not None and sig.backend == "mlx"

    def test_vllm_requires_serve_subcommand(self):
        assert (
            _match_backend("vllm", ["vllm", "serve", "meta-llama/m", "--port", "8080"]) is not None
        )
        # "vllm" alone (no `serve`) must not match — avoids false positives.
        assert _match_backend("vllm", ["vllm", "--help"]) is None

    def test_sglang(self):
        sig = _match_backend(
            "python3",
            ["python3", "-m", "sglang.launch_server", "--model-path", "m", "--port", "8080"],
        )
        assert sig is not None and sig.backend == "sglang"

    def test_unrelated_process_does_not_match(self):
        assert _match_backend("bash", ["bash", "-c", "echo hi"]) is None


class TestExtractModelPath:
    def test_flag_based_backends(self):
        sig = _match_backend("mlx_lm.server", ["mlx_lm.server", "--model", "mlx-community/m"])
        assert _extract_model_path(["mlx_lm.server", "--model", "mlx-community/m"], sig) == (
            "mlx-community/m"
        )

    def test_vllm_positional_model(self):
        sig = _match_backend("vllm", ["vllm", "serve", "meta-llama/m", "--port", "8080"])
        cmdline = ["vllm", "serve", "meta-llama/m", "--port", "8080"]
        assert _extract_model_path(cmdline, sig) == "meta-llama/m"


class TestPidFileMap:
    def test_maps_pid_to_model_id(self, tmp_path: Path):
        (tmp_path / "my-model.pid").write_text("4242")
        assert _pid_file_map(tmp_path) == {4242: "my-model"}

    def test_empty_dir_returns_empty_map(self, tmp_path: Path):
        assert _pid_file_map(tmp_path) == {}

    def test_missing_dir_returns_empty_map(self, tmp_path: Path):
        assert _pid_file_map(tmp_path / "does-not-exist") == {}

    def test_ignores_corrupt_pid_file(self, tmp_path: Path):
        (tmp_path / "broken.pid").write_text("not-a-pid")
        assert _pid_file_map(tmp_path) == {}


class TestScanRecognizesMlxProcess:
    """mlx_lm.server has no --alias flag — scan() must resolve the alias from
    the PID file instead (see _pid_file_map), same mechanism for every backend."""

    def test_alias_resolved_from_pid_file_not_cmdline(self, tmp_path: Path):
        pid = 54321
        (tmp_path / "my-mlx-model.pid").write_text(str(pid))

        mock_proc = MagicMock()
        mock_proc.info = {
            "pid": pid,
            "name": "mlx_lm.server",
            "cmdline": ["mlx_lm.server", "--model", "mlx-community/m-4bit", "--port", "8081"],
            "memory_info": MagicMock(rss=0),
            "status": "running",
            "create_time": datetime.now(tz=UTC).timestamp(),
        }

        with (
            patch("prometheus_manager_core.scanner.psutil.process_iter", return_value=[mock_proc]),
            patch("prometheus_manager_core.scanner._probe_health", return_value="ready"),
        ):
            result = scan(tmp_path)

        assert len(result) == 1
        assert result[0].backend == "mlx"
        assert result[0].alias == "my-mlx-model"
        assert result[0].managed is True
        assert result[0].model_path == "mlx-community/m-4bit"


# ── AC-1: Discover running instances ──────────────────────────────────────────


class TestDiscovery:
    """AC-1: scan() discovers all running llama-server processes."""

    def test_AC1_returns_empty_when_no_llama_server(self, tmp_path: Path):
        """AC-1: no llama-server processes → empty list returned."""
        with patch("prometheus_manager_core.scanner.psutil.process_iter") as mock_iter:
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
            patch("prometheus_manager_core.scanner.psutil.process_iter", return_value=[mock_proc]),
            patch("prometheus_manager_core.scanner._probe_health", return_value="ready"),
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

        with patch("prometheus_manager_core.scanner.psutil.process_iter", return_value=[mock_proc]):
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
                "prometheus_manager_core.scanner.psutil.process_iter",
                return_value=[self._make_proc()],
            ),
            patch("prometheus_manager_core.scanner._probe_health", return_value="ready"),
        ):
            result = scan(tmp_path)
        assert result[0].state == "ready"

    def test_AC2_state_loading_when_young_and_not_ready(self, tmp_path: Path):
        """AC-2: state is 'loading' for young process that is not yet listening."""
        with (
            patch(
                "prometheus_manager_core.scanner.psutil.process_iter",
                return_value=[self._make_proc()],
            ),
            patch("prometheus_manager_core.scanner._probe_health", return_value="loading"),
        ):
            result = scan(tmp_path)
        assert result[0].state == "loading"

    def test_AC2_state_paused_when_process_stopped(self, tmp_path: Path):
        """AC-2: state is 'paused' when OS reports STATUS_STOPPED."""
        proc = self._make_proc(status=psutil.STATUS_STOPPED)
        with patch("prometheus_manager_core.scanner.psutil.process_iter", return_value=[proc]):
            result = scan(tmp_path)
        assert result[0].state == "paused"

    def test_AC2_state_error_for_old_unreachable_process(self, tmp_path: Path):
        """AC-2: state is 'error' for old process that can't be reached."""
        with (
            patch(
                "prometheus_manager_core.scanner.psutil.process_iter",
                return_value=[self._make_proc()],
            ),
            patch("prometheus_manager_core.scanner._probe_health", return_value="error"),
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
            patch("prometheus_manager_core.scanner.psutil.process_iter", return_value=[mock_proc]),
            patch("prometheus_manager_core.scanner._probe_health", return_value="ready"),
        ):
            result = scan(tmp_path)

        assert result[0].managed is True
