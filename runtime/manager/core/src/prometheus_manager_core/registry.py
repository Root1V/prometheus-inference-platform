"""Registry CRUD — runtime/manager/registry.db (SQLite).

Implements: memory/specs/008-llama-server-manager.md — AC-3, AC-15, AC-16, AC-17, AC-18
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import sqlite3
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,62}[a-z0-9]$")

# See memory/wiki/inference-engines.md (RM-06) for the comparison behind this list.
BACKENDS = ("llama_cpp", "mlx", "vllm", "sglang")

# RM-09: what kind of requests this model serves. Determines which flags
# lifecycle.py adds to the launch command and how the gateway routes requests
# (text -> /v1/chat/completions, embedding -> /v1/embeddings, vision -> chat
# completions with image content parts). See memory/wiki/model-registry.md.
MODALITIES = ("text", "embedding", "vision")

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS models (
    id TEXT PRIMARY KEY,
    context_length INTEGER NOT NULL,
    port INTEGER NOT NULL,
    path TEXT NOT NULL DEFAULT '',
    family TEXT NOT NULL DEFAULT '',
    quantization TEXT NOT NULL DEFAULT '',
    backend TEXT NOT NULL DEFAULT 'llama_cpp',
    modality TEXT NOT NULL DEFAULT 'text',
    mmproj_path TEXT NOT NULL DEFAULT '',
    downloaded INTEGER NOT NULL DEFAULT 0,
    discovery INTEGER NOT NULL DEFAULT 0,
    rss_estimate_mb INTEGER,
    hf_repo TEXT NOT NULL DEFAULT '',
    hf_sha256 TEXT NOT NULL DEFAULT '',
    hf_filenames TEXT NOT NULL DEFAULT '[]',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""

_COLUMNS = (
    "id",
    "context_length",
    "port",
    "path",
    "family",
    "quantization",
    "backend",
    "modality",
    "mmproj_path",
    "downloaded",
    "discovery",
    "rss_estimate_mb",
    "hf_repo",
    "hf_sha256",
    "hf_filenames",
)


@dataclass
class RegistryEntry:
    id: str
    context_length: int
    port: int
    path: str = ""
    family: str = ""
    quantization: str = ""
    # One of BACKENDS. Selects how lifecycle.start_instance() launches this
    # model and how the scanner recognizes its process. See RM-06/RM-08.
    backend: str = "llama_cpp"
    # One of MODALITIES. Only "llama_cpp" acts on this today (--embedding /
    # --mmproj flags in lifecycle.py); other backends accept it but don't yet
    # dispatch on it — see memory/wiki/model-registry.md RM-09 section.
    modality: str = "text"
    # Vision projector file (.gguf), required when modality="vision" on
    # llama_cpp — llama-server's --mmproj flag.
    mmproj_path: str = ""
    downloaded: bool = False
    discovery: bool = False  # See: memory/specs/010-registry-view-redesign.md
    rss_estimate_mb: int | None = None
    hf_repo: str = ""
    hf_sha256: str = ""
    # The downloaded file(s) for this model — a single-element list for
    # single-file models, multiple for sharded ones. Always populated once a
    # file is known (never a separate "first filename" field — RM-49).
    hf_filenames: list[str] = field(default_factory=list)

    @property
    def backend_url(self) -> str:
        """Always derived from `port` — see RM-49's schema evaluation for why
        this was dropped as a stored field (it never carried information
        beyond the port, on every real code path)."""
        return f"http://127.0.0.1:{self.port}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "port": self.port,
            "context_length": self.context_length,
            "path": self.path,
            "family": self.family,
            "quantization": self.quantization,
            "backend": self.backend,
            "modality": self.modality,
            "mmproj_path": self.mmproj_path,
            "downloaded": self.downloaded,
            "discovery": self.discovery,
            "rss_estimate_mb": self.rss_estimate_mb,
            "backend_url": self.backend_url,
            "hf_repo": self.hf_repo,
            "hf_sha256": self.hf_sha256,
            "hf_filenames": self.hf_filenames,
        }


class Registry:
    """Load and persist runtime/manager/registry.db.

    Implements: memory/specs/008-llama-server-manager.md — AC-3, AC-15, AC-16, AC-18
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._entries: dict[str, RegistryEntry] = {}
        self._lock = threading.RLock()
        self._migrate_legacy_yaml_if_needed()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.executescript(_SCHEMA_SQL)
        self._conn.commit()
        self._load()

    # ── public API ────────────────────────────────────────────────────────────

    @property
    def entries(self) -> list[RegistryEntry]:
        with self._lock:
            return list(self._entries.values())

    def get(self, model_id: str) -> RegistryEntry | None:
        with self._lock:
            return self._entries.get(model_id)

    def add(self, entry: RegistryEntry) -> None:
        """Validate and add entry; persist to disk."""
        _validate_id(entry.id)
        _validate_backend(entry.backend)
        _validate_modality(entry.modality)
        _validate_path(entry.path, entry.backend)
        _validate_port(entry.port)
        with self._lock:
            self._entries[entry.id] = entry
            self._conn.execute(
                f"INSERT OR REPLACE INTO models ({', '.join(_COLUMNS)}) "
                f"VALUES ({', '.join('?' for _ in _COLUMNS)})",
                _row_params(entry),
            )
            self._conn.commit()

    def update(self, model_id: str, **kwargs: Any) -> None:
        """Patch fields on an existing entry and persist."""
        with self._lock:
            entry = self._entries[model_id]
            for key, val in kwargs.items():
                setattr(entry, key, val)
            self._conn.execute(
                f"UPDATE models SET {', '.join(f'{c} = ?' for c in _COLUMNS if c != 'id')} "
                "WHERE id = ?",
                (*_row_params(entry, skip_id=True), model_id),
            )
            self._conn.commit()

    def remove(self, model_id: str) -> None:
        """Remove entry from registry.

        Implements: memory/specs/008-llama-server-manager.md — AC-18
        """
        with self._lock:
            if model_id not in self._entries:
                raise KeyError(model_id)
            del self._entries[model_id]
            self._conn.execute("DELETE FROM models WHERE id = ?", (model_id,))
            self._conn.commit()

    def reload(self) -> None:
        """Re-read every entry from the database."""
        with self._lock:
            self._load()

    # ── persistence ──────────────────────────────────────────────────────────

    def _load(self) -> None:
        cursor = self._conn.execute(f"SELECT {', '.join(_COLUMNS)} FROM models ORDER BY rowid")
        self._entries = {}
        for row in cursor.fetchall():
            raw = dict(zip(_COLUMNS, row, strict=True))
            entry = RegistryEntry(
                id=raw["id"],
                context_length=raw["context_length"],
                port=raw["port"],
                path=raw["path"],
                family=raw["family"],
                quantization=raw["quantization"],
                backend=raw["backend"],
                modality=raw["modality"],
                mmproj_path=raw["mmproj_path"],
                downloaded=bool(raw["downloaded"]),
                discovery=bool(raw["discovery"]),
                rss_estimate_mb=raw["rss_estimate_mb"],
                hf_repo=raw["hf_repo"],
                hf_sha256=raw["hf_sha256"],
                hf_filenames=json.loads(raw["hf_filenames"]),
            )
            self._entries[entry.id] = entry

    def _migrate_legacy_yaml_if_needed(self) -> None:
        """One-time import from a legacy registry.yaml, if the new DB doesn't
        exist yet but the old YAML file does. Non-destructive: the YAML is
        renamed to .yaml.bak, never deleted. Builds the DB at a temp path and
        os.replace()'s it into place only once fully populated, so a crash
        mid-import leaves the next start with a clean retry (an orphaned .tmp
        file and an untouched, still-migratable .yaml) rather than a
        half-populated DB masquerading as complete."""
        if self._path.exists():
            return
        legacy = self._path.with_suffix(".yaml")
        if not legacy.exists():
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".db.tmp")
        tmp.unlink(missing_ok=True)
        conn = sqlite3.connect(str(tmp))
        try:
            conn.executescript(_SCHEMA_SQL)
            data = yaml.safe_load(legacy.read_text()) or {}
            for raw in data.get("models", []):
                entry = _entry_from_legacy_yaml(raw)
                conn.execute(
                    f"INSERT OR REPLACE INTO models "
                    f"({', '.join(_COLUMNS)}, created_at) "
                    f"VALUES ({', '.join('?' for _ in _COLUMNS)}, CURRENT_TIMESTAMP)",
                    _row_params(entry),
                )
            conn.commit()
        finally:
            conn.close()
        os.replace(tmp, self._path)
        # A missing legacy file here means we lost a rare cross-process race —
        # harmless, since the DB is already fully in place.
        with contextlib.suppress(FileNotFoundError):
            legacy.rename(legacy.with_suffix(".yaml.bak"))


