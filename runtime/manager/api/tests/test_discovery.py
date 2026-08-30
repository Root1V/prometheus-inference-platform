"""Tests for RM-48 — model discovery/download endpoints (discovery.py).

See docs/roadmap.md RM-48 and runtime/manager/core/src/prometheus_manager_core/
hf_discovery.py / downloader.py for the underlying logic these endpoints wrap.
"""

from __future__ import annotations

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
from prometheus_manager_core.downloader import DownloadState
from prometheus_manager_core.registry import Registry, RegistryEntry

from prometheus_manager_api.app import app
from prometheus_manager_api.auth import (
    require_backend_registry_read,
    require_backend_registry_write,
)

# ── Setup ──────────────────────────────────────────────────────────────────────


def _make_registry(tmp_path: Path) -> Registry:
    return Registry(tmp_path / "registry.yaml")


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
    app.state.downloads = []
    return TestClient(app, raise_server_exceptions=True)


def _authed_write(client: TestClient) -> TestClient:
    app.dependency_overrides[require_backend_registry_write] = lambda: {
        "sub": "operator",
        "scope": "backend-registry:write",
    }
    return client


def _authed_read(client: TestClient) -> TestClient:
    app.dependency_overrides[require_backend_registry_read] = lambda: {
        "sub": "operator",
        "scope": "backend-registry:read",
    }
    return client


def _clear_overrides() -> None:
    app.dependency_overrides.pop(require_backend_registry_write, None)
    app.dependency_overrides.pop(require_backend_registry_read, None)


_HEADERS = {"Authorization": "Bearer dummy"}


# ── GET/PATCH /v1/models/config ──────────────────────────────────────────────


class TestModelsConfig:
    def test_get_requires_auth(self, tmp_path: Path):
        client = _make_client(tmp_path)
        resp = client.get("/v1/models/config")
        assert resp.status_code == 401

    def test_get_returns_current_downloads_dir(self, tmp_path: Path):
        client = _authed_read(_make_client(tmp_path))
        try:
            resp = client.get("/v1/models/config", headers=_HEADERS)
        finally:
            _clear_overrides()
        assert resp.status_code == 200
        assert resp.json()["downloads_dir"] == str(tmp_path / "models")

    def test_patch_requires_auth(self, tmp_path: Path):
        client = _make_client(tmp_path)
        resp = client.patch("/v1/models/config", json={"downloads_dir": "/tmp/x"})
        assert resp.status_code == 401

    def test_patch_updates_downloads_dir(self, tmp_path: Path):
        client = _authed_write(_make_client(tmp_path))
        try:
            resp = client.patch(
                "/v1/models/config",
                json={"downloads_dir": "/new/models/path"},
                headers=_HEADERS,
            )
        finally:
            _clear_overrides()
        assert resp.status_code == 200
        assert resp.json()["downloads_dir"] == "/new/models/path"
        assert app.state.config.downloads.dir == "/new/models/path"

    def test_patch_rejects_empty_downloads_dir(self, tmp_path: Path):
        client = _authed_write(_make_client(tmp_path))
        try:
            resp = client.patch("/v1/models/config", json={"downloads_dir": "  "}, headers=_HEADERS)
        finally:
            _clear_overrides()
        assert resp.status_code == 400

    def test_patch_updates_hf_token_env(self, tmp_path: Path):
        client = _authed_write(_make_client(tmp_path))
        try:
            resp = client.patch(
                "/v1/models/config", json={"hf_token_env": "MY_HF_TOKEN"}, headers=_HEADERS
            )
        finally:
            _clear_overrides()
        assert resp.status_code == 200
        assert resp.json()["hf_token_env"] == "MY_HF_TOKEN"


# ── GET /v1/models/search[/files|/card] ──────────────────────────────────────


