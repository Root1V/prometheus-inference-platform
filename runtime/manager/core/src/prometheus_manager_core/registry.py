"""Registry CRUD — runtime/manager/registry.db (SQLite).

Implements: memory/specs/008-llama-server-manager.md — AC-3, AC-15, AC-16, AC-17, AC-18

RM-51: split what used to be one conflated `models` row into two tables —
`models` (the catalog: a downloaded/known model's file metadata, shared by
however many instances of it) and `instances` (a specific running deployment,
FK'd to its catalog row via `model_id`). This fixes a real incident: deleting
an "instance" through the dashboard used to delete the whole row, wiping the
model's catalog registration along with it (see docs/roadmap.md RM-51).

`RegistryEntry` stays a flat, merged view — every field the pre-split schema
had, computed via a join instead of read off one row — so lifecycle.py,
scanner.py, capacity.py, and most of the manager-api/tui callers need no
changes at all.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import sqlite3
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,62}[a-z0-9]$")

# RM-70: a slug becomes a `model:<slug>` scope, so it must fit what
# auth-service accepts there — see its schemas.py _MODEL_SCOPE_RE. Kept
# deliberately narrower than that regex's open-ended tail (bounded length, no
# trailing separator) but never wider: a slug auth-service would reject is a
# model nobody could ever be granted.
_SLUG_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{1,62}[a-zA-Z0-9]$")

# See memory/wiki/inference-engines.md (RM-06) for the comparison behind this list.
# RM-38: sd_cpp (stable-diffusion.cpp's sd-server) is the odd one out — it
# generates images rather than serving LLM completions, so it deviates from
# the other four in lifecycle.py's command-building and scanner.py's
# process-recognition (its own --listen-ip/--listen-port flags, no /health
# endpoint). See lifecycle.py's _build_sd_cpp_cmd for specifics.
BACKENDS = ("llama_cpp", "mlx", "vllm", "sglang", "sd_cpp")

# RM-09: what kind of requests this model serves. Determines which flags
# lifecycle.py adds to the launch command and how the gateway routes requests
# (text -> /v1/chat/completions, embedding -> /v1/embeddings, vision -> chat
# completions with image content parts). See memory/wiki/model-registry.md.
# RM-38: "image" -> POST /v1/images/generations (sd_cpp only).
MODALITIES = ("text", "embedding", "vision", "image")

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS models (
    id TEXT PRIMARY KEY,
    -- RM-70: the public, routable name — what a client sends as `model`.
    -- Immutable for the life of the model; renaming means a new model, which
    -- is what keeps `model:<slug>` grants and usage rows meaningful without a
    -- slug-history table or cross-service grant rewrites.
    slug TEXT NOT NULL DEFAULT '',
    -- RM-70: display label. Free to change precisely because nothing keys off it.
    name TEXT NOT NULL DEFAULT '',
    -- RM-70: modality is a property of the weights, not of a process, so it
    -- belongs here rather than per instance. Two replicas of one model can't
    -- disagree about it by construction.
    modality TEXT NOT NULL DEFAULT '',
    path TEXT NOT NULL DEFAULT '',
    family TEXT NOT NULL DEFAULT '',
    quantization TEXT NOT NULL DEFAULT '',
    downloaded INTEGER NOT NULL DEFAULT 0,
    hf_repo TEXT NOT NULL DEFAULT '',
    hf_sha256 TEXT NOT NULL DEFAULT '',
    hf_filenames TEXT NOT NULL DEFAULT '[]',
    mmproj_path TEXT NOT NULL DEFAULT '',
    vae_path TEXT NOT NULL DEFAULT '',
    clip_l_path TEXT NOT NULL DEFAULT '',
    t5xxl_path TEXT NOT NULL DEFAULT '',
    -- RM-74: set instead of deleting the row. A published slug can never be
    -- handed to a different model: usage is billed against it and
    -- `model:<slug>` grants are written against it, so recycling one would
    -- merge two models' invoices *and* silently give everyone who could reach
    -- the old model access to the new. Keeping the row rather than a tombstone
    -- also keeps the answer to "what was this model?" for a billing question
    -- about usage recorded under that name.
    archived_at DATETIME,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS instances (
    id TEXT PRIMARY KEY,
    model_id TEXT NOT NULL REFERENCES models(id),
    -- RM-70: human handle for ops, unique within the model ("#1", "#2").
    -- Auto-assigned, so adding a replica no longer asks the operator to invent
    -- a globally-unique id — which is what leaked "-1"/"-2" suffixes into the
    -- scope picker and made every replica look like a separate model.
    label TEXT NOT NULL DEFAULT '',
    port INTEGER NOT NULL,
    backend TEXT NOT NULL DEFAULT 'llama_cpp',
    modality TEXT NOT NULL DEFAULT 'text',
    context_length INTEGER NOT NULL,
    discovery INTEGER NOT NULL DEFAULT 0,
    rss_estimate_mb INTEGER,
    cfg_scale REAL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_instances_model_id ON instances(model_id);
-- The unique index on models(slug) is created by _backfill_identity_columns,
-- not here: on a database that predates RM-70 the CREATE TABLE above is a
-- no-op, so `slug` does not exist yet and indexing it would fail outright.
"""

