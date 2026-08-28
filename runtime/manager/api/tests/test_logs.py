"""Tests for GET /v1/backends/{model_id}/logs — RM-13 (live log viewer)."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from prometheus_manager_core.config import (
    ApiConfig,
    DashboardConfig,
    DownloadsConfig,
    ManagerConfig,
    RegistryConfig,
    ServerConfig,
)
from prometheus_manager_core.registry import Registry, RegistryEntry

from prometheus_manager_api.app import app
from prometheus_manager_api.auth import require_backend_registry_read

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
    app.dependency_overrides[require_backend_registry_read] = lambda: {
        "sub": "operator",
        "scope": "backend-registry:read",
    }
    return client


def _clear_override():
    app.dependency_overrides.pop(require_backend_registry_read, None)


class TestBackendLogs:
    def test_requires_auth(self, tmp_path: Path):
        client = _make_client(tmp_path)
        resp = client.get("/v1/backends/llama3-test/logs")
        assert resp.status_code == 401

    def test_unknown_model_returns_404(self, tmp_path: Path):
        client = _authed(_make_client(tmp_path))
        try:
            resp = client.get("/v1/backends/does-not-exist/logs")
        finally:
            _clear_override()
        assert resp.status_code == 404

    def test_no_log_file_yet_returns_empty_lines(self, tmp_path: Path):
        client = _authed(_make_client(tmp_path))
        try:
            resp = client.get("/v1/backends/llama3-test/logs")
        finally:
            _clear_override()
        assert resp.status_code == 200
        assert resp.json() == {"model_id": "llama3-test", "lines": []}

    def test_returns_tail_of_log_file(self, tmp_path: Path):
        client = _authed(_make_client(tmp_path))
        log_dir = tmp_path / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        (log_dir / "llama3-test.log").write_text(
            "\n".join(f"line {i}" for i in range(1, 11)) + "\n"
        )
        try:
            resp = client.get("/v1/backends/llama3-test/logs", params={"tail": 3})
        finally:
            _clear_override()
        assert resp.status_code == 200
        assert resp.json() == {"model_id": "llama3-test", "lines": ["line 8", "line 9", "line 10"]}

    def test_tail_defaults_to_200_and_is_capped_at_2000(self, tmp_path: Path):
        client = _authed(_make_client(tmp_path))
        try:
            resp = client.get("/v1/backends/llama3-test/logs", params={"tail": 5000})
        finally:
            _clear_override()
        assert resp.status_code == 422