class TestSearch:
    def test_requires_auth(self, tmp_path: Path):
        client = _make_client(tmp_path)
        resp = client.get("/v1/models/search", params={"q": "llama"})
        assert resp.status_code == 401

    def test_search_success(self, tmp_path: Path):
        client = _authed_read(_make_client(tmp_path))
        try:
            with patch(
                "prometheus_manager_api.discovery.search_models",
                return_value=[
                    {
                        "id": "bartowski/Llama-3.2-1B-GGUF",
                        "downloads": 100,
                        "likes": 5,
                        "last_modified": None,
                    }
                ],
            ):
                resp = client.get("/v1/models/search", params={"q": "llama"}, headers=_HEADERS)
        finally:
            _clear_overrides()
        assert resp.status_code == 200
        assert resp.json()["results"][0]["id"] == "bartowski/Llama-3.2-1B-GGUF"

    def test_search_upstream_failure_returns_502(self, tmp_path: Path):
        client = _authed_read(_make_client(tmp_path))
        try:
            with patch(
                "prometheus_manager_api.discovery.search_models", side_effect=RuntimeError("boom")
            ):
                resp = client.get("/v1/models/search", params={"q": "llama"}, headers=_HEADERS)
        finally:
            _clear_overrides()
        assert resp.status_code == 502
        assert resp.json()["detail"]["type"].endswith("hf-search-failed")

    def test_search_files(self, tmp_path: Path):
        client = _authed_read(_make_client(tmp_path))
        try:
            with patch(
                "prometheus_manager_api.discovery.list_model_files",
                return_value=[{"filename": "model-Q4_K_M.gguf", "quantization": "Q4_K_M"}],
            ):
                resp = client.get(
                    "/v1/models/search/files",
                    params={"repo_id": "bartowski/Llama-3.2-1B-GGUF"},
                    headers=_HEADERS,
                )
        finally:
            _clear_overrides()
        assert resp.status_code == 200
        assert resp.json()["files"][0]["filename"] == "model-Q4_K_M.gguf"

    def test_search_card(self, tmp_path: Path):
        client = _authed_read(_make_client(tmp_path))
        try:
            with patch(
                "prometheus_manager_api.discovery.fetch_model_card",
                return_value={"repo_id": "x/y", "text": "# Card", "metadata": {}},
            ):
                resp = client.get(
                    "/v1/models/search/card", params={"repo_id": "x/y"}, headers=_HEADERS
                )
        finally:
            _clear_overrides()
        assert resp.status_code == 200
        assert resp.json()["text"] == "# Card"


# ── POST /v1/models/downloads (start) ────────────────────────────────────────


class TestStartDownload:
    def test_requires_auth(self, tmp_path: Path):
        client = _make_client(tmp_path)
        resp = client.post("/v1/models/downloads", json={"repo_id": "x/y", "filename": "m.gguf"})
        assert resp.status_code == 401

    def test_missing_fields_returns_400(self, tmp_path: Path):
        client = _authed_write(_make_client(tmp_path))
        try:
            resp = client.post("/v1/models/downloads", json={}, headers=_HEADERS)
        finally:
            _clear_overrides()
        assert resp.status_code == 400

    def test_filename_not_in_repo_returns_400(self, tmp_path: Path):
        client = _authed_write(_make_client(tmp_path))
        try:
            with patch(
                "prometheus_manager_api.discovery.list_model_files",
                return_value=[{"filename": "other.gguf", "quantization": "Q4_0"}],
            ):
                resp = client.post(
                    "/v1/models/downloads",
                    json={"repo_id": "x/y", "filename": "missing.gguf"},
                    headers=_HEADERS,
                )
        finally:
            _clear_overrides()
        assert resp.status_code == 400

    def test_starts_registers_entry_immediately(self, tmp_path: Path):
        """The registry entry (downloaded=False) exists as soon as the 202
        response comes back — the actual download runs in a background
        asyncio task, covered separately below via _run_download directly
        (a TestClient request tears down its event loop right after the
        response, so a fire-and-forget task isn't guaranteed to progress
        within the same call — see TestRunDownload)."""
        client = _authed_write(_make_client(tmp_path))
        try:
            with (
                patch(
                    "prometheus_manager_api.discovery.list_model_files",
                    return_value=[{"filename": "model-Q4_K_M.gguf", "quantization": "Q4_K_M"}],
                ),
                patch("prometheus_manager_api.discovery.download_model"),
            ):
                resp = client.post(
                    "/v1/models/downloads",
                    json={
                        "repo_id": "bartowski/Llama-3.2-1B-GGUF",
                        "filename": "model-Q4_K_M.gguf",
                    },
                    headers=_HEADERS,
                )
        finally:
            _clear_overrides()

        assert resp.status_code == 202
        body = resp.json()
        assert body["hf_repo"] == "bartowski/Llama-3.2-1B-GGUF"
        assert body["shard_count"] == 1
        entry = app.state.registry.get(body["model_id"])
        assert entry is not None
        assert entry.downloaded is False
        assert entry.hf_repo == "bartowski/Llama-3.2-1B-GGUF"
        assert entry.hf_filename == "model-Q4_K_M.gguf"

    def test_duplicate_model_id_returns_409(self, tmp_path: Path):
        client = _authed_write(_make_client(tmp_path))
        app.state.registry.add(RegistryEntry(id="existing-model", port=8090, context_length=4096))
        try:
            with patch(
                "prometheus_manager_api.discovery.list_model_files",
                return_value=[{"filename": "model.gguf", "quantization": "?"}],
            ):
                resp = client.post(
                    "/v1/models/downloads",
                    json={
                        "repo_id": "x/y",
                        "filename": "model.gguf",
                        "model_id": "existing-model",
                    },
                    headers=_HEADERS,
                )
        finally:
            _clear_overrides()
        assert resp.status_code == 409