# Column order for the two new tables (excludes created_at, which every
# INSERT sets to CURRENT_TIMESTAMP explicitly rather than round-tripping).
_MODEL_COLUMNS = (
    "id",
    "slug",
    "name",
    "modality",
    "path",
    "family",
    "quantization",
    "downloaded",
    "hf_repo",
    "hf_sha256",
    "hf_filenames",
    "mmproj_path",
    "vae_path",
    "clip_l_path",
    "t5xxl_path",
    "archived_at",
)

_INSTANCE_COLUMNS = (
    "id",
    "model_id",
    "label",
    "port",
    "backend",
    "modality",
    "context_length",
    "discovery",
    "rss_estimate_mb",
    "cfg_scale",
)

# Pre-RM-51 single-table column order — used only by the structural migration
# to read legacy rows before the split (and by the legacy-YAML migration,
# which also lands directly in the new two-table shape).
_LEGACY_COLUMNS = (
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
    "vae_path",
    "clip_l_path",
    "t5xxl_path",
    "cfg_scale",
)

# RM-52: added after the single-table schema already existed in the wild —
# needed here too so a very old legacy file (pre-dating RM-52) can still be
# read before the RM-51 split runs on it.
_LEGACY_MIGRATION_COLUMNS = (
    ("vae_path", "TEXT NOT NULL DEFAULT ''"),
    ("clip_l_path", "TEXT NOT NULL DEFAULT ''"),
    ("t5xxl_path", "TEXT NOT NULL DEFAULT ''"),
    ("cfg_scale", "REAL"),
)


class RegistryIntegrityError(ValueError):
    """Raised when an operation would violate a catalog/instance invariant —
    e.g. removing a catalog entry that still has instances referencing it."""


@dataclass
class CatalogEntry:
    """RM-51: a downloaded/known model — the file metadata shared by however
    many instances reference it via `model_id`."""

    id: str
    # RM-70: the public name clients route on. Immutable; renaming is a new
    # model. Empty only for a transient entry before Registry.add_catalog(),
    # which derives it from `id`.
    slug: str = ""
    # RM-70: display label, safe to change — nothing keys off it.
    name: str = ""
    # RM-70: a property of the weights, so it lives here rather than on each
    # instance, which makes replicas disagreeing about it impossible.
    modality: str = "text"
    path: str = ""
    family: str = ""
    quantization: str = ""
    downloaded: bool = False
    hf_repo: str = ""
    hf_sha256: str = ""
    hf_filenames: list[str] = field(default_factory=list)
    mmproj_path: str = ""
    vae_path: str = ""
    clip_l_path: str = ""
    t5xxl_path: str = ""
    # RM-74: when this model was archived, or None while it is live.
    archived_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "slug": self.slug,
            "archived_at": self.archived_at,
            "name": self.name,
            "modality": self.modality,
            "path": self.path,
            "family": self.family,
            "quantization": self.quantization,
            "downloaded": self.downloaded,
            "hf_repo": self.hf_repo,
            "hf_sha256": self.hf_sha256,
            "hf_filenames": self.hf_filenames,
            "mmproj_path": self.mmproj_path,
            "vae_path": self.vae_path,
            "clip_l_path": self.clip_l_path,
            "t5xxl_path": self.t5xxl_path,
        }


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
    # RM-52: split-file diffusion models (FLUX.1, SD3.5) ship their diffusion
    # weights, VAE, and text encoder(s) as separate files instead of one
    # merged .gguf like SD-Turbo. sd_cpp-only — set any of these three and
    # lifecycle.py's _build_sd_cpp_cmd switches from -m/--model (single file,
    # `path`) to --diffusion-model (`path`) + --vae/--clip_l/--t5xxl. Leave
    # all three empty for a merged single-file model.
    vae_path: str = ""
    clip_l_path: str = ""
    t5xxl_path: str = ""
    # RM-52: sd-server's --cfg-scale defaults to 7.0 (classic SD). FLUX.1/SD3.5
    # are guidance-distilled and need ~1.0 — anything close to the SD default
    # produces a blown-out/solid-color image (confirmed empirically: cfg=7.0
    # against FLUX.1-dev returned a uniform dark blob; cfg=1.0 returned a real
    # image). None means "let sd-server use its own default" — needed for
    # backends/models that DO want it (unchanged SD-Turbo behavior).
    cfg_scale: float | None = None
    # RM-51: which catalog (models) row this instance belongs to. Populated
    # correctly by Registry._load()'s join for every real loaded entry —
    # callers constructing a transient RegistryEntry before Registry.add()
    # don't need to set this, since add() derives the catalog id from the
    # instance's own id (its one-shot "manual registration" path).
    model_id: str = ""
    # RM-70: ops handle, unique within the model ("#1", "#2"). Assigned by
    # Registry, never typed by an operator — inventing a globally-unique
    # instance id is what leaked "-1"/"-2" suffixes into the scope picker.
    label: str = ""
    # RM-70: the catalog's public name, merged in the same way path/family/
    # quantization already are. This is what a client routes on, so the gateway
    # needs it per instance to group replicas under one name.
    model_slug: str = ""

    @property
    def backend_url(self) -> str:
        """Always derived from `port` — see RM-49's schema evaluation for why
        this was dropped as a stored field (it never carried information
        beyond the port, on every real code path)."""
        return f"http://127.0.0.1:{self.port}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "model_id": self.model_id,
            "model_slug": self.model_slug,
            "label": self.label,
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
            "vae_path": self.vae_path,
            "clip_l_path": self.clip_l_path,
            "t5xxl_path": self.t5xxl_path,
            "cfg_scale": self.cfg_scale,
        }