def _row_params(entry: RegistryEntry, skip_id: bool = False) -> tuple[Any, ...]:
    values = {
        "id": entry.id,
        "context_length": entry.context_length,
        "port": entry.port,
        "path": entry.path,
        "family": entry.family,
        "quantization": entry.quantization,
        "backend": entry.backend,
        "modality": entry.modality,
        "mmproj_path": entry.mmproj_path,
        "downloaded": int(entry.downloaded),
        "discovery": int(entry.discovery),
        "rss_estimate_mb": entry.rss_estimate_mb,
        "hf_repo": entry.hf_repo,
        "hf_sha256": entry.hf_sha256,
        "hf_filenames": json.dumps(entry.hf_filenames),
    }
    cols = [c for c in _COLUMNS if c != "id"] if skip_id else _COLUMNS
    return tuple(values[c] for c in cols)


def _entry_from_legacy_yaml(raw: dict[str, Any]) -> RegistryEntry:
    """Maps a legacy registry.yaml model dict onto RegistryEntry, folding the
    old hf_filename/hf_filenames split into the single hf_filenames list and
    dropping the removed log_level/backend_url fields (see RM-49)."""
    hf_filenames = raw.get("hf_filenames") or []
    if not hf_filenames and raw.get("hf_filename"):
        hf_filenames = [raw["hf_filename"]]
    return RegistryEntry(
        id=raw["id"],
        path=raw.get("path", ""),
        context_length=raw.get("context_length", 4096),
        port=raw.get("port", 8080),
        family=raw.get("family", ""),
        quantization=raw.get("quantization", ""),
        backend=raw.get("backend", "llama_cpp"),
        modality=raw.get("modality", "text"),
        mmproj_path=raw.get("mmproj_path", ""),
        downloaded=raw.get("downloaded", False),
        discovery=raw.get("discovery", False),
        rss_estimate_mb=raw.get("rss_estimate_mb"),
        hf_repo=raw.get("hf_repo", ""),
        hf_sha256=raw.get("hf_sha256", ""),
        hf_filenames=hf_filenames,
    )