# ── GET /v1/models/downloads, cancel, retry ──────────────────────────────────


class TestDownloadProgress:
    def test_list_downloads(self, tmp_path: Path):
        client = _authed_read(_make_client(tmp_path))
        app.state.downloads.append(
            DownloadState(model_id="m1", hf_repo="x/y", hf_filename="m1.gguf", status="downloading")
        )
        try:
            resp = client.get("/v1/models/downloads", headers=_HEADERS)
        finally:
            _clear_overrides()
        assert resp.status_code == 200
        assert resp.json()["downloads"][0]["model_id"] == "m1"

    def test_cancel_sets_flag_on_active_download(self, tmp_path: Path):
        client = _authed_write(_make_client(tmp_path))
        ds = DownloadState(
            model_id="m1", hf_repo="x/y", hf_filename="m1.gguf", status="downloading"
        )
        app.state.downloads.append(ds)
        try:
            resp = client.post("/v1/models/downloads/m1/cancel", headers=_HEADERS)
        finally:
            _clear_overrides()
        assert resp.status_code == 200
        assert ds.cancel_requested is True

    def test_cancel_unknown_model_returns_404(self, tmp_path: Path):
        client = _authed_write(_make_client(tmp_path))
        try:
            resp = client.post("/v1/models/downloads/no-such-model/cancel", headers=_HEADERS)
        finally:
            _clear_overrides()
        assert resp.status_code == 404

    def test_retry_requires_existing_downloadable_entry(self, tmp_path: Path):
        client = _authed_write(_make_client(tmp_path))
        try:
            resp = client.post("/v1/models/downloads/no-such-model/retry", headers=_HEADERS)
        finally:
            _clear_overrides()
        assert resp.status_code == 404

    def test_retry_rejects_manually_registered_entry_without_hf_repo(self, tmp_path: Path):
        client = _authed_write(_make_client(tmp_path))
        app.state.registry.add(
            RegistryEntry(id="local-model", port=8090, context_length=4096, path="/x/y.gguf")
        )
        try:
            resp = client.post("/v1/models/downloads/local-model/retry", headers=_HEADERS)
        finally:
            _clear_overrides()
        assert resp.status_code == 404


class TestPauseAndResume:
    def test_pause_sets_flag_on_active_download(self, tmp_path: Path):
        client = _authed_write(_make_client(tmp_path))
        ds = DownloadState(
            model_id="m1", hf_repo="x/y", hf_filename="m1.gguf", status="downloading"
        )
        app.state.downloads.append(ds)
        try:
            resp = client.post("/v1/models/downloads/m1/pause", headers=_HEADERS)
        finally:
            _clear_overrides()
        assert resp.status_code == 200
        assert ds.pause_requested is True

    def test_pause_rejects_when_nothing_active(self, tmp_path: Path):
        client = _authed_write(_make_client(tmp_path))
        try:
            resp = client.post("/v1/models/downloads/no-such-model/pause", headers=_HEADERS)
        finally:
            _clear_overrides()
        assert resp.status_code == 409

    def test_pause_rejects_already_paused(self, tmp_path: Path):
        client = _authed_write(_make_client(tmp_path))
        app.state.downloads.append(
            DownloadState(model_id="m1", hf_repo="x/y", hf_filename="m1.gguf", status="paused")
        )
        try:
            resp = client.post("/v1/models/downloads/m1/pause", headers=_HEADERS)
        finally:
            _clear_overrides()
        assert resp.status_code == 409

    def test_resume_requires_existing_downloadable_entry(self, tmp_path: Path):
        client = _authed_write(_make_client(tmp_path))
        try:
            resp = client.post("/v1/models/downloads/no-such-model/resume", headers=_HEADERS)
        finally:
            _clear_overrides()
        assert resp.status_code == 404

    def test_resume_requires_a_paused_download(self, tmp_path: Path):
        client = _authed_write(_make_client(tmp_path))
        app.state.registry.add(
            RegistryEntry(
                id="model-m1",
                port=8090,
                context_length=4096,
                hf_repo="x/y",
                hf_filename="m1.gguf",
            )
        )
        try:
            resp = client.post("/v1/models/downloads/model-m1/resume", headers=_HEADERS)
        finally:
            _clear_overrides()
        assert resp.status_code == 404

    def test_resume_starts_when_a_paused_shard_exists(self, tmp_path: Path):
        client = _authed_write(_make_client(tmp_path))
        app.state.registry.add(
            RegistryEntry(
                id="model-m1",
                port=8090,
                context_length=4096,
                hf_repo="x/y",
                hf_filename="m1.gguf",
            )
        )
        app.state.downloads.append(
            DownloadState(
                model_id="model-m1", hf_repo="x/y", hf_filename="m1.gguf", status="paused"
            )
        )
        try:
            with patch("prometheus_manager_api.discovery.download_model"):
                resp = client.post("/v1/models/downloads/model-m1/resume", headers=_HEADERS)
        finally:
            _clear_overrides()
        assert resp.status_code == 202
        assert resp.json()["model_id"] == "model-m1"