class Registry:
    """Load and persist runtime/manager/registry.db.

    Implements: memory/specs/008-llama-server-manager.md — AC-3, AC-15, AC-16, AC-18
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._instances: dict[str, RegistryEntry] = {}
        self._catalog: dict[str, CatalogEntry] = {}
        self._lock = threading.RLock()
        self._migrate_legacy_yaml_if_needed()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._migrate_models_instances_split_if_needed()
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.executescript(_SCHEMA_SQL)
        _backfill_identity_columns(self._conn)
        self._conn.commit()
        self._load()

    # ── public API: instances ────────────────────────────────────────────────

    @property
    def entries(self) -> list[RegistryEntry]:
        with self._lock:
            return list(self._instances.values())

    def get(self, instance_id: str) -> RegistryEntry | None:
        with self._lock:
            return self._instances.get(instance_id)

    def add(self, entry: RegistryEntry, *, slug: str = "", name: str = "") -> None:
        """Validate and add both a catalog row and an instance row under the
        same id — today's exact behavior. Used by manual registration (a
        hand-typed path, `pmgr register`, GPT4All-style local models) where
        there's no separate catalog entry to reference yet.

        RM-70: `slug` is the model's public, immutable routing name and `name`
        its display label; both default to the instance id, which is what every
        pre-RM-70 registration effectively used.
        """
        _validate_id(entry.id)
        _validate_backend(entry.backend)
        _validate_modality(entry.modality)
        _validate_path(entry.path, entry.backend)
        _validate_path(entry.vae_path, entry.backend)
        _validate_path(entry.clip_l_path, entry.backend)
        _validate_path(entry.t5xxl_path, entry.backend)
        _validate_port(entry.port)
        self._assert_id_free(entry.id)
        self._assert_slug_free(slug or entry.id, owner_id=entry.id)
        # RM-70: manual registration creates the catalog row too, so this is
        # always the model's first instance unless the same id is being
        # re-registered — in which case it keeps the label it already had.
        if not entry.label:
            entry.label = self._next_label(entry.id)
        with self._lock:
            self._conn.execute(
                f"INSERT OR REPLACE INTO models ({', '.join(_MODEL_COLUMNS)}) "
                f"VALUES ({', '.join('?' for _ in _MODEL_COLUMNS)})",
                _model_row_params(_catalog_entry_from_registry_entry(entry, slug=slug, name=name)),
            )
            self._conn.execute(
                f"INSERT OR REPLACE INTO instances ({', '.join(_INSTANCE_COLUMNS)}) "
                f"VALUES ({', '.join('?' for _ in _INSTANCE_COLUMNS)})",
                _instance_row_params(entry, model_id=entry.id),
            )
            self._conn.commit()
            self._load()

    def next_instance_id(self, model_id: str) -> str:
        """A free instance id for the next replica of *model_id* — RM-70.

        Instance ids are still the primary key, and lifecycle.py names each
        process's PID and log file after one, so they can't be opaque yet. But
        nobody should have to *invent* one: asking an operator for a globally
        unique id is what produced the "-1"/"-2" suffixes that leaked into the
        scope picker and made replicas look like separate models.

        Derived from the catalog id rather than the slug, so the id stays
        stable if the display-facing naming ever changes.
        """
        base = model_id
        with self._lock:
            taken = set(self._instances)
        position = 2  # the first instance is normally the model id itself
        while f"{base}-{position}" in taken:
            position += 1
        return f"{base}-{position}"

    def set_slug(self, model_id: str, slug: str) -> None:
        """Name a model whose slug is still the placeholder the migration left.

        RM-70 backfilled `slug = id` for every model that predated slugs, so
        nothing was ever *chosen* — the ids just happen to sit in the field.
        Letting that placeholder be replaced once is not a rename: it's the
        naming that never happened. Once a real slug is in place it is frozen,
        because clients route on it and `model:<slug>` grants key off it, and
        changing it under them is the thing immutability exists to prevent.
        """
        with self._lock:
            catalog = self._catalog.get(model_id)
        if catalog is None:
            raise KeyError(model_id)
        if catalog.slug != model_id:
            raise RegistryIntegrityError(
                f"Model {model_id!r} is already published as {catalog.slug!r}. A slug is "
                "frozen once set — clients route on it and their grants key off it."
            )
        _validate_slug(slug)
        self._assert_slug_free(slug, owner_id=model_id)
        with self._lock:
            self._conn.execute("UPDATE models SET slug = ? WHERE id = ?", (slug, model_id))
            self._conn.commit()
            self._load()

    def _assert_id_free(self, model_id: str) -> None:
        """RM-74: an archived model still owns its id.

        Without this the INSERT OR REPLACE that upserts a catalog row would
        overwrite the archived one, quietly resurrecting it as a different model
        and taking its slug and history along.
        """
        with self._lock:
            archived = self._archived.get(model_id)
        if archived is not None:
            raise RegistryIntegrityError(
                f"Model id {model_id!r} belongs to a model archived on {archived.archived_at}. "
                "Restore it if this is the same model, or pick another id."
            )

    def _assert_slug_free(self, slug: str, *, owner_id: str) -> None:
        """A slug is what clients route on, so two models sharing one would make
        routing ambiguous rather than merely untidy.
        """
        with self._lock:
            clash = next(
                (c for c in self._catalog.values() if c.slug == slug and c.id != owner_id),
                None,
            )
            retired = next(
                (c for c in self._archived.values() if c.slug == slug and c.id != owner_id),
                None,
            )
        if clash is not None:
            raise RegistryIntegrityError(
                f"Slug {slug!r} is already used by model {clash.id!r}. "
                "A slug is what clients route on, so it has to be unique."
            )
        if retired is not None:
            raise RegistryIntegrityError(
                f"Slug {slug!r} belonged to model {retired.id!r}, archived on "
                f"{retired.archived_at}. Retired names are never reused: usage is billed "
                "against the slug and `model:<slug>` grants are written against it, so a new "
                "model taking this name would inherit the old one's invoices and everyone's "
                "access to it. Pick another name, or restore that model if this is the same one."
            )

    def _next_label(self, model_id: str) -> str:
        """ "#1", "#2"… — one past the highest this model has handed out.

        Deliberately not "lowest free": removing #2 from #1/#2/#3 and handing
        #2 to the next replica would make two different processes share a label
        across time, so a log or a dashboard screenshot mentioning "#2" would be
        ambiguous. Removing the *highest* replica does still free its number —
        closing that too would mean persisting a high-water mark, which isn't
        worth it for a label.
        """
        with self._lock:
            taken = [
                inst.label
                for inst in self._instances.values()
                if inst.model_id == model_id and inst.label.startswith("#")
            ]
        highest = max((int(label[1:]) for label in taken if label[1:].isdigit()), default=0)
        return f"#{highest + 1}"

    def add_instance(
        self,
        id: str,
        model_id: str,
        *,
        port: int,
        backend: str = "llama_cpp",
        modality: str = "text",
        context_length: int = 4096,
        discovery: bool = False,
        rss_estimate_mb: int | None = None,
        cfg_scale: float | None = None,
    ) -> None:
        """Create a new instance referencing an *existing* catalog entry —
        the "create instance from a downloaded model" flow (RM-51)."""
        with self._lock:
            catalog = self._catalog.get(model_id)
        if catalog is None:
            raise ValueError(f"No catalog entry {model_id!r} — download or register it first.")
        _validate_id(id)
        _validate_backend(backend)
        _validate_modality(modality)
        _validate_path(catalog.path, backend)
        _validate_path(catalog.vae_path, backend)
        _validate_path(catalog.clip_l_path, backend)
        _validate_path(catalog.t5xxl_path, backend)
        _validate_port(port)
        entry = RegistryEntry(
            id=id,
            model_id=model_id,
            label=self._next_label(model_id),
            port=port,
            backend=backend,
            modality=modality,
            context_length=context_length,
            discovery=discovery,
            rss_estimate_mb=rss_estimate_mb,
            cfg_scale=cfg_scale,
        )
        with self._lock:
            self._conn.execute(
                f"INSERT OR REPLACE INTO instances ({', '.join(_INSTANCE_COLUMNS)}) "
                f"VALUES ({', '.join('?' for _ in _INSTANCE_COLUMNS)})",
                _instance_row_params(entry, model_id=model_id),
            )
            self._conn.commit()
            self._load()

    def update(self, instance_id: str, **kwargs: Any) -> None:
        """Patch fields on an existing instance (routing each to the
        instance or catalog table it actually belongs to) and persist.

        No validation — deliberately permissive, matching pre-RM-51 behavior
        (see test_lifecycle.py::test_start_instance_rejects_unknown_backend,
        which relies on start_instance() doing its own downstream checks
        rather than update() gatekeeping).
        """
        with self._lock:
            if instance_id not in self._instances:
                raise KeyError(instance_id)
            model_id = self._instances[instance_id].model_id

            instance_fields = {
                k: v
                for k, v in kwargs.items()
                if k in _INSTANCE_COLUMNS and k not in ("id", "model_id")
            }
            model_fields = {k: v for k, v in kwargs.items() if k in _MODEL_COLUMNS and k != "id"}

            if instance_fields:
                self._conn.execute(
                    f"UPDATE instances SET {', '.join(f'{c} = ?' for c in instance_fields)} "
                    "WHERE id = ?",
                    (*_encode_instance_values(instance_fields), instance_id),
                )
            if model_fields:
                self._conn.execute(
                    f"UPDATE models SET {', '.join(f'{c} = ?' for c in model_fields)} WHERE id = ?",
                    (*_encode_model_values(model_fields), model_id),
                )
            self._conn.commit()
            self._load()

    def remove(self, instance_id: str) -> None:
        """Remove an INSTANCE only. The catalog entry (and any other
        instance referencing it) is left untouched — this is the RM-51 bug
        fix: previously this deleted the whole conflated row.

        Implements: memory/specs/008-llama-server-manager.md — AC-18
        """
        with self._lock:
            if instance_id not in self._instances:
                raise KeyError(instance_id)
            self._conn.execute("DELETE FROM instances WHERE id = ?", (instance_id,))
            self._conn.commit()
            self._load()

    def reload(self) -> None:
        """Re-read every entry from the database."""
        with self._lock:
            self._load()

    # ── public API: catalog ──────────────────────────────────────────────────

    def add_catalog(self, entry: CatalogEntry) -> None:
        _validate_id(entry.id)
        _validate_path_traversal(entry.path)
        _validate_path_traversal(entry.vae_path)
        _validate_path_traversal(entry.clip_l_path)
        _validate_path_traversal(entry.t5xxl_path)
        # RM-70: the INSERT below is OR REPLACE so that re-registering the same
        # id upserts. On a duplicate *slug* that would silently delete the other
        # model's row and orphan its instances, so refuse explicitly instead.
        self._assert_id_free(entry.id)
        self._assert_slug_free(entry.slug or entry.id, owner_id=entry.id)
        with self._lock:
            self._conn.execute(
                f"INSERT OR REPLACE INTO models ({', '.join(_MODEL_COLUMNS)}) "
                f"VALUES ({', '.join('?' for _ in _MODEL_COLUMNS)})",
                _model_row_params(entry),
            )
            self._conn.commit()
            self._load()

    def get_catalog(self, model_id: str) -> CatalogEntry | None:
        with self._lock:
            return self._catalog.get(model_id)

    def list_catalog(self) -> list[CatalogEntry]:
        with self._lock:
            return list(self._catalog.values())

    def update_catalog(self, model_id: str, **kwargs: Any) -> None:
        """Same permissive, no-validation contract as update(). Needed by
        the download flow: a freshly-downloaded model has no instance row
        yet, so there's nothing for the instance-keyed update() to route
        through."""
        with self._lock:
            if model_id not in self._catalog:
                raise KeyError(model_id)
            fields = {k: v for k, v in kwargs.items() if k != "id"}
            if fields:
                self._conn.execute(
                    f"UPDATE models SET {', '.join(f'{c} = ?' for c in fields)} WHERE id = ?",
                    (*_encode_model_values(fields), model_id),
                )
                self._conn.commit()
                self._load()

    def archive_catalog(self, model_id: str) -> None:
        """Retire a catalog entry — RM-74. Raises RegistryIntegrityError if any
        instance still references it (see lifecycle.deregister_model for the
        cascade used by the Models/Library page's "delete downloaded file").

        The row is kept, not deleted. A published slug can never be handed to a
        different model: usage is billed against it and `model:<slug>` grants
        are written against it, so recycling one would merge two models'
        invoices *and* silently give everyone who could reach the old model
        access to the new. Keeping the row rather than a tombstone also means a
        billing question about usage recorded under that name can still be
        answered — which file, which quantization, which context.
        """
        with self._lock:
            if model_id not in self._catalog:
                raise KeyError(model_id)
            referencing = [e.id for e in self._instances.values() if e.model_id == model_id]
            if referencing:
                raise RegistryIntegrityError(
                    f"Cannot archive catalog {model_id!r}: still referenced by "
                    f"instances {referencing}"
                )
            self._conn.execute(
                "UPDATE models SET archived_at = CURRENT_TIMESTAMP WHERE id = ?", (model_id,)
            )
            self._conn.commit()
            self._load()

    def restore_catalog(self, model_id: str) -> None:
        """Bring an archived model back — RM-74. The escape hatch for archiving
        the wrong one, and the reason archiving can stay the default.
        """
        with self._lock:
            if model_id not in self._archived:
                raise KeyError(model_id)
            self._conn.execute("UPDATE models SET archived_at = NULL WHERE id = ?", (model_id,))
            self._conn.commit()
            self._load()

    def list_archived(self) -> list[CatalogEntry]:
        with self._lock:
            return sorted(self._archived.values(), key=lambda c: c.archived_at or "", reverse=True)

    # ── persistence ──────────────────────────────────────────────────────────

    def _load(self) -> None:
        cat_cursor = self._conn.execute(
            f"SELECT {', '.join(_MODEL_COLUMNS)} FROM models ORDER BY rowid"
        )
        self._catalog = {}
        self._archived = {}
        for row in cat_cursor.fetchall():
            cat_raw = dict(zip(_MODEL_COLUMNS, row, strict=True))
            cat_entry = CatalogEntry(
                archived_at=cat_raw["archived_at"],
                id=cat_raw["id"],
                slug=cat_raw["slug"],
                name=cat_raw["name"],
                modality=cat_raw["modality"] or "text",
                path=cat_raw["path"],
                family=cat_raw["family"],
                quantization=cat_raw["quantization"],
                downloaded=bool(cat_raw["downloaded"]),
                hf_repo=cat_raw["hf_repo"],
                hf_sha256=cat_raw["hf_sha256"],
                hf_filenames=json.loads(cat_raw["hf_filenames"]),
                mmproj_path=cat_raw["mmproj_path"],
                vae_path=cat_raw["vae_path"],
                clip_l_path=cat_raw["clip_l_path"],
                t5xxl_path=cat_raw["t5xxl_path"],
            )
            # RM-74: archived models are kept out of every operational path —
            # they don't route, don't list, and can't take an instance — but
            # stay reachable to the slug/id guards and the archive listing.
            if cat_entry.archived_at:
                self._archived[cat_entry.id] = cat_entry
            else:
                self._catalog[cat_entry.id] = cat_entry

        inst_cursor = self._conn.execute(
            f"SELECT {', '.join(_INSTANCE_COLUMNS)} FROM instances ORDER BY rowid"
        )
        self._instances = {}
        for row in inst_cursor.fetchall():
            inst_raw = dict(zip(_INSTANCE_COLUMNS, row, strict=True))
            catalog = self._catalog.get(inst_raw["model_id"])
            inst_entry = RegistryEntry(
                id=inst_raw["id"],
                model_id=inst_raw["model_id"],
                label=inst_raw["label"],
                port=inst_raw["port"],
                backend=inst_raw["backend"],
                modality=inst_raw["modality"],
                context_length=inst_raw["context_length"],
                discovery=bool(inst_raw["discovery"]),
                rss_estimate_mb=inst_raw["rss_estimate_mb"],
                cfg_scale=inst_raw["cfg_scale"],
                model_slug=catalog.slug if catalog else "",
                path=catalog.path if catalog else "",
                family=catalog.family if catalog else "",
                quantization=catalog.quantization if catalog else "",
                mmproj_path=catalog.mmproj_path if catalog else "",
                downloaded=catalog.downloaded if catalog else False,
                hf_repo=catalog.hf_repo if catalog else "",
                hf_sha256=catalog.hf_sha256 if catalog else "",
                hf_filenames=catalog.hf_filenames if catalog else [],
                vae_path=catalog.vae_path if catalog else "",
                clip_l_path=catalog.clip_l_path if catalog else "",
                t5xxl_path=catalog.t5xxl_path if catalog else "",
            )
            self._instances[inst_entry.id] = inst_entry

    def _migrate_legacy_yaml_if_needed(self) -> None:
        """One-time import from a legacy registry.yaml, if the new DB doesn't
        exist yet but the old YAML file does. Non-destructive: the YAML is
        renamed to .yaml.bak, never deleted. Builds the DB at a temp path and
        os.replace()'s it into place only once fully populated, so a crash
        mid-import leaves the next start with a clean retry (an orphaned .tmp
        file and an untouched, still-migratable .yaml) rather than a
        half-populated DB masquerading as complete.

        RM-51: lands directly in the new two-table shape — every legacy YAML
        model dict becomes one catalog row + one instance row, same id for
        both, via the same _insert_split_row() helper the same-file
        models->models+instances migration below uses.
        """
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
                _insert_split_row(conn, entry)
            conn.commit()
        finally:
            conn.close()
        os.replace(tmp, self._path)
        # A missing legacy file here means we lost a rare cross-process race —
        # harmless, since the DB is already fully in place.
        with contextlib.suppress(FileNotFoundError):
            legacy.rename(legacy.with_suffix(".yaml.bak"))

    def _migrate_models_instances_split_if_needed(self) -> None:
        """RM-51: split a pre-existing single-table registry.db into the new
        (models, instances) shape, in place.

        Unlike the legacy-YAML migration above (source and destination are
        different files, so renaming the source to .bak after the swap is
        safe), source and destination are the SAME file here — so the backup
        must be taken *before* any destructive step, via a copy (not a
        rename, which would briefly leave self._path missing for no benefit).

        Crash-safe: a crash before the atomic os.replace() leaves self._path
        completely untouched (we only ever read from the backup copy) — the
        next start detects the still-legacy shape and retries from scratch
        (the backup-exists check below makes the copy step idempotent). A
        crash after the swap leaves the .pre-rm51.bak file as a permanent
        recovery snapshot, never deleted — mirrors the .yaml.bak convention.
        """
        if not self._path.exists():
            return  # brand-new file — _SCHEMA_SQL creates the split shape directly

        probe = sqlite3.connect(str(self._path))
        try:
            tables = {
                r[0] for r in probe.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            if "instances" in tables:
                return  # already migrated
            if "models" not in tables:
                return  # unexpected/empty file — let _SCHEMA_SQL create fresh, don't touch
            model_cols = {r[1] for r in probe.execute("PRAGMA table_info(models)")}
            if "port" not in model_cols or "backend" not in model_cols:
                return  # not the legacy single-table shape — safety no-op
        finally:
            probe.close()

        # with_name (append), not with_suffix (replace) — self._path.suffix is
        # ".db", and with_suffix(".pre-rm51.bak") would silently drop it,
        # producing "registry.pre-rm51.bak" instead of the intended
        # "registry.db.pre-rm51.bak".
        backup = self._path.with_name(self._path.name + ".pre-rm51.bak")
        if not backup.exists():
            shutil.copy2(self._path, backup)

        legacy_conn = sqlite3.connect(str(backup))
        try:
            _backfill_legacy_columns(legacy_conn)
            rows = legacy_conn.execute(
                f"SELECT {', '.join(_LEGACY_COLUMNS)} FROM models ORDER BY rowid"
            ).fetchall()
        finally:
            legacy_conn.close()

        tmp = self._path.with_suffix(".db.tmp")
        tmp.unlink(missing_ok=True)
        conn = sqlite3.connect(str(tmp))
        try:
            conn.executescript(_SCHEMA_SQL)
            for row in rows:
                raw: dict[str, Any] = dict(zip(_LEGACY_COLUMNS, row, strict=True))
                entry = _entry_from_legacy_row(raw)
                _insert_split_row(conn, entry)
            conn.commit()
        finally:
            conn.close()
        os.replace(tmp, self._path)


# RM-70: added to a schema already in the wild, so the same idempotent,
# PRAGMA-guarded ALTER this file has used since RM-52. Every column defaults to
# empty and is filled in by _backfill_identity_columns below, because a DEFAULT
# can't express "derive it from the row you're on".
_IDENTITY_MIGRATION_COLUMNS = (
    ("models", "slug", "TEXT NOT NULL DEFAULT ''"),
    ("models", "name", "TEXT NOT NULL DEFAULT ''"),
    ("models", "modality", "TEXT NOT NULL DEFAULT ''"),
    ("instances", "label", "TEXT NOT NULL DEFAULT ''"),
    ("models", "archived_at", "DATETIME"),
)


def _backfill_identity_columns(conn: sqlite3.Connection) -> None:
    """Add and populate RM-70's identity columns. Idempotent.

    Deliberately additive: no primary key changes, so every existing model and
    instance id survives untouched. That matters beyond tidiness — lifecycle.py
    names each running process's PID and log file after its instance id
    (`{id}.pid`, `{id}.log`), so renaming ids under a live manager would strand
    the processes it is currently supervising.

    Backfill rules:
    * `models.slug` = the model's current id, which *is* the name clients
      already send, so routing is unchanged on the first boot after upgrading.
    * `models.name` = the same, as a starting display label.
    * `models.modality` = whatever its instances already agree on, since the
      column is moving up from `instances`. A model whose instances disagree
      keeps the most common answer — the disagreement was already a
      misconfiguration ([[RM-57]] had to detect it at request time).
    * `instances.label` = "#1", "#2"… in creation order within each model, so
      the oldest replica keeps the lowest number.
    """
    for table, column, col_def in _IDENTITY_MIGRATION_COLUMNS:
        existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_def}")

    conn.execute("UPDATE models SET slug = id WHERE slug = ''")
    conn.execute("UPDATE models SET name = id WHERE name = ''")
    conn.execute(
        """
        UPDATE models SET modality = COALESCE((
            SELECT i.modality FROM instances i
            WHERE i.model_id = models.id
            GROUP BY i.modality
            ORDER BY COUNT(*) DESC, i.modality
            LIMIT 1
        ), 'text')
        WHERE modality = ''
        """
    )

    unlabelled = [
        r[0] for r in conn.execute("SELECT DISTINCT model_id FROM instances WHERE label = ''")
    ]
    for model_id in unlabelled:
        rows = conn.execute(
            "SELECT id FROM instances WHERE model_id = ? ORDER BY created_at, id",
            (model_id,),
        ).fetchall()
        for position, (instance_id,) in enumerate(rows, start=1):
            conn.execute(
                "UPDATE instances SET label = ? WHERE id = ?",
                (f"#{position}", instance_id),
            )

    # Only now that every row has a slug — indexing the column before the
    # backfill would fail on a pre-RM-70 database, where it doesn't exist yet.
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_models_slug ON models(slug) WHERE slug != ''"
    )


def _backfill_legacy_columns(conn: sqlite3.Connection) -> None:
    """Backfill RM-52's columns onto a legacy single-table `models` if this
    is a very old registry.db that pre-dates them — same idempotent
    PRAGMA-table_info-guarded ALTER TABLE this codebase has used since RM-52,
    needed here so the RM-51 migration can read even a pre-RM-52 legacy file."""
    existing = {row[1] for row in conn.execute("PRAGMA table_info(models)")}
    for name, col_def in _LEGACY_MIGRATION_COLUMNS:
        if name not in existing:
            conn.execute(f"ALTER TABLE models ADD COLUMN {name} {col_def}")


def _insert_split_row(conn: sqlite3.Connection, entry: RegistryEntry) -> None:
    """Insert one flat legacy row as a (models, instances) pair sharing the
    same id — used by both the YAML migration (RM-49) and the same-file
    single-table migration (RM-51), so every already-registered model's
    routing/PID-file name is unchanged post-migration."""
    conn.execute(
        f"INSERT OR REPLACE INTO models ({', '.join(_MODEL_COLUMNS)}, created_at) "
        f"VALUES ({', '.join('?' for _ in _MODEL_COLUMNS)}, CURRENT_TIMESTAMP)",
        _model_row_params(_catalog_entry_from_registry_entry(entry)),
    )
    conn.execute(
        f"INSERT OR REPLACE INTO instances ({', '.join(_INSTANCE_COLUMNS)}, created_at) "
        f"VALUES ({', '.join('?' for _ in _INSTANCE_COLUMNS)}, CURRENT_TIMESTAMP)",
        _instance_row_params(entry, model_id=entry.id),
    )


def _catalog_entry_from_registry_entry(
    entry: RegistryEntry, *, slug: str = "", name: str = ""
) -> CatalogEntry:
    return CatalogEntry(
        id=entry.id,
        # RM-70: manual registration creates catalog and instance under one id,
        # so that id is the default public name when none was given.
        slug=slug or entry.id,
        name=name or entry.id,
        modality=entry.modality,
        path=entry.path,
        family=entry.family,
        quantization=entry.quantization,
        downloaded=entry.downloaded,
        hf_repo=entry.hf_repo,
        hf_sha256=entry.hf_sha256,
        hf_filenames=entry.hf_filenames,
        mmproj_path=entry.mmproj_path,
        vae_path=entry.vae_path,
        clip_l_path=entry.clip_l_path,
        t5xxl_path=entry.t5xxl_path,
    )


def _model_row_params(entry: CatalogEntry) -> tuple[Any, ...]:
    values = {
        "id": entry.id,
        # A catalog row written without an explicit slug falls back to its id,
        # matching what _backfill_identity_columns does for pre-RM-70 rows.
        "slug": entry.slug or entry.id,
        "name": entry.name or entry.id,
        "modality": entry.modality or "text",
        "archived_at": entry.archived_at,
        "path": entry.path,
        "family": entry.family,
        "quantization": entry.quantization,
        "downloaded": int(entry.downloaded),
        "hf_repo": entry.hf_repo,
        "hf_sha256": entry.hf_sha256,
        "hf_filenames": json.dumps(entry.hf_filenames),
        "mmproj_path": entry.mmproj_path,
        "vae_path": entry.vae_path,
        "clip_l_path": entry.clip_l_path,
        "t5xxl_path": entry.t5xxl_path,
    }
    return tuple(values[c] for c in _MODEL_COLUMNS)


def _instance_row_params(entry: RegistryEntry, *, model_id: str) -> tuple[Any, ...]:
    values = {
        "id": entry.id,
        "model_id": model_id,
        "label": entry.label,
        "port": entry.port,
        "backend": entry.backend,
        "modality": entry.modality,
        "context_length": entry.context_length,
        "discovery": int(entry.discovery),
        "rss_estimate_mb": entry.rss_estimate_mb,
        "cfg_scale": entry.cfg_scale,
    }
    return tuple(values[c] for c in _INSTANCE_COLUMNS)


def _encode_model_values(fields: dict[str, Any]) -> tuple[Any, ...]:
    def encode(name: str, value: Any) -> Any:
        if name == "hf_filenames":
            return json.dumps(value)
        if name == "downloaded":
            return int(value)
        return value

    return tuple(encode(k, v) for k, v in fields.items())


def _encode_instance_values(fields: dict[str, Any]) -> tuple[Any, ...]:
    def encode(name: str, value: Any) -> Any:
        if name == "discovery":
            return int(value)
        return value

    return tuple(encode(k, v) for k, v in fields.items())


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
        vae_path=raw.get("vae_path", ""),
        clip_l_path=raw.get("clip_l_path", ""),
        t5xxl_path=raw.get("t5xxl_path", ""),
        cfg_scale=raw.get("cfg_scale"),
    )


def _entry_from_legacy_row(raw: dict[str, Any]) -> RegistryEntry:
    """Maps a pre-RM-51 single-table `models` row (read as a plain dict via
    _LEGACY_COLUMNS) onto RegistryEntry, for the same-file split migration."""
    return RegistryEntry(
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
        vae_path=raw["vae_path"],
        clip_l_path=raw["clip_l_path"],
        t5xxl_path=raw["t5xxl_path"],
        cfg_scale=raw["cfg_scale"],
    )


# ── validators ────────────────────────────────────────────────────────────────


def _validate_id(model_id: str) -> None:
    """Implements: memory/specs/008-llama-server-manager.md — AC-16"""
    if not _ID_RE.match(model_id):
        raise ValueError(
            f"Invalid model ID {model_id!r}. Must match ^[a-z0-9][a-z0-9_-]{{1,62}}[a-z0-9]$"
        )


def _validate_slug(slug: str) -> None:
    if not _SLUG_RE.match(slug):
        raise ValueError(
            f"Invalid slug {slug!r}. Must match {_SLUG_RE.pattern} — it becomes a "
            "`model:<slug>` scope, so anything auth-service would reject here is a "
            "model nobody could be granted access to."
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
    _validate_path_traversal(path)
    if not path:
        return  # path may be empty before download
    if backend == "llama_cpp" and Path(path).resolve().suffix.lower() != ".gguf":
        raise ValueError(f"Model path must point to a .gguf file, got: {path!r}")


def _validate_path_traversal(path: str) -> None:
    """The backend-agnostic half of _validate_path — catalog rows have no
    `backend` of their own, so add_catalog() uses this directly; add_instance()
    re-runs the full _validate_path against the catalog's stored path once a
    backend is chosen."""
    if not path:
        return
    if ".." in Path(path).parts:
        raise ValueError(f"Path traversal detected in model path: {path!r}")


def _validate_port(port: int) -> None:
    if not (1024 <= port <= 65535):
        raise ValueError(f"Port must be in range 1024–65535, got: {port}")
