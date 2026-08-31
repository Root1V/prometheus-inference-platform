"""Tests for Manager REST API: AC-11, AC-12, AC-13."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.testclient import TestClient
from prometheus_manager_core.registry import Registry, RegistryEntry
from prometheus_manager_core.scanner import ProcessState

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
            discovery=True,  # spec 010: must be true to appear in /v1/backends
        )
    )
    return reg


def _make_client(tmp_path: Path) -> TestClient:
    reg = _make_registry(tmp_path)
    app.state.registry = reg
    app.state.pid_dir = tmp_path / "run"
    app.state.jwks_url = "http://localhost:9000/v1/jwks"
    return TestClient(app, raise_server_exceptions=True)


# ── AC-11: /health endpoint ────────────────────────────────────────────────────


class TestHealthEndpoint:
    """AC-11: GET /health returns 200 without authentication."""

    def test_AC11_health_no_auth(self, tmp_path: Path):
        """AC-11: /health accessible without token."""
        client = _make_client(tmp_path)
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


# ── AC-12: JWT validation ──────────────────────────────────────────────────────


class TestJWTValidation:
    """AC-12: /v1/backends requires a valid RS256 JWT."""

    def test_AC12_missing_token_returns_401(self, tmp_path: Path):
        """AC-12: no Authorization header → 401."""
        client = _make_client(tmp_path)
        resp = client.get("/v1/backends")
        assert resp.status_code == 401

    def test_AC12_invalid_token_returns_401(self, tmp_path: Path):
        """AC-12: malformed token → 401."""
        client = _make_client(tmp_path)
        resp = client.get("/v1/backends", headers={"Authorization": "Bearer not.a.jwt"})
        assert resp.status_code == 401

    def test_AC12_problem_details_format(self, tmp_path: Path):
        """AC-12: 401 response uses RFC 9457 Problem Details format."""
        client = _make_client(tmp_path)
        resp = client.get("/v1/backends")
        body = resp.json()
        assert "detail" in body or "title" in body or "type" in body


# ── AC-13: Scope enforcement ───────────────────────────────────────────────────


class TestScopeEnforcement:
    """AC-13: backend-registry:read scope is required."""

    def test_AC13_wrong_scope_returns_403(self, tmp_path: Path):
        """AC-13: valid JWT but wrong scope → 403."""
        client = _make_client(tmp_path)

        def _raise_403():
            raise HTTPException(
                status_code=403,
                detail={
                    "type": "https://prometheus.local/errors/forbidden",
                    "title": "Forbidden",
                    "status": 403,
                    "detail": "Scope 'backend-registry:read' is required.",
                },
            )

        app.dependency_overrides[require_backend_registry_read] = _raise_403
        try:
            resp = client.get("/v1/backends", headers={"Authorization": "Bearer dummy"})
        finally:
            app.dependency_overrides.pop(require_backend_registry_read, None)

        assert resp.status_code == 403

    def test_AC13_correct_scope_returns_list(self, tmp_path: Path):
        """AC-13: valid JWT with correct scope → 200 with backends list."""
        client = _make_client(tmp_path)

        mock_ps = ProcessState(
            pid=1234,
            model_id="llama3-test",
            alias="llama3-test",
            port=8080,
            model_path="/models/llama3.gguf",
            host="127.0.0.1",
            state="ready",
            cpu_percent=5.0,
            rss_mb=512.0,
            started_at=datetime.now(tz=UTC),
            managed=True,
        )

        app.dependency_overrides[require_backend_registry_read] = lambda: {
            "sub": "gateway",
            "scope": "backend-registry:read",
        }
        try:
            with patch("prometheus_manager_api.routes.scan", return_value=[mock_ps]):
                resp = client.get("/v1/backends", headers={"Authorization": "Bearer valid"})
        finally:
            app.dependency_overrides.pop(require_backend_registry_read, None)

        assert resp.status_code == 200
        body = resp.json()
        assert "backends" in body
        assert len(body["backends"]) == 1
        assert body["backends"][0]["id"] == "llama3-test"

    def test_AC13_get_single_backend_requires_auth(self, tmp_path: Path):
        """AC-13: GET /v1/backends/{id} without auth → 401."""
        client = _make_client(tmp_path)
        resp = client.get("/v1/backends/llama3-test")
        assert resp.status_code == 401

    def test_AC13_get_single_backend_404_for_unknown(self, tmp_path: Path):
        """AC-11: GET /v1/backends/{id} for unknown model → 404."""
        client = _make_client(tmp_path)

        app.dependency_overrides[require_backend_registry_read] = lambda: {
            "sub": "gateway",
            "scope": "backend-registry:read",
        }
        try:
            with patch("prometheus_manager_api.routes.scan", return_value=[]):
                resp = client.get(
                    "/v1/backends/no-such-model",
                    headers={"Authorization": "Bearer valid"},
                )
        finally:
            app.dependency_overrides.pop(require_backend_registry_read, None)

        assert resp.status_code == 404


# ── spec-010 AC-18: discovery filtering ────────────────────────────────────────


class TestDiscoveryFiltering:
    """memory/specs/010 AC-18: /v1/backends only returns entries with discovery: true."""

    def test_AC18_only_discovery_true_returned(self, tmp_path: Path):
        """AC-18: 2 of 4 entries with discovery=true → exactly 2 backends returned."""
        reg = Registry(tmp_path / "registry.yaml")
        for i in range(4):
            reg.add(
                RegistryEntry(
                    id=f"model-{i:02d}",
                    path=f"/models/model-{i}.gguf",
                    port=8080 + i,
                    context_length=4096,
                    discovery=(i < 2),  # first two discoverable
                )
            )

        app.state.registry = reg
        app.state.pid_dir = tmp_path / "run"
        client = TestClient(app, raise_server_exceptions=True)

        app.dependency_overrides[require_backend_registry_read] = lambda: {
            "sub": "gateway",
            "scope": "backend-registry:read",
        }
        try:
            with patch("prometheus_manager_api.routes.scan", return_value=[]):
                resp = client.get("/v1/backends", headers={"Authorization": "Bearer valid"})
        finally:
            app.dependency_overrides.pop(require_backend_registry_read, None)

        assert resp.status_code == 200
        body = resp.json()
        assert len(body["backends"]) == 2
        ids = {b["id"] for b in body["backends"]}
        assert ids == {"model-00", "model-01"}

    def test_AC18_no_discovery_returns_empty_list(self, tmp_path: Path):
        """AC-18: no entries with discovery=true → empty backends list."""
        reg = Registry(tmp_path / "registry.yaml")
        reg.add(
            RegistryEntry(
                id="hidden-model",
                path="/models/hidden.gguf",
                port=8080,
                context_length=4096,
                discovery=False,
            )
        )

        app.state.registry = reg
        app.state.pid_dir = tmp_path / "run"
        client = TestClient(app, raise_server_exceptions=True)

        app.dependency_overrides[require_backend_registry_read] = lambda: {
            "sub": "gateway",
            "scope": "backend-registry:read",
        }
        try:
            with patch("prometheus_manager_api.routes.scan", return_value=[]):
                resp = client.get("/v1/backends", headers={"Authorization": "Bearer valid"})
        finally:
            app.dependency_overrides.pop(require_backend_registry_read, None)

        assert resp.status_code == 200
        assert resp.json()["backends"] == []


class TestErrorState:
    """RM-10: a model whose last start attempt crashed reports state="error"
    (not "stopped") once the dead process is gone, via a {id}.error marker
    lifecycle.py writes — see routes.py's _merge()."""

    def test_crashed_model_reports_error_state_with_message(self, tmp_path: Path):
        reg = Registry(tmp_path / "registry.yaml")
        reg.add(
            RegistryEntry(
                id="crashed-model",
                path="/models/crashed.gguf",
                port=8080,
                context_length=4096,
                discovery=True,
            )
        )
        pid_dir = tmp_path / "run"
        pid_dir.mkdir(parents=True)
        (pid_dir / "crashed-model.error").write_text(
            "Process for crashed-model exited unexpectedly (code 1)"
        )

        app.state.registry = reg
        app.state.pid_dir = pid_dir
        client = TestClient(app, raise_server_exceptions=True)

        app.dependency_overrides[require_backend_registry_read] = lambda: {
            "sub": "gateway",
            "scope": "backend-registry:read",
        }
        try:
            with patch("prometheus_manager_api.routes.scan", return_value=[]):
                resp = client.get("/v1/backends", headers={"Authorization": "Bearer valid"})
        finally:
            app.dependency_overrides.pop(require_backend_registry_read, None)

        assert resp.status_code == 200
        backend = resp.json()["backends"][0]
        assert backend["state"] == "error"
        assert "exited unexpectedly" in backend["error_message"]

    def test_model_never_started_reports_stopped_not_error(self, tmp_path: Path):
        """No .error marker at all — must not be misreported as "error"."""
        reg = Registry(tmp_path / "registry.yaml")
        reg.add(
            RegistryEntry(
                id="fresh-model",
                path="/models/fresh.gguf",
                port=8080,
                context_length=4096,
                discovery=True,
            )
        )
        app.state.registry = reg
        app.state.pid_dir = tmp_path / "run"
        client = TestClient(app, raise_server_exceptions=True)

        app.dependency_overrides[require_backend_registry_read] = lambda: {
            "sub": "gateway",
            "scope": "backend-registry:read",
        }
        try:
            with patch("prometheus_manager_api.routes.scan", return_value=[]):
                resp = client.get("/v1/backends", headers={"Authorization": "Bearer valid"})
        finally:
            app.dependency_overrides.pop(require_backend_registry_read, None)

        backend = resp.json()["backends"][0]
        assert backend["state"] == "stopped"
        assert backend["error_message"] is None


