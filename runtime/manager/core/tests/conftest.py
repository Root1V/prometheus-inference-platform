"""Test fixtures for prometheus_manager tests."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from prometheus_manager_core.config import (
    ApiConfig,
    DashboardConfig,
    DownloadsConfig,
    ManagerConfig,
    RegistryConfig,
    ServerConfig,
)
from prometheus_manager_core.registry import Registry, RegistryEntry

# ── Config fixtures ────────────────────────────────────────────────────────────


@pytest.fixture
def tmp_dir(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture
def default_config(tmp_path: Path) -> ManagerConfig:
    """ManagerConfig backed entirely by tmp_path directories."""
    return ManagerConfig(
        api=ApiConfig(),
        server=ServerConfig(
            binary="/usr/bin/echo",  # safe no-op for lifecycle tests
            host="127.0.0.1",
            log_dir=str(tmp_path / "logs"),
            pid_dir=str(tmp_path / "run"),
            start_timeout_s=5,
            stop_timeout_s=5,
        ),
        registry=RegistryConfig(path=str(tmp_path / "registry.db")),
        downloads=DownloadsConfig(dir=str(tmp_path / "models")),
        dashboard=DashboardConfig(refresh_interval_s=2),
    )


# ── Registry fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def registry_path(tmp_path: Path) -> Path:
    return tmp_path / "registry.db"


@pytest.fixture
def empty_registry(registry_path: Path) -> Registry:
    return Registry(registry_path)


@pytest.fixture
def sample_entry() -> RegistryEntry:
    return RegistryEntry(
        id="test-model",
        path="/models/test-model.gguf",
        context_length=4096,
        port=9090,
        family="llama",
        quantization="Q4_0",
    )


@pytest.fixture
def populated_registry(empty_registry: Registry, sample_entry: RegistryEntry) -> Registry:
    empty_registry.add(sample_entry)
    return empty_registry


# ── Process state fixtures ─────────────────────────────────────────────────────


@pytest.fixture
def mock_process_state():
    from prometheus_manager_core.scanner import ProcessState

    return ProcessState(
        pid=12345,
        model_id="test-model",
        alias="test-model",
        port=9090,
        model_path="/models/test-model.gguf",
        host="127.0.0.1",
        state="ready",
        cpu_percent=5.0,
        rss_mb=1024.0,
        started_at=datetime.now(tz=UTC),
        managed=True,
    )
