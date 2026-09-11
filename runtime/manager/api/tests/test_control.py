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
        # RM-51 regression: the immediate POST response must reflect the
        # persisted catalog FK (self-referencing for manual registration),
        # not the stale in-memory entry built before registry.add().
        assert body["model_id"] == "new-model"

    def test_register_split_file_fields(self, tmp_path: Path):
        """RM-52: vae_path/clip_l_path/t5xxl_path — FLUX.1-class split models.
        Regression: register_backend() builds RegistryEntry field-by-field
        rather than **body, so a new field is easy to add here and forget
        to wire into that constructor call."""
        client = _authed(_make_client(tmp_path))
        try:
            resp = client.post(
                "/v1/backends",
                json={
                    "id": "flux-model",
                    "port": 8199,
                    "path": "/models/flux1-dev-q8_0.gguf",
                    "context_length": 0,
                    "backend": "sd_cpp",
                    "modality": "image",
                    "vae_path": "/models/ae.safetensors",
                    "clip_l_path": "/models/clip_l.safetensors",
                    "t5xxl_path": "/models/t5xxl.safetensors",
                },
                headers={"Authorization": "Bearer dummy"},
            )
        finally:
            _clear_override()
        assert resp.status_code == 201
        body = resp.json()
        assert body["vae_path"] == "/models/ae.safetensors"
        assert body["clip_l_path"] == "/models/clip_l.safetensors"
        assert body["t5xxl_path"] == "/models/t5xxl.safetensors"

    def test_register_and_update_cfg_scale(self, tmp_path: Path):
        """RM-52: sd-server's own --cfg-scale default (7.0) is wrong for
        guidance-distilled models (FLUX.1) — confirmed empirically."""
        client = _authed(_make_client(tmp_path))
        try:
            resp = client.post(
                "/v1/backends",
                json={
                    "id": "flux-model",
                    "port": 8199,
                    "path": "/models/flux1-dev-q8_0.gguf",
                    "backend": "sd_cpp",
                    "cfg_scale": 1.0,
                },
                headers={"Authorization": "Bearer dummy"},
            )
            assert resp.status_code == 201
            assert resp.json()["cfg_scale"] == 1.0

            patch_resp = client.patch(
                "/v1/backends/flux-model",
                json={"cfg_scale": 3.5},
                headers={"Authorization": "Bearer dummy"},
            )
            assert patch_resp.status_code == 200
            assert patch_resp.json()["cfg_scale"] == 3.5
            assert app.state.registry.get("flux-model").cfg_scale == 3.5
        finally:
            _clear_override()

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


