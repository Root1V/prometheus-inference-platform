"""Tests for RM-10 write endpoints: register/deregister/start/stop/restart.

See docs/roadmap.md RM-10 (gateway admin dashboard, phase 1) and
memory/wiki/model-registry.md.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from prometheus_manager_core.config import (
    ApiConfig,
    DashboardConfig,
    DownloadsConfig,
    ManagerConfig,
    RegistryConfig,
    ServerConfig,
)
from prometheus_manager_core.lifecycle import LifecycleError
from prometheus_manager_core.registry import Registry, RegistryEntry
from prometheus_manager_core.scanner import ProcessState

from prometheus_manager_api.app import app
from prometheus_manager_api.auth import require_backend_registry_write

# ── Setup ──────────────────────────────────────────────────────────────────────


def _make_registry(tmp_path: Path) -> Registry:
    reg = Registry(tmp_path / "registry.yaml")
    reg.add(
        RegistryEntry(
            id="llama3-test",
            path="/models/llama3.gguf",
            context_length=4096,
            port=8080,
            family="llama",
            quantization="Q4_0",
            discovery=True,
        )
    )
    return reg


def _make_config(tmp_path: Path) -> ManagerConfig:
    return ManagerConfig(
        api=ApiConfig(),
        server=ServerConfig(
            binary="/usr/bin/echo",
            host="127.0.0.1",
            log_dir=str(tmp_path / "logs"),
            pid_dir=str(tmp_path / "run"),
            start_timeout_s=5,
            stop_timeout_s=5,
        ),
        registry=RegistryConfig(path=str(tmp_path / "registry.yaml")),
        downloads=DownloadsConfig(dir=str(tmp_path / "models")),
        dashboard=DashboardConfig(refresh_interval_s=2),
    )


def _make_client(tmp_path: Path) -> TestClient:
    app.state.registry = _make_registry(tmp_path)
    app.state.config = _make_config(tmp_path)
    app.state.pid_dir = tmp_path / "run"
    app.state.jwks_url = "http://localhost:9000/v1/jwks"
    app.state.proxy_host = ""
    return TestClient(app, raise_server_exceptions=True)


def _authed(client: TestClient):
    app.dependency_overrides[require_backend_registry_write] = lambda: {
        "sub": "operator",
        "scope": "backend-registry:write",
    }
    return client


def _clear_override():
    app.dependency_overrides.pop(require_backend_registry_write, None)


# ── POST /v1/backends (register) ─────────────────────────────────────────────


class TestRegister:
    def test_register_requires_auth(self, tmp_path: Path):
        client = _make_client(tmp_path)
        resp = client.post("/v1/backends", json={"id": "new-model", "port": 8090})
        assert resp.status_code == 401

    def test_register_success(self, tmp_path: Path):
        client = _authed(_make_client(tmp_path))
        try:
            resp = client.post(
                "/v1/backends",
                json={
                    "id": "new-model",
                    "port": 8090,
                    "path": "/models/new-model.gguf",
                    "context_length": 8192,
                    "modality": "vision",
                    "mmproj_path": "/models/mmproj.gguf",
                },
                headers={"Authorization": "Bearer dummy"},
            )
        finally:
            _clear_override()
        assert resp.status_code == 201
        body = resp.json()
        assert body["id"] == "new-model"
        assert body["modality"] == "vision"
        assert body["mmproj_path"] == "/models/mmproj.gguf"

    def test_register_invalid_id_returns_400(self, tmp_path: Path):
        client = _authed(_make_client(tmp_path))
        try:
            resp = client.post(
                "/v1/backends",
                json={"id": "N", "port": 8090},
                headers={"Authorization": "Bearer dummy"},
            )
        finally:
            _clear_override()
        assert resp.status_code == 400
        assert resp.json()["detail"]["type"].endswith("invalid-registration")

    def test_register_bad_modality_returns_400(self, tmp_path: Path):
        client = _authed(_make_client(tmp_path))
        try:
            resp = client.post(
                "/v1/backends",
                json={"id": "audio-model", "port": 8091, "modality": "audio"},
                headers={"Authorization": "Bearer dummy"},
            )
        finally:
            _clear_override()
        assert resp.status_code == 400


# ── PATCH /v1/backends/{id} (update) ──────────────────────────────────────────


class TestUpdate:
    def test_update_requires_auth(self, tmp_path: Path):
        client = _make_client(tmp_path)
        resp = client.patch("/v1/backends/llama3-test", json={"context_length": 8192})
        assert resp.status_code == 401

    def test_update_unknown_model_returns_404(self, tmp_path: Path):
        client = _authed(_make_client(tmp_path))
        try:
            resp = client.patch(
                "/v1/backends/does-not-exist",
                json={"context_length": 8192},
                headers={"Authorization": "Bearer dummy"},
            )
        finally:
            _clear_override()
        assert resp.status_code == 404

    def test_update_success_persists_and_returns_updated_entry(self, tmp_path: Path):
        client = _authed(_make_client(tmp_path))
        try:
            resp = client.patch(
                "/v1/backends/llama3-test",
                json={"context_length": 16384, "family": "llama3.1", "port": 8099},
                headers={"Authorization": "Bearer dummy"},
            )
        finally:
            _clear_override()
        assert resp.status_code == 200
        body = resp.json()
        assert body["context_length"] == 16384
        assert body["family"] == "llama3.1"
        assert body["port"] == 8099
        # persisted, not just returned in the response
        assert app.state.registry.get("llama3-test").context_length == 16384

    def test_update_id_field_is_ignored(self, tmp_path: Path):
        """id is the registry key — PATCH cannot rename an entry."""
        client = _authed(_make_client(tmp_path))
        try:
            resp = client.patch(
                "/v1/backends/llama3-test",
                json={"id": "renamed", "family": "llama3.1"},
                headers={"Authorization": "Bearer dummy"},
            )
        finally:
            _clear_override()
        assert resp.status_code == 200
        assert resp.json()["id"] == "llama3-test"
        assert app.state.registry.get("renamed") is None

    def test_update_bad_modality_returns_400(self, tmp_path: Path):
        client = _authed(_make_client(tmp_path))
        try:
            resp = client.patch(
                "/v1/backends/llama3-test",
                json={"modality": "audio"},
                headers={"Authorization": "Bearer dummy"},
            )
        finally:
            _clear_override()
        assert resp.status_code == 400
        assert resp.json()["detail"]["type"].endswith("invalid-update")
        # rejected update must not be partially applied
        assert app.state.registry.get("llama3-test").modality == "text"

    def test_update_bad_port_returns_400(self, tmp_path: Path):
        client = _authed(_make_client(tmp_path))
        try:
            resp = client.patch(
                "/v1/backends/llama3-test",
                json={"port": 80},
                headers={"Authorization": "Bearer dummy"},
            )
        finally:
            _clear_override()
        assert resp.status_code == 400

    def test_update_path_revalidated_against_new_backend(self, tmp_path: Path):
        """Changing backend to llama_cpp with a non-.gguf path must fail."""
        client = _authed(_make_client(tmp_path))
        try:
            resp = client.patch(
                "/v1/backends/llama3-test",
                json={"path": "mlx-community/some-model", "backend": "llama_cpp"},
                headers={"Authorization": "Bearer dummy"},
            )
        finally:
            _clear_override()
        assert resp.status_code == 400


# ── DELETE /v1/backends/{id} (deregister) ────────────────────────────────────


class TestDeregister:
    def test_deregister_requires_auth(self, tmp_path: Path):
        client = _make_client(tmp_path)
        resp = client.delete("/v1/backends/llama3-test")
        assert resp.status_code == 401

    def test_deregister_unknown_model_returns_404(self, tmp_path: Path):
        client = _authed(_make_client(tmp_path))
        try:
            resp = client.delete(
                "/v1/backends/does-not-exist", headers={"Authorization": "Bearer dummy"}
            )
        finally:
            _clear_override()
        assert resp.status_code == 404

    def test_deregister_success_removes_from_registry(self, tmp_path: Path):
        client = _authed(_make_client(tmp_path))
        try:
            resp = client.delete(
                "/v1/backends/llama3-test", headers={"Authorization": "Bearer dummy"}
            )
        finally:
            _clear_override()
        assert resp.status_code == 204
        assert app.state.registry.get("llama3-test") is None


# ── POST /v1/backends/{id}/start|stop|restart ────────────────────────────────


class TestLifecycleControl:
    def _mock_process_state(self) -> ProcessState:
        return ProcessState(
            pid=4242,
            model_id="llama3-test",
            alias="llama3-test",
            port=8080,
            model_path="/models/llama3.gguf",
            host="127.0.0.1",
            state="ready",
            cpu_percent=1.0,
            rss_mb=256.0,
            started_at=datetime.now(tz=UTC),
            managed=True,
        )

    def test_start_requires_auth(self, tmp_path: Path):
        client = _make_client(tmp_path)
        resp = client.post("/v1/backends/llama3-test/start")
        assert resp.status_code == 401

    def test_start_unknown_model_returns_404(self, tmp_path: Path):
        client = _authed(_make_client(tmp_path))
        try:
            resp = client.post(
                "/v1/backends/does-not-exist/start", headers={"Authorization": "Bearer dummy"}
            )
        finally:
            _clear_override()
        assert resp.status_code == 404

    def test_start_success_returns_merged_state(self, tmp_path: Path):
        client = _authed(_make_client(tmp_path))
        try:
            with (
                patch("prometheus_manager_api.control.start_instance", return_value=None),
                patch(
                    "prometheus_manager_api.control.scan",
                    return_value=[self._mock_process_state()],
                ),
            ):
                resp = client.post(
                    "/v1/backends/llama3-test/start", headers={"Authorization": "Bearer dummy"}
                )
        finally:
            _clear_override()
        assert resp.status_code == 200
        body = resp.json()
        assert body["id"] == "llama3-test"
        assert body["state"] == "ready"
        assert body["pid"] == 4242

    def test_start_lifecycle_error_returns_409(self, tmp_path: Path):
        client = _authed(_make_client(tmp_path))
        try:
            with patch(
                "prometheus_manager_api.control.start_instance",
                side_effect=LifecycleError("already running"),
            ):
                resp = client.post(
                    "/v1/backends/llama3-test/start", headers={"Authorization": "Bearer dummy"}
                )
        finally:
            _clear_override()
        assert resp.status_code == 409
        assert resp.json()["detail"]["type"].endswith("lifecycle-conflict")

    def test_start_binary_not_found_returns_clean_500(self, tmp_path: Path):
        """subprocess.Popen failures (e.g. a mistyped/missing binary path,
        including the '~' expansion bug this test guards against
        regressing) must not leak as a raw, undetailed 500."""
        client = _authed(_make_client(tmp_path))
        try:
            with patch(
                "prometheus_manager_api.control.start_instance",
                side_effect=FileNotFoundError(
                    2, "No such file or directory", "~/.local/bin/llama-server"
                ),
            ):
                resp = client.post(
                    "/v1/backends/llama3-test/start", headers={"Authorization": "Bearer dummy"}
                )
        finally:
            _clear_override()
        assert resp.status_code == 500
        body = resp.json()
        assert body["detail"]["type"].endswith("backend-launch-error")
        assert "llama-server" in body["detail"]["detail"]

    def test_stop_success(self, tmp_path: Path):
        client = _authed(_make_client(tmp_path))
        try:
            with (
                patch("prometheus_manager_api.control.stop_instance", return_value=None),
                patch("prometheus_manager_api.control.scan", return_value=[]),
            ):
                resp = client.post(
                    "/v1/backends/llama3-test/stop", headers={"Authorization": "Bearer dummy"}
                )
        finally:
            _clear_override()
        assert resp.status_code == 200
        assert resp.json()["state"] == "stopped"

    def test_restart_success(self, tmp_path: Path):
        client = _authed(_make_client(tmp_path))
        try:
            with (
                patch("prometheus_manager_api.control.restart_instance", return_value=None),
                patch(
                    "prometheus_manager_api.control.scan",
                    return_value=[self._mock_process_state()],
                ),
            ):
                resp = client.post(
                    "/v1/backends/llama3-test/restart", headers={"Authorization": "Bearer dummy"}
                )
        finally:
            _clear_override()
        assert resp.status_code == 200
        assert resp.json()["state"] == "ready"
