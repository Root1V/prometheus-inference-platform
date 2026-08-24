"""Tests for Lifecycle operations.

AC-4, AC-5, AC-6, AC-6b, AC-6c, AC-6d, AC-7, AC-8, AC-9, AC-10.
"""

from __future__ import annotations

import signal
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from prometheus_manager_core.lifecycle import (
    LifecycleError,
    _build_llama_cpp_cmd,
    _build_mlx_cmd,
    _build_sglang_cmd,
    _build_vllm_cmd,
    _verify_pid_file,
    deregister_instance,
    pause_instance,
    restart_instance,
    resume_instance,
    start_instance,
    stop_instance,
)
from prometheus_manager_core.registry import RegistryEntry

# ── RM-08: per-backend command builders ─────────────────────────────────────────


class TestBackendCommandBuilders:
    """One builder per backend — see memory/wiki/inference-engines.md (RM-06)."""

    def _entry(self, **overrides):
        defaults = dict(
            id="test-model",
            path="/models/test-model",
            context_length=8192,
            port=9090,
        )
        defaults.update(overrides)
        return RegistryEntry(**defaults)

    def test_llama_cpp_cmd_uses_alias_and_ctx_size(self):
        cmd = _build_llama_cpp_cmd("llama-server", self._entry(), 9090, "127.0.0.1")
        assert cmd[0] == "llama-server"
        assert "--model" in cmd and "/models/test-model" in cmd
        assert "--alias" in cmd and "test-model" in cmd
        assert "--ctx-size" in cmd and "8192" in cmd
        assert "--port" in cmd and "9090" in cmd

    def test_mlx_cmd_has_no_alias_or_ctx_size_flags(self):
        """mlx_lm.server (verified via --help) has neither flag."""
        cmd = _build_mlx_cmd("mlx_lm.server", self._entry(), 9090, "127.0.0.1")
        assert cmd == [
            "mlx_lm.server",
            "--model",
            "/models/test-model",
            "--host",
            "127.0.0.1",
            "--port",
            "9090",
        ]
        assert "--alias" not in cmd
        assert "--ctx-size" not in cmd

    def test_vllm_cmd_uses_positional_model_and_served_model_name(self):
        cmd = _build_vllm_cmd("vllm", self._entry(), 9090, "127.0.0.1")
        assert cmd[0:3] == ["vllm", "serve", "/models/test-model"]
        assert "--served-model-name" in cmd and "test-model" in cmd
        assert "--max-model-len" in cmd and "8192" in cmd

    def test_sglang_cmd_uses_module_and_model_path_flag(self):
        cmd = _build_sglang_cmd("python3", self._entry(), 9090, "127.0.0.1")
        assert cmd[0:3] == ["python3", "-m", "sglang.launch_server"]
        assert "--model-path" in cmd and "/models/test-model" in cmd
        assert "--served-model-name" in cmd and "test-model" in cmd
        assert "--context-length" in cmd and "8192" in cmd

    def test_start_instance_dispatches_on_backend(self, default_config, populated_registry):
        """start_instance picks the command builder matching entry.backend."""
        populated_registry.update("test-model", backend="mlx", path="mlx-community/model-4bit")
        mock_state = MagicMock(pid=42, port=9090, alias="test-model", model_id="test-model")

        with (
            patch("prometheus_manager_core.lifecycle._find_running", return_value=None),
            patch("prometheus_manager_core.lifecycle._find_free_port", return_value=9090),
            patch("prometheus_manager_core.lifecycle.subprocess.Popen") as mock_popen,
            patch("prometheus_manager_core.lifecycle.httpx.get") as mock_get,
            patch("prometheus_manager_core.lifecycle.scan", return_value=[mock_state]),
        ):
            mock_proc = MagicMock()
            mock_proc.pid = 42
            mock_proc.poll.return_value = None
            mock_popen.return_value = mock_proc
            mock_get.return_value = MagicMock(status_code=200)

            start_instance("test-model", default_config, populated_registry)

            cmd = mock_popen.call_args[0][0]
            assert cmd[0] == "mlx_lm.server"
            assert "mlx-community/model-4bit" in cmd

    def test_start_instance_rejects_unknown_backend(self, default_config, populated_registry):
        populated_registry.update("test-model", backend="does-not-exist")
        with pytest.raises(LifecycleError, match="Unknown backend"):
            start_instance("test-model", default_config, populated_registry)


