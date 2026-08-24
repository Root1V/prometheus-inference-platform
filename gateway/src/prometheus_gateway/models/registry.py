"""Model registry loader.

Loads runtime/models/registry.yaml and provides lookup by model id.
Implements: memory/specs/001-gateway-core.md — AC-5 (unknown model → 400)
Implements: memory/specs/006-multi-model-gateway.md — AC-1, AC-9
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

import yaml

from ..telemetry import get_logger

logger = get_logger(__name__)

# Default path: resolve from repo root (two levels up from gateway/src/)
_DEFAULT_REGISTRY_PATH = Path(__file__).parents[4] / "runtime" / "models" / "registry.yaml"

# Implements: memory/specs/006-multi-model-gateway.md — AC-9 (loopback-only enforcement)
_ALLOWED_BACKEND_HOSTS: frozenset[str] = frozenset(
    {"127.0.0.1", "::1", "host.docker.internal", "host.containers.internal"}
)


@dataclass(frozen=True)
class ModelEntry:
    id: str
    path: str
    context_length: int
    family: str
    quantization: str
    backend_url: str | None = (
        None  # Implements: memory/specs/006-multi-model-gateway.md — Data Model
    )
    backend_status: Literal["active", "inactive", "invalid"] = "inactive"
    discovery: bool = True  # Implements: memory/specs/013-web-chat-ui-proxy.md — AC-3, AC-17, AC-18


class ModelRegistry:
    """In-memory model registry loaded from registry.yaml.

    The source of truth at runtime is the Manager REST API (MANAGER_URL).
    ManagerRegistrySync polls /v1/backends and calls _models directly.
    This class is used as the static fallback when MANAGER_URL is not set.
    """

    def __init__(self, path: Path | str | None = None) -> None:
        registry_path = Path(path) if path else _DEFAULT_REGISTRY_PATH
        self._models: dict[str, ModelEntry] = {}
        self._load(registry_path)

    def _load(self, path: Path) -> None:
        with path.open() as f:
            data = yaml.safe_load(f)
        for entry in data.get("models", []):
            raw_url: str | None = entry.get("backend_url")
            backend_url: str | None = None
            backend_status: Literal["active", "inactive", "invalid"] = "inactive"

            if raw_url is not None:
                # Implements: memory/specs/006-multi-model-gateway.md — AC-9
                parsed = urlparse(raw_url)
                if parsed.hostname in _ALLOWED_BACKEND_HOSTS:
                    backend_url = raw_url
                    backend_status = "active"
                else:
                    logger.error(
                        "registry.invalid_backend_url",
                        extra={"id": entry["id"], "url": raw_url, "hostname": parsed.hostname},
                    )
                    backend_status = "invalid"

            model = ModelEntry(
                id=entry["id"],
                path=entry["path"],
                context_length=int(entry["context_length"]),
                family=entry["family"],
                quantization=entry["quantization"],
                backend_url=backend_url,
                backend_status=backend_status,
                discovery=bool(entry.get("discovery", True)),
            )
            self._models[model.id] = model
        logger.info("registry.loaded", extra={"count": len(self._models)})

    def get(self, model_id: str) -> ModelEntry | None:
        return self._models.get(model_id)

    def list_models(self) -> list[ModelEntry]:
        """Return all registered models (active, inactive, and invalid)."""
        return list(self._models.values())

    def list_active_models(self) -> list[ModelEntry]:
        """Return only models with a valid backend_url (active).

        Implements: memory/specs/006-multi-model-gateway.md — AC-1
        """
        return [m for m in self._models.values() if m.backend_url is not None]


def load_registry(path: Path | str | None = None) -> ModelRegistry:
    """Convenience factory — creates and returns a ModelRegistry."""
    return ModelRegistry(path)