# ── DELETE /v1/models/{id}/downloaded ────────────────────────────────────────


class TestDeleteDownloaded:
    def test_requires_auth(self, tmp_path: Path):
        client = _make_client(tmp_path)
        resp = client.delete("/v1/models/some-model/downloaded")
        assert resp.status_code == 401

    def test_unknown_model_returns_404(self, tmp_path: Path):
        client = _authed_write(_make_client(tmp_path))
        try:
            resp = client.delete("/v1/models/no-such-model/downloaded", headers=_HEADERS)
        finally:
            _clear_overrides()
        assert resp.status_code == 404

    def test_not_downloaded_entry_returns_400(self, tmp_path: Path):
        client = _authed_write(_make_client(tmp_path))
        app.state.registry.add(
            RegistryEntry(id="local-model", port=8090, context_length=4096, path="/x/y.gguf")
        )
        try:
            resp = client.delete("/v1/models/local-model/downloaded", headers=_HEADERS)
        finally:
            _clear_overrides()
        assert resp.status_code == 400
        assert resp.json()["detail"]["type"].endswith("not-downloaded")

    def test_deletes_file_and_deregisters(self, tmp_path: Path):
        client = _authed_write(_make_client(tmp_path))
        downloads_dir = tmp_path / "models"
        downloads_dir.mkdir(parents=True, exist_ok=True)
        gguf_path = downloads_dir / "downloaded-model.gguf"
        gguf_path.write_bytes(b"fake gguf content")

        app.state.registry.add(
            RegistryEntry(
                id="downloaded-model",
                port=8090,
                context_length=4096,
                path=str(gguf_path),
                downloaded=True,
                hf_repo="x/y",
                hf_filename="downloaded-model.gguf",
            )
        )
        try:
            with patch("prometheus_manager_api.discovery.scan", return_value=[]):
                resp = client.delete("/v1/models/downloaded-model/downloaded", headers=_HEADERS)
        finally:
            _clear_overrides()

        assert resp.status_code == 204
        assert not gguf_path.exists()
        assert app.state.registry.get("downloaded-model") is None

    def test_blocked_while_instance_is_running(self, tmp_path: Path):
        from datetime import UTC, datetime

        from prometheus_manager_core.scanner import ProcessState

        client = _authed_write(_make_client(tmp_path))
        downloads_dir = tmp_path / "models"
        downloads_dir.mkdir(parents=True, exist_ok=True)
        gguf_path = downloads_dir / "running-model.gguf"
        gguf_path.write_bytes(b"fake gguf content")

        app.state.registry.add(
            RegistryEntry(
                id="running-model",
                port=8090,
                context_length=4096,
                path=str(gguf_path),
                downloaded=True,
                hf_repo="x/y",
                hf_filename="running-model.gguf",
            )
        )
        fake_proc = ProcessState(
            pid=123,
            model_id="running-model",
            alias="running-model",
            port=8090,
            model_path=str(gguf_path),
            host="127.0.0.1",
            state="ready",
            cpu_percent=0.0,
            rss_mb=0.0,
            started_at=datetime.now(tz=UTC),
            managed=True,
        )
        try:
            with patch("prometheus_manager_api.discovery.scan", return_value=[fake_proc]):
                resp = client.delete("/v1/models/running-model/downloaded", headers=_HEADERS)
        finally:
            _clear_overrides()

        assert resp.status_code == 409
        assert gguf_path.exists()
        assert app.state.registry.get("running-model") is not None


