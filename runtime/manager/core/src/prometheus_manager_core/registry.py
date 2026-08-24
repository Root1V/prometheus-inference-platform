"""Registry CRUD — runtime/manager/registry.yaml.

Implements: memory/specs/008-llama-server-manager.md — AC-3, AC-15, AC-16, AC-17, AC-18
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,62}[a-z0-9]$")


@dataclass
class RegistryEntry:
    id: str
    context_length: int
    port: int
    path: str = ""
    family: str = ""
    quantization: str = ""
    log_level: str = "info"
    downloaded: bool = False
    discovery: bool = False  # See: memory/specs/010-registry-view-redesign.md
    rss_estimate_mb: int | None = None
    backend_url: str = ""
    hf_repo: str = ""
    hf_filename: str = ""
    hf_sha256: str = ""
    # For multi-part sharded models; empty for single-file models.
    hf_filenames: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.backend_url:
            self.backend_url = f"http://127.0.0.1:{self.port}"

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "id": self.id,
            "port": self.port,
            "context_length": self.context_length,
        }
        for attr in (
            "path",
            "family",
            "quantization",
            "log_level",
            "downloaded",
            "discovery",
            "rss_estimate_mb",
            "backend_url",
            "hf_repo",
            "hf_filename",
            "hf_sha256",
            "hf_filenames",
        ):
            val = getattr(self, attr)
            if val not in (None, "", False, []) or attr in ("downloaded", "discovery"):
                d[attr] = val
        return d


class Registry:
    """Load and persist registry.yaml.

    Implements: memory/specs/008-llama-server-manager.md — AC-3, AC-15, AC-16, AC-18
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._entries: dict[str, RegistryEntry] = {}
        if path.exists():
            self._load()

    # ── public API ────────────────────────────────────────────────────────────

    @property
    def entries(self) -> list[RegistryEntry]:
        return list(self._entries.values())

    def get(self, model_id: str) -> RegistryEntry | None:
        return self._entries.get(model_id)

    def add(self, entry: RegistryEntry) -> None:
        """Validate and add entry; persist to disk."""
        _validate_id(entry.id)
        _validate_path(entry.path)
        _validate_port(entry.port)
        self._entries[entry.id] = entry
        self._save()

    def update(self, model_id: str, **kwargs: Any) -> None:
        """Patch fields on an existing entry and persist."""
        entry = self._entries[model_id]
        for key, val in kwargs.items():
            setattr(entry, key, val)
        self._save()

    def remove(self, model_id: str) -> None:
        """Remove entry from registry.

        Implements: memory/specs/008-llama-server-manager.md — AC-18
        """
        del self._entries[model_id]
        self._save()

    def reload(self) -> None:
        self._load()

    # ── persistence ──────────────────────────────────────────────────────────

    def _load(self) -> None:
        with open(self._path) as fh:
            data = yaml.safe_load(fh) or {}
        self._entries = {}
        for raw in data.get("models", []):
            entry = RegistryEntry(
                id=raw["id"],
                path=raw.get("path", ""),
                context_length=raw.get("context_length", 4096),
                port=raw.get("port", 8080),
                family=raw.get("family", ""),
                quantization=raw.get("quantization", ""),
                log_level=raw.get("log_level", "info"),
                downloaded=raw.get("downloaded", False),
                discovery=raw.get("discovery", False),
                rss_estimate_mb=raw.get("rss_estimate_mb"),
                backend_url=raw.get("backend_url", ""),
                hf_repo=raw.get("hf_repo", ""),
                hf_filename=raw.get("hf_filename", ""),
                hf_sha256=raw.get("hf_sha256", ""),
                hf_filenames=raw.get("hf_filenames", []),
            )
            self._entries[entry.id] = entry

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        data = {"models": [e.to_dict() for e in self._entries.values()]}
        with open(self._path, "w") as fh:
            yaml.safe_dump(data, fh, default_flow_style=False, allow_unicode=True)


# ── validators ────────────────────────────────────────────────────────────────


def _validate_id(model_id: str) -> None:
    """Implements: memory/specs/008-llama-server-manager.md — AC-16"""
    if not _ID_RE.match(model_id):
        raise ValueError(
            f"Invalid model ID {model_id!r}. Must match ^[a-z0-9][a-z0-9_-]{{1,62}}[a-z0-9]$"
        )


def _validate_path(path: str) -> None:
    """Implements: memory/specs/008-llama-server-manager.md — AC-15"""
    if not path:
        return  # path may be empty before download
    resolved = Path(path).resolve()
    if ".." in Path(path).parts:
        raise ValueError(f"Path traversal detected in model path: {path!r}")
    if resolved.suffix.lower() != ".gguf":
        raise ValueError(f"Model path must point to a .gguf file, got: {path!r}")


def _validate_port(port: int) -> None:
    if not (1024 <= port <= 65535):
        raise ValueError(f"Port must be in range 1024–65535, got: {port}")