class TestFileSizeBytes:
    """RM-48 follow-up: file_size_bytes on /v1/backends entries, for the
    admin dashboard's Downloaded models table."""

    def _client(self, tmp_path: Path, entry: RegistryEntry) -> TestClient:
        reg = Registry(tmp_path / "registry.yaml")
        reg.add(entry)
        app.state.registry = reg
        app.state.pid_dir = tmp_path / "run"
        client = TestClient(app, raise_server_exceptions=True)
        app.dependency_overrides[require_backend_registry_read] = lambda: {
            "sub": "gateway",
            "scope": "backend-registry:read",
        }
        return client

    def test_downloaded_single_file_reports_real_size(self, tmp_path: Path):
        gguf = tmp_path / "model.gguf"
        gguf.write_bytes(b"x" * 12345)
        client = self._client(
            tmp_path,
            RegistryEntry(
                id="downloaded-model",
                path=str(gguf),
                port=8080,
                context_length=4096,
                downloaded=True,
                discovery=True,
            ),
        )
        try:
            with patch("prometheus_manager_api.routes.scan", return_value=[]):
                resp = client.get("/v1/backends", headers={"Authorization": "Bearer valid"})
        finally:
            app.dependency_overrides.pop(require_backend_registry_read, None)

        assert resp.json()["backends"][0]["file_size_bytes"] == 12345

    def test_downloaded_sharded_model_sums_all_shards(self, tmp_path: Path):
        (tmp_path / "m-00001-of-00002.gguf").write_bytes(b"a" * 1000)
        (tmp_path / "m-00002-of-00002.gguf").write_bytes(b"b" * 2000)
        client = self._client(
            tmp_path,
            RegistryEntry(
                id="sharded-model",
                path=str(tmp_path / "m-00001-of-00002.gguf"),
                port=8080,
                context_length=4096,
                downloaded=True,
                discovery=True,
                hf_filenames=["m-00001-of-00002.gguf", "m-00002-of-00002.gguf"],
            ),
        )
        try:
            with patch("prometheus_manager_api.routes.scan", return_value=[]):
                resp = client.get("/v1/backends", headers={"Authorization": "Bearer valid"})
        finally:
            app.dependency_overrides.pop(require_backend_registry_read, None)

        assert resp.json()["backends"][0]["file_size_bytes"] == 3000

    def test_sharded_model_without_hf_filenames_sums_sibling_shards(self, tmp_path: Path):
        """RM-50: a manually-registered model (no hf_filenames — never went
        through the download flow) whose path is one shard of several must
        still report the total across all sibling shards, not just the one
        file the registry happens to point at."""
        (tmp_path / "m-00001-of-00003.gguf").write_bytes(b"a" * 1000)
        (tmp_path / "m-00002-of-00003.gguf").write_bytes(b"b" * 2000)
        (tmp_path / "m-00003-of-00003.gguf").write_bytes(b"c" * 3000)
        client = self._client(
            tmp_path,
            RegistryEntry(
                id="manual-sharded-model",
                path=str(tmp_path / "m-00001-of-00003.gguf"),
                port=8080,
                context_length=4096,
                downloaded=True,
                discovery=True,
            ),
        )
        try:
            with patch("prometheus_manager_api.routes.scan", return_value=[]):
                resp = client.get("/v1/backends", headers={"Authorization": "Bearer valid"})
        finally:
            app.dependency_overrides.pop(require_backend_registry_read, None)

        assert resp.json()["backends"][0]["file_size_bytes"] == 6000

    def test_not_downloaded_reports_none(self, tmp_path: Path):
        client = self._client(
            tmp_path,
            RegistryEntry(
                id="not-downloaded",
                path="/models/never-downloaded.gguf",
                port=8080,
                context_length=4096,
                discovery=True,
            ),
        )
        try:
            with patch("prometheus_manager_api.routes.scan", return_value=[]):
                resp = client.get("/v1/backends", headers={"Authorization": "Bearer valid"})
        finally:
            app.dependency_overrides.pop(require_backend_registry_read, None)

        assert resp.json()["backends"][0]["file_size_bytes"] is None

    def test_missing_file_reports_none_not_error(self, tmp_path: Path):
        """downloaded=True but the file was deleted out-of-band — don't 500."""
        client = self._client(
            tmp_path,
            RegistryEntry(
                id="missing-file",
                path=str(tmp_path / "deleted.gguf"),
                port=8080,
                context_length=4096,
                downloaded=True,
                discovery=True,
            ),
        )
        try:
            with patch("prometheus_manager_api.routes.scan", return_value=[]):
                resp = client.get("/v1/backends", headers={"Authorization": "Bearer valid"})
        finally:
            app.dependency_overrides.pop(require_backend_registry_read, None)

        assert resp.status_code == 200
        assert resp.json()["backends"][0]["file_size_bytes"] is None