# ── _run_download orchestration (direct, bypassing HTTP/event-loop lifecycle) ─


class TestRunDownload:
    async def test_marks_downloaded_true_on_full_success(self, tmp_path: Path):
        from prometheus_manager_api.discovery import _kick_download

        registry = _make_registry(tmp_path)
        config = _make_config(tmp_path)
        registry.add(
            RegistryEntry(
                id="new-model",
                port=8081,
                context_length=4096,
                hf_repo="bartowski/Llama-3.2-1B-GGUF",
                hf_filename="model-Q4_K_M.gguf",
            )
        )
        downloads: list[DownloadState] = []
        dest = tmp_path / "models" / "model-Q4_K_M.gguf"

        with patch("prometheus_manager_api.discovery.download_model") as mock_download:

            def fake_download(*, on_progress, **kwargs):
                state = DownloadState(
                    model_id=kwargs["model_id"],
                    hf_repo=kwargs["hf_repo"],
                    hf_filename=kwargs["hf_filename"],
                    status="done",
                    total_bytes=100,
                    downloaded_bytes=100,
                )
                on_progress(state)
                return dest

            mock_download.side_effect = fake_download
            await _kick_download(
                downloads,
                registry,
                config,
                "new-model",
                "bartowski/Llama-3.2-1B-GGUF",
                ["model-Q4_K_M.gguf"],
                "",
            )

        entry = registry.get("new-model")
        assert entry.downloaded is True
        assert entry.path == str(dest)
        assert downloads[0].status == "done"

    async def test_failed_shard_does_not_mark_downloaded(self, tmp_path: Path):
        from prometheus_manager_core.downloader import DownloadError

        from prometheus_manager_api.discovery import _kick_download

        registry = _make_registry(tmp_path)
        config = _make_config(tmp_path)
        registry.add(
            RegistryEntry(
                id="broken-model",
                port=8081,
                context_length=4096,
                hf_repo="x/y",
                hf_filename="m.gguf",
            )
        )
        downloads: list[DownloadState] = []

        with patch(
            "prometheus_manager_api.discovery.download_model",
            side_effect=DownloadError("network error"),
        ):
            await _kick_download(downloads, registry, config, "broken-model", "x/y", ["m.gguf"], "")

        entry = registry.get("broken-model")
        assert entry.downloaded is False
        assert downloads[0].status == "failed"
        assert downloads[0].error == "network error"

    async def test_resumes_a_paused_shard_via_download_shards(self, tmp_path: Path):
        """A shard already status=='paused' is resumed (resume=True passed to
        download_model), not restarted — and shards already 'done' are
        skipped entirely rather than re-downloaded."""
        from prometheus_manager_api.discovery import _download_shards

        registry = _make_registry(tmp_path)
        config = _make_config(tmp_path)
        registry.add(
            RegistryEntry(
                id="multi-model",
                port=8081,
                context_length=4096,
                hf_repo="x/y",
                hf_filename="m-00001-of-00002.gguf",
                hf_filenames=["m-00001-of-00002.gguf", "m-00002-of-00002.gguf"],
            )
        )
        done_shard = DownloadState(
            model_id="multi-model [1/2]",
            hf_repo="x/y",
            hf_filename="m-00001-of-00002.gguf",
            status="done",
        )
        paused_shard = DownloadState(
            model_id="multi-model [2/2]",
            hf_repo="x/y",
            hf_filename="m-00002-of-00002.gguf",
            status="paused",
            downloaded_bytes=500,
        )
        downloads = [done_shard, paused_shard]

        with patch("prometheus_manager_api.discovery.download_model") as mock_download:

            def fake_download(*, on_progress, **kwargs):
                state = DownloadState(
                    model_id=kwargs["model_id"],
                    hf_repo=kwargs["hf_repo"],
                    hf_filename=kwargs["hf_filename"],
                    status="done",
                    total_bytes=1000,
                    downloaded_bytes=1000,
                )
                on_progress(state)
                return tmp_path / "models" / kwargs["hf_filename"]

            mock_download.side_effect = fake_download
            await _download_shards(downloads, registry, config, "multi-model", "x/y", downloads, "")

        # Only the paused shard was actually downloaded — the done one was skipped.
        mock_download.assert_called_once()
        assert mock_download.call_args.kwargs["resume"] is True
        assert mock_download.call_args.kwargs["hf_filename"] == "m-00002-of-00002.gguf"
        assert paused_shard.status == "done"
        entry = registry.get("multi-model")
        assert entry.downloaded is True