# ── AC-9: Duplicate start ──────────────────────────────────────────────────────


class TestACDuplicateStart:
    """AC-9: Starting an already-running instance raises LifecycleError."""

    def test_AC9_start_raises_when_already_running(self, default_config, populated_registry):
        """AC-9: start_instance raises if model already running."""
        mock_state = MagicMock(pid=12345, alias="test-model", model_id="test-model")

        with (
            patch("prometheus_manager_core.lifecycle._find_running", return_value=mock_state),
            pytest.raises(LifecycleError, match="already running"),
        ):
            start_instance("test-model", default_config, populated_registry)


# ── AC-10: Unknown model ───────────────────────────────────────────────────────


class TestACUnknownModel:
    """AC-10: Starting a model not in registry raises LifecycleError."""

    def test_AC10_start_raises_for_unknown_model(self, default_config, empty_registry):
        """AC-10: model not in registry raises LifecycleError."""
        with pytest.raises(LifecycleError, match="not found in registry"):
            start_instance("does-not-exist", default_config, empty_registry)


# ── AC-5: Start timeout ────────────────────────────────────────────────────────


class TestACStartTimeout:
    """AC-5: If instance does not become healthy within timeout, it is killed."""

    def test_AC5_timeout_kills_process_and_raises(self, default_config, populated_registry):
        """AC-5: process killed and LifecycleError raised on timeout."""
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None  # process alive
        mock_proc.pid = 99999

        with (
            patch("prometheus_manager_core.lifecycle._find_running", return_value=None),
            patch("prometheus_manager_core.lifecycle.subprocess.Popen", return_value=mock_proc),
            patch(
                "prometheus_manager_core.lifecycle.httpx.get",
                side_effect=Exception("connection refused"),
            ),
            patch("prometheus_manager_core.lifecycle.time.sleep"),
            patch(
                "prometheus_manager_core.lifecycle.time.monotonic", side_effect=[0, 999]
            ),  # immediately past deadline
            pytest.raises(LifecycleError, match="Timed out"),
        ):
            start_instance("test-model", default_config, populated_registry)

        mock_proc.terminate.assert_called_once()


# ── AC-4: Successful start ────────────────────────────────────────────────────


class TestACSuccessfulStart:
    """AC-4: start_instance spawns llama-server with correct arguments."""

    def test_AC4_start_builds_correct_command(self, default_config, populated_registry):
        """AC-4: subprocess.Popen called with correct flags."""
        mock_proc = MagicMock()
        mock_proc.pid = 12345
        mock_proc.poll.return_value = None

        mock_state = MagicMock(pid=12345, port=9090)

        with (
            patch("prometheus_manager_core.lifecycle._find_running", return_value=None),
            patch(
                "prometheus_manager_core.lifecycle.subprocess.Popen", return_value=mock_proc
            ) as mock_popen,
            patch("prometheus_manager_core.lifecycle.httpx.get") as mock_get,
            patch("prometheus_manager_core.lifecycle.scan", return_value=[mock_state]),
            patch("prometheus_manager_core.lifecycle.time.sleep"),
            patch("prometheus_manager_core.lifecycle.time.monotonic", side_effect=[0, 1, 999]),
        ):
            mock_get.return_value = MagicMock(status_code=200)
            start_instance("test-model", default_config, populated_registry)

        call_args = mock_popen.call_args[0][0]
        assert "--host" in call_args
        host_idx = call_args.index("--host")
        assert call_args[host_idx + 1] == "127.0.0.1"  # AC-19: always 127.0.0.1
        assert "--alias" in call_args
        assert "--model" in call_args
        assert "--port" in call_args


# ── AC-6: Stop instance ────────────────────────────────────────────────────────