class TestRegisterInstanceFromCatalog:
    """RM-51: an optional `model_id` field creates a new instance of an
    already-catalogued model instead of registering a brand-new one."""

    def test_register_with_model_id_creates_instance(self, tmp_path: Path):
        client = _authed(_make_client(tmp_path))
        try:
            registry: Registry = app.state.registry
            registry.get("llama3-test")  # sanity: the seeded instance/catalog exist
            resp = client.post(
                "/v1/backends",
                json={
                    "id": "llama3-test-2",
                    "model_id": "llama3-test",
                    "port": 8081,
                    "discovery": True,
                },
                headers={"Authorization": "Bearer dummy"},
            )
        finally:
            _clear_override()
        assert resp.status_code == 201
        body = resp.json()
        assert body["id"] == "llama3-test-2"
        assert body["model_id"] == "llama3-test"
        assert body["path"] == "/models/llama3.gguf"  # carried from the catalog
        # The original instance and its catalog entry are untouched.
        assert app.state.registry.get("llama3-test") is not None
        assert app.state.registry.get_catalog("llama3-test") is not None

    def test_register_with_unknown_model_id_returns_404(self, tmp_path: Path):
        client = _authed(_make_client(tmp_path))
        try:
            resp = client.post(
                "/v1/backends",
                json={"id": "orphan-instance", "model_id": "nonexistent", "port": 8082},
                headers={"Authorization": "Bearer dummy"},
            )
        finally:
            _clear_override()
        assert resp.status_code == 404

    def test_register_with_model_id_invalid_backend_returns_400(self, tmp_path: Path):
        client = _authed(_make_client(tmp_path))
        try:
            resp = client.post(
                "/v1/backends",
                json={
                    "id": "bad-backend-instance",
                    "model_id": "llama3-test",
                    "port": 8083,
                    "backend": "not-a-real-backend",
                },
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

    def test_update_split_file_fields_persists_and_validates(self, tmp_path: Path):
        """RM-52: vae_path/clip_l_path/t5xxl_path are PATCH-able and path-
        traversal-validated the same way `path` already is."""
        client = _authed(_make_client(tmp_path))
        try:
            app.state.registry.add(
                RegistryEntry(
                    id="flux-model",
                    port=8199,
                    context_length=0,
                    path="/models/flux1-dev-q8_0.gguf",
                    backend="sd_cpp",
                    modality="image",
                )
            )
            resp = client.patch(
                "/v1/backends/flux-model",
                json={"vae_path": "/models/ae.safetensors"},
                headers={"Authorization": "Bearer dummy"},
            )
            assert resp.status_code == 200
            assert resp.json()["vae_path"] == "/models/ae.safetensors"
            assert app.state.registry.get("flux-model").vae_path == "/models/ae.safetensors"

            bad_resp = client.patch(
                "/v1/backends/flux-model",
                json={"clip_l_path": "../../etc/passwd"},
                headers={"Authorization": "Bearer dummy"},
            )
            assert bad_resp.status_code == 400
        finally:
            _clear_override()

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
        # RM-51 regression: _control_action must pass entry.to_dict(), not
        # entry.__dict__ — a bare dataclass __dict__ silently drops
        # @property fields (backend_url) and would also drop model_id.
        assert body["model_id"] == "llama3-test"
        assert body["backend_url"] == "http://127.0.0.1:8080"

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


class TestModelIdentity:
    """RM-70: the catalog's public slug and display name over the API."""

    def test_registering_with_a_slug_sets_the_public_name(self, tmp_path: Path):
        client = _authed(_make_client(tmp_path))
        try:
            resp = client.post(
                "/v1/backends",
                json={
                    "id": "qwen3-06b-a",
                    "port": 8099,
                    "path": "/models/qwen3.gguf",
                    "slug": "qwen3-0.6b",
                    "name": "Qwen3 0.6B Instruct",
                },
                headers={"Authorization": "Bearer dummy"},
            )
            catalog = app.state.registry.get_catalog("qwen3-06b-a")
        finally:
            _clear_override()
        assert resp.status_code == 201
        assert catalog is not None
        assert catalog.slug == "qwen3-0.6b"
        assert catalog.name == "Qwen3 0.6B Instruct"
        # The merged instance view carries it, which is how the gateway groups.
        assert resp.json()["model_slug"] == "qwen3-0.6b"

    def test_registering_without_a_slug_falls_back_to_the_id(self, tmp_path: Path):
        client = _authed(_make_client(tmp_path))
        try:
            resp = client.post(
                "/v1/backends",
                json={"id": "plain-model", "port": 8098, "path": "/models/p.gguf"},
                headers={"Authorization": "Bearer dummy"},
            )
        finally:
            _clear_override()
        assert resp.status_code == 201
        assert resp.json()["model_slug"] == "plain-model"

    def test_a_duplicate_slug_is_rejected(self, tmp_path: Path):
        client = _authed(_make_client(tmp_path))
        try:
            client.post(
                "/v1/backends",
                json={"id": "first", "port": 8097, "path": "/m/a.gguf", "slug": "shared"},
                headers={"Authorization": "Bearer dummy"},
            )
            resp = client.post(
                "/v1/backends",
                json={"id": "second", "port": 8096, "path": "/m/b.gguf", "slug": "shared"},
                headers={"Authorization": "Bearer dummy"},
            )
        finally:
            _clear_override()
        assert resp.status_code == 400

    def test_changing_a_slug_is_refused_rather_than_ignored(self, tmp_path: Path):
        """Silently dropping it would read as a successful rename, and the
        caller would believe clients could route on the new name.
        """
        client = _authed(_make_client(tmp_path))
        try:
            resp = client.patch(
                "/v1/backends/llama3-test",
                json={"slug": "something-else"},
                headers={"Authorization": "Bearer dummy"},
            )
            catalog = app.state.registry.get_catalog("llama3-test")
        finally:
            _clear_override()
        assert resp.status_code == 400
        assert catalog is not None
        assert catalog.slug == "llama3-test"

    def test_the_display_name_can_be_changed(self, tmp_path: Path):
        client = _authed(_make_client(tmp_path))
        try:
            resp = client.patch(
                "/v1/backends/llama3-test",
                json={"name": "Llama 3 (production)"},
                headers={"Authorization": "Bearer dummy"},
            )
            catalog = app.state.registry.get_catalog("llama3-test")
        finally:
            _clear_override()
        assert resp.status_code == 200
        assert catalog is not None
        assert catalog.name == "Llama 3 (production)"

    def test_adding_a_replica_without_an_id_derives_one(self, tmp_path: Path):
        """RM-70: going from one instance to two should be a port and a node,
        not an exercise in inventing a globally unique string."""
        client = _authed(_make_client(tmp_path))
        try:
            first = client.post(
                "/v1/backends",
                json={"model_id": "llama3-test", "port": 8081},
                headers={"Authorization": "Bearer dummy"},
            )
            second = client.post(
                "/v1/backends",
                json={"model_id": "llama3-test", "port": 8082},
                headers={"Authorization": "Bearer dummy"},
            )
        finally:
            _clear_override()
        assert first.status_code == 201
        assert second.status_code == 201
        assert first.json()["id"] == "llama3-test-2"
        assert second.json()["id"] == "llama3-test-3"
        # And each gets its own label within the model, assigned the same way.
        assert first.json()["label"] == "#2"
        assert second.json()["label"] == "#3"

    def test_an_explicit_instance_id_is_still_honoured(self, tmp_path: Path):
        client = _authed(_make_client(tmp_path))
        try:
            resp = client.post(
                "/v1/backends",
                json={"id": "my-own-name", "model_id": "llama3-test", "port": 8081},
                headers={"Authorization": "Bearer dummy"},
            )
        finally:
            _clear_override()
        assert resp.status_code == 201
        assert resp.json()["id"] == "my-own-name"