# ── validators ────────────────────────────────────────────────────────────────


def _validate_id(model_id: str) -> None:
    """Implements: memory/specs/008-llama-server-manager.md — AC-16"""
    if not _ID_RE.match(model_id):
        raise ValueError(
            f"Invalid model ID {model_id!r}. Must match ^[a-z0-9][a-z0-9_-]{{1,62}}[a-z0-9]$"
        )


def _validate_backend(backend: str) -> None:
    if backend not in BACKENDS:
        raise ValueError(f"Unknown backend {backend!r}. Must be one of {BACKENDS}")


def _validate_modality(modality: str) -> None:
    if modality not in MODALITIES:
        raise ValueError(f"Unknown modality {modality!r}. Must be one of {MODALITIES}")


def _validate_path(path: str, backend: str = "llama_cpp") -> None:
    """Implements: memory/specs/008-llama-server-manager.md — AC-15

    Only llama_cpp requires a local .gguf file. mlx/vllm/sglang commonly load
    directly from a HuggingFace repo id (e.g. "mlx-community/..."), which is
    not a filesystem path, so only path-traversal safety is enforced for them.
    """
    if not path:
        return  # path may be empty before download
    if ".." in Path(path).parts:
        raise ValueError(f"Path traversal detected in model path: {path!r}")
    if backend == "llama_cpp" and Path(path).resolve().suffix.lower() != ".gguf":
        raise ValueError(f"Model path must point to a .gguf file, got: {path!r}")


def _validate_port(port: int) -> None:
    if not (1024 <= port <= 65535):
        raise ValueError(f"Port must be in range 1024–65535, got: {port}")