class TestACStop:
    """AC-6: stop_instance sends SIGTERM, waits, then SIGKILL if needed."""

    def test_AC6_stop_sends_sigterm(self, default_config, populated_registry):
        """AC-6: SIGTERM sent to running process."""
        mock_ps = MagicMock(pid=12345, alias="test-model")
        mock_psutil_proc = MagicMock()
        mock_psutil_proc.wait.return_value = None  # graceful exit

        pid_file = default_config.resolved_pid_dir / "test-model.pid"
        default_config.resolved_pid_dir.mkdir(parents=True, exist_ok=True)
        pid_file.write_text("12345")

        with (
            patch("prometheus_manager_core.lifecycle._find_running", return_value=mock_ps),
            patch(
                "prometheus_manager_core.lifecycle.psutil.Process", return_value=mock_psutil_proc
            ),
        ):
            stop_instance("test-model", default_config, populated_registry)

        mock_psutil_proc.send_signal.assert_called_once_with(signal.SIGTERM)

    def test_AC6_stop_raises_for_not_running(self, default_config, populated_registry):
        """AC-6: stop_instance raises LifecycleError when nothing is running."""
        with (
            patch("prometheus_manager_core.lifecycle._find_running", return_value=None),
            pytest.raises(LifecycleError, match="No running"),
        ):
            stop_instance("test-model", default_config, populated_registry)


# ── AC-7: SIGKILL fallback ─────────────────────────────────────────────────────


class TestACSigKill:
    """AC-7: SIGKILL sent if process doesn't exit within stop_timeout_s."""

    def test_AC7_sigkill_sent_after_timeout(self, default_config, populated_registry):
        """AC-7: process.kill() called when wait() raises TimeoutExpired."""
        import psutil as _psutil

        mock_ps = MagicMock(pid=12345, alias="test-model")
        mock_psutil_proc = MagicMock()
        # First wait() times out, second wait() (after kill) succeeds
        mock_psutil_proc.wait.side_effect = [
            _psutil.TimeoutExpired(12345, 10),
            None,
        ]

        pid_file = default_config.resolved_pid_dir / "test-model.pid"
        default_config.resolved_pid_dir.mkdir(parents=True, exist_ok=True)
        pid_file.write_text("12345")

        with (
            patch("prometheus_manager_core.lifecycle._find_running", return_value=mock_ps),
            patch(
                "prometheus_manager_core.lifecycle.psutil.Process", return_value=mock_psutil_proc
            ),
        ):
            stop_instance("test-model", default_config, populated_registry)

        mock_psutil_proc.kill.assert_called_once()


# ── AC-6b: Pause ──────────────────────────────────────────────────────────────


class TestACPause:
    """AC-6b: pause_instance sends SIGSTOP."""

    def test_AC6b_sigstop_sent(self, default_config):
        """AC-6b: SIGSTOP sent to process."""
        mock_ps = MagicMock(pid=77777, alias="test-model")
        with (
            patch("prometheus_manager_core.lifecycle._find_running", return_value=mock_ps),
            patch("prometheus_manager_core.lifecycle.os.kill") as mock_kill,
        ):
            pause_instance("test-model", default_config)

        mock_kill.assert_called_once_with(77777, signal.SIGSTOP)

    def test_AC6b_pause_raises_if_not_running(self, default_config):
        """AC-6b: raises LifecycleError when no instance is running."""
        with (
            patch("prometheus_manager_core.lifecycle._find_running", return_value=None),
            pytest.raises(LifecycleError),
        ):
            pause_instance("test-model", default_config)


# ── AC-6c: Resume ─────────────────────────────────────────────────────────────


class TestACResume:
    """AC-6c: resume_instance sends SIGCONT."""

    def test_AC6c_sigcont_sent(self, default_config):
        """AC-6c: SIGCONT sent to paused process."""
        mock_state = MagicMock(pid=77777, alias="test-model", model_id="test-model")

        with (
            patch("prometheus_manager_core.lifecycle.scan", return_value=[mock_state]),
            patch("prometheus_manager_core.lifecycle.os.kill") as mock_kill,
        ):
            resume_instance("test-model", default_config)

        mock_kill.assert_called_once_with(77777, signal.SIGCONT)

    def test_AC6c_resume_raises_if_not_found(self, default_config):
        """AC-6c: raises LifecycleError when no paused instance found."""
        with (
            patch("prometheus_manager_core.lifecycle.scan", return_value=[]),
            pytest.raises(LifecycleError),
        ):
            resume_instance("test-model", default_config)


# ── AC-8: Restart ─────────────────────────────────────────────────────────────


class TestACRestart:
    """AC-8: restart_instance = stop + start."""

    def test_AC8_calls_stop_then_start(self, default_config, populated_registry):
        """AC-8: restart delegates to stop_instance then start_instance."""
        mock_state = MagicMock(pid=9999, port=9090)

        with (
            patch("prometheus_manager_core.lifecycle.stop_instance") as mock_stop,
            patch(
                "prometheus_manager_core.lifecycle.start_instance", return_value=mock_state
            ) as mock_start,
        ):
            result = restart_instance("test-model", default_config, populated_registry)

        mock_stop.assert_called_once()
        mock_start.assert_called_once()
        assert result == mock_state


# ── AC-6d: Deregister ─────────────────────────────────────────────────────────


class TestACDeregister:
    """AC-6d: deregister_instance stops then removes from registry."""

    def test_AC6d_stops_and_removes(self, default_config, populated_registry):
        """AC-6d: deregister stops instance then removes from registry."""
        with patch("prometheus_manager_core.lifecycle.stop_instance") as mock_stop:
            deregister_instance("test-model", default_config, populated_registry)

        mock_stop.assert_called_once()
        assert populated_registry.get("test-model") is None


# ── PID integrity (security)───────────────────────────────────────────────────


class TestPIDIntegrity:
    """Security: _verify_pid_file guards against PID reuse."""

    def test_pid_mismatch_raises_lifecycle_error(self, tmp_path: Path):
        """PID file mismatch raises LifecycleError."""
        pid_path = tmp_path / "model.pid"
        pid_path.write_text("11111")
        with pytest.raises(LifecycleError, match="PID file mismatch"):
            _verify_pid_file(pid_path, 22222, "model")

    def test_pid_match_passes_silently(self, tmp_path: Path):
        """Matching PID file raises nothing."""
        pid_path = tmp_path / "model.pid"
        pid_path.write_text("11111")
        _verify_pid_file(pid_path, 11111, "model")  # no exception

    def test_pid_missing_pid_file_passes_silently(self, tmp_path: Path):
        """Missing PID file does not block stop (unmanaged process)."""
        pid_path = tmp_path / "missing.pid"
        _verify_pid_file(pid_path, 11111, "model")  # no exception


# ── spec-010 AC-8 & AC-9: discovery auto-toggle ───────────────────────────────


class TestDiscoveryAutoToggle:
    """memory/specs/010 AC-8, AC-9: start sets discovery=True, stop sets discovery=False."""

    def test_AC8_start_sets_discovery_true(self, default_config, populated_registry):
        """AC-8: successful start sets entry.discovery = True."""
        mock_state = MagicMock(pid=42, port=9090, alias="test-model", model_id="test-model")

        with (
            patch("prometheus_manager_core.lifecycle._find_running", return_value=None),
            patch("prometheus_manager_core.lifecycle._find_free_port", return_value=9090),
            patch("prometheus_manager_core.lifecycle.subprocess.Popen") as mock_popen,
            patch("prometheus_manager_core.lifecycle.httpx.get") as mock_get,
            patch("prometheus_manager_core.lifecycle.scan", return_value=[mock_state]),
        ):
            mock_proc = MagicMock()
            mock_proc.pid = 42
            mock_proc.poll.return_value = None
            mock_popen.return_value = mock_proc
            mock_get.return_value = MagicMock(status_code=200)

            result = start_instance("test-model", default_config, populated_registry)

        assert result == mock_state
        assert populated_registry.get("test-model").discovery is True

    def test_AC9_stop_sets_discovery_false(self, default_config, populated_registry):
        """AC-9: stop sets entry.discovery = False."""
        # First mark as discoverable
        populated_registry.update("test-model", discovery=True)
        assert populated_registry.get("test-model").discovery is True

        mock_state = MagicMock(pid=42, port=9090)
        pid_path = default_config.resolved_pid_dir / "test-model.pid"
        pid_path.parent.mkdir(parents=True, exist_ok=True)
        pid_path.write_text("42")

        with (
            patch("prometheus_manager_core.lifecycle._find_running", return_value=mock_state),
            patch("prometheus_manager_core.lifecycle._verify_pid_file"),
            patch("prometheus_manager_core.lifecycle.psutil.Process") as mock_proc_cls,
        ):
            mock_proc = MagicMock()
            mock_proc.wait.return_value = None
            mock_proc_cls.return_value = mock_proc

            stop_instance("test-model", default_config, populated_registry)

        assert populated_registry.get("test-model").discovery is False
