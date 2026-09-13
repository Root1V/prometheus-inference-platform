"""Tests for Registry: AC-3, AC-15, AC-16, AC-17, AC-18."""

from __future__ import annotations

from pathlib import Path

import pytest

from prometheus_manager_core.registry import (
    BACKENDS,
    MODALITIES,
    Registry,
    RegistryEntry,
    _validate_backend,
    _validate_id,
    _validate_modality,
    _validate_path,
)

# ── RM-08: backend field ─────────────────────────────────────────────────────


class TestBackendField:
    """RM-08: backend selects the launch/scan strategy — see RM-06 for the comparison."""

    def test_defaults_to_llama_cpp(self):
        assert RegistryEntry(id="m", port=8080, context_length=4096).backend == "llama_cpp"

    def test_all_backends_accepted(self):
        for backend in BACKENDS:
            _validate_backend(backend)  # no raise

    def test_unknown_backend_rejected(self):
        with pytest.raises(ValueError, match="Unknown backend"):
            _validate_backend("tensorrt-llm")

    def test_non_gguf_path_rejected_for_llama_cpp(self):
        with pytest.raises(ValueError, match=r"\.gguf"):
            _validate_path("mlx-community/some-model", "llama_cpp")

    def test_hf_repo_id_accepted_for_mlx(self):
        """mlx_lm.server loads directly from a HF repo id — not a .gguf file."""
        _validate_path("mlx-community/Llama-3.2-3B-Instruct-4bit", "mlx")  # no raise

    def test_hf_repo_id_accepted_for_vllm(self):
        _validate_path("meta-llama/Llama-3.1-8B-Instruct", "vllm")  # no raise

    def test_path_traversal_still_rejected_for_non_llama_cpp_backends(self):
        with pytest.raises(ValueError, match="[Tt]raversal"):
            _validate_path("../../etc/passwd", "mlx")

    def test_backend_persisted_through_save_and_reload(self, registry_path: Path):
        registry = Registry(registry_path)
        registry.add(
            RegistryEntry(
                id="mlx-model",
                port=8081,
                context_length=8192,
                backend="mlx",
                path="mlx-community/m-4bit",
            )
        )
        reloaded = Registry(registry_path)
        assert reloaded.get("mlx-model").backend == "mlx"


# ── RM-09: modality field ────────────────────────────────────────────────────


class TestModalityField:
    """RM-09: modality routes VLM/embedding requests — see memory/wiki/model-registry.md."""

    def test_defaults_to_text(self):
        assert RegistryEntry(id="m", port=8080, context_length=4096).modality == "text"

    def test_all_modalities_accepted(self):
        for modality in MODALITIES:
            _validate_modality(modality)  # no raise

    def test_unknown_modality_rejected(self):
        with pytest.raises(ValueError, match="Unknown modality"):
            _validate_modality("audio")

    def test_add_rejects_unknown_modality(self, registry_path: Path):
        registry = Registry(registry_path)
        with pytest.raises(ValueError, match="Unknown modality"):
            registry.add(
                RegistryEntry(
                    id="test-model",
                    port=8080,
                    context_length=4096,
                    path="/m.gguf",
                    modality="audio",
                )
            )

    def test_modality_and_mmproj_path_persisted_through_save_and_reload(self, registry_path: Path):
        registry = Registry(registry_path)
        registry.add(
            RegistryEntry(
                id="vlm-model",
                port=8082,
                context_length=8192,
                path="/models/vlm-model.gguf",
                modality="vision",
                mmproj_path="/models/mmproj.gguf",
            )
        )
        reloaded = Registry(registry_path)
        entry = reloaded.get("vlm-model")
        assert entry.modality == "vision"
        assert entry.mmproj_path == "/models/mmproj.gguf"

    def test_mmproj_path_included_in_dict_when_empty(self, registry_path: Path):
        """to_dict() always includes every field — RM-49 dropped the old
        omit-falsy-fields YAML-tidiness behavior once SQLite became the
        storage format."""
        entry = RegistryEntry(id="m", port=8080, context_length=4096, path="/m.gguf")
        assert entry.to_dict()["mmproj_path"] == ""


# ── RM-52: split-file sd_cpp models (FLUX.1, SD3.5) ─────────────────────────────


class TestSplitFileFields:
    """vae_path/clip_l_path/t5xxl_path — sd_cpp-only, empty by default."""

    def test_default_to_empty(self):
        entry = RegistryEntry(id="m", port=8080, context_length=4096)
        assert entry.vae_path == ""
        assert entry.clip_l_path == ""
        assert entry.t5xxl_path == ""

    def test_persisted_through_save_and_reload(self, registry_path: Path):
        registry = Registry(registry_path)
        registry.add(
            RegistryEntry(
                id="flux-model",
                port=8199,
                context_length=0,
                path="/models/flux1-dev-q8_0.gguf",
                backend="sd_cpp",
                modality="image",
                vae_path="/models/ae.safetensors",
                clip_l_path="/models/clip_l.safetensors",
                t5xxl_path="/models/t5xxl.safetensors",
            )
        )
        reloaded = Registry(registry_path)
        entry = reloaded.get("flux-model")
        assert entry.vae_path == "/models/ae.safetensors"
        assert entry.clip_l_path == "/models/clip_l.safetensors"
        assert entry.t5xxl_path == "/models/t5xxl.safetensors"

    def test_cfg_scale_defaults_to_none_and_round_trips(self, registry_path: Path):
        """RM-52: sd-server's own default (7.0) is wrong for guidance-distilled
        models (FLUX.1) — None means "leave sd-server's default alone"."""
        assert RegistryEntry(id="m", port=8080, context_length=4096).cfg_scale is None

        registry = Registry(registry_path)
        registry.add(
            RegistryEntry(
                id="flux-model",
                port=8199,
                context_length=0,
                backend="sd_cpp",
                cfg_scale=1.0,
            )
        )
        reloaded = Registry(registry_path)
        assert reloaded.get("flux-model").cfg_scale == 1.0

    def test_path_traversal_rejected_for_vae_path(self, registry_path: Path):
        registry = Registry(registry_path)
        with pytest.raises(ValueError, match="[Tt]raversal"):
            registry.add(
                RegistryEntry(
                    id="flux-model",
                    port=8199,
                    context_length=0,
                    path="/models/flux1-dev-q8_0.gguf",
                    backend="sd_cpp",
                    vae_path="../../etc/passwd",
                )
            )

    def test_pre_rm52_legacy_db_migrates_cleanly(self, registry_path: Path):
        """RM-51: a legacy single-table registry.db that ALSO pre-dates RM-52
        (missing vae_path/clip_l_path/t5xxl_path/cfg_scale) must still split
        cleanly into (models, instances) — the split migration backfills
        those columns onto the legacy row before reading it (see
        _backfill_legacy_columns), so an even-older file than RM-52 alone
        doesn't break the RM-51 migration."""
        import sqlite3

        conn = sqlite3.connect(str(registry_path))
        conn.execute(
            """
            CREATE TABLE models (
                id TEXT PRIMARY KEY, context_length INTEGER NOT NULL,
                port INTEGER NOT NULL, path TEXT NOT NULL DEFAULT '',
                family TEXT NOT NULL DEFAULT '', quantization TEXT NOT NULL DEFAULT '',
                backend TEXT NOT NULL DEFAULT 'llama_cpp', modality TEXT NOT NULL DEFAULT 'text',
                mmproj_path TEXT NOT NULL DEFAULT '', downloaded INTEGER NOT NULL DEFAULT 0,
                discovery INTEGER NOT NULL DEFAULT 0, rss_estimate_mb INTEGER,
                hf_repo TEXT NOT NULL DEFAULT '', hf_sha256 TEXT NOT NULL DEFAULT '',
                hf_filenames TEXT NOT NULL DEFAULT '[]',
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            "INSERT INTO models (id, context_length, port, path, family, quantization, downloaded) "
            "VALUES ('old-model', 4096, 8080, '/models/old-model.gguf', 'llama3', 'Q4_0', 1)"
        )
        conn.commit()
        conn.close()

        reopened = Registry(registry_path)
        entry = reopened.get("old-model")
        assert entry is not None
        assert entry.vae_path == ""
        assert entry.path == "/models/old-model.gguf"
        assert reopened.get_catalog("old-model") is not None

        reopened.add(
            RegistryEntry(
                id="new-flux-model",
                port=8199,
                context_length=0,
                backend="sd_cpp",
                vae_path="/models/ae.safetensors",
            )
        )
        assert reopened.get("new-flux-model").vae_path == "/models/ae.safetensors"


# ── AC-3: Registry CRUD ────────────────────────────────────────────────────────


class TestRegistryCRUD:
    """AC-3: Registry loads, persists, and returns entries correctly."""

    def test_AC3_empty_registry_has_no_entries(self, registry_path: Path, empty_registry: Registry):
        """AC-3: new registry returns empty list."""
        assert empty_registry.entries == []

    def test_AC3_add_entry_persists_to_disk(
        self, registry_path: Path, empty_registry: Registry, sample_entry: RegistryEntry
    ):
        """AC-3: add() saves to YAML and reloaded instance returns entry."""
        empty_registry.add(sample_entry)

        reloaded = Registry(registry_path)
        assert len(reloaded.entries) == 1
        assert reloaded.entries[0].id == "test-model"

    def test_AC3_get_returns_entry(self, populated_registry: Registry):
        """AC-3: get() retrieves by id."""
        e = populated_registry.get("test-model")
        assert e is not None
        assert e.port == 9090

    def test_AC3_get_returns_none_for_missing(self, empty_registry: Registry):
        """AC-3: get() returns None for unknown id."""
        assert empty_registry.get("nonexistent") is None

    def test_AC3_update_patches_fields(self, populated_registry: Registry):
        """AC-3: update() modifies specific fields."""
        populated_registry.update("test-model", context_length=8192)
        assert populated_registry.get("test-model").context_length == 8192

    def test_AC3_reload_refreshes_from_disk(
        self, registry_path: Path, populated_registry: Registry
    ):
        """AC-3: reload() re-reads the database."""
        # Directly mutate the DB through a second connection, bypassing Registry.
        import sqlite3

        conn = sqlite3.connect(str(registry_path))
        conn.execute("UPDATE instances SET port = 9999 WHERE id = 'test-model'")
        conn.commit()
        conn.close()
        populated_registry.reload()
        assert populated_registry.get("test-model").port == 9999


# ── AC-15: Path validation ─────────────────────────────────────────────────────


class TestPathValidation:
    """AC-15: Only absolute .gguf paths without traversal are accepted."""

    def test_AC15_valid_gguf_path_accepted(self):
        """AC-15: .gguf path is accepted."""
        _validate_path("/models/llama.gguf")  # should not raise

    def test_AC15_non_gguf_path_rejected(self):
        """AC-15: non-.gguf extension raises ValueError."""
        with pytest.raises(ValueError, match=r"\.gguf"):
            _validate_path("/models/llama.bin")

    def test_AC15_path_traversal_rejected(self):
        """AC-15: path with '..' components raises ValueError."""
        with pytest.raises(ValueError, match="traversal"):
            _validate_path("/models/../etc/passwd.gguf")

    def test_AC15_empty_path_is_allowed(self):
        """AC-15: empty path (pre-download) is accepted."""
        _validate_path("")  # no raise for blank

    def test_AC15_add_with_bad_path_raises(self, empty_registry: Registry):
        """AC-15: registry.add() rejects entry with bad path."""
        bad = RegistryEntry(id="bad-model", path="/models/bad.txt", port=9091, context_length=4096)
        with pytest.raises(ValueError):
            empty_registry.add(bad)


# ── AC-16: ID validation ───────────────────────────────────────────────────────


class TestIdValidation:
    """AC-16: model IDs must match ^[a-z0-9][a-z0-9_-]{1,62}[a-z0-9]$"""

    def test_AC16_valid_id_accepted(self):
        """AC-16: valid id passes."""
        _validate_id("llama3-8b-q4-local")  # should not raise

    def test_AC16_id_with_uppercase_rejected(self):
        """AC-16: uppercase letters raise ValueError."""
        with pytest.raises(ValueError):
            _validate_id("Llama3-8B")

    def test_AC16_id_too_short_rejected(self):
        """AC-16: single char id rejected."""
        with pytest.raises(ValueError):
            _validate_id("a")

    def test_AC16_id_with_spaces_rejected(self):
        """AC-16: spaces not allowed."""
        with pytest.raises(ValueError):
            _validate_id("my model")

    def test_AC16_id_starting_with_dash_rejected(self):
        """AC-16: leading dash rejected."""
        with pytest.raises(ValueError):
            _validate_id("-model")

    def test_AC16_id_with_valid_underscores(self):
        """AC-16: underscores in middle are allowed."""
        _validate_id("my_model_v2")  # should not raise


# ── AC-17: Unregister running instance ────────────────────────────────────────


class TestUnregisterRunningBlock:
    """AC-17: unregistering a running instance must be refused."""

    def test_AC17_remove_succeeds_when_not_running(self, populated_registry: Registry):
        """AC-17: remove() on a non-running model succeeds."""
        populated_registry.remove("test-model")
        assert populated_registry.get("test-model") is None

    def test_AC17_remove_missing_raises_key_error(self, empty_registry: Registry):
        """AC-17: removing a model not in registry raises KeyError."""
        with pytest.raises(KeyError):
            empty_registry.remove("nonexistent")


# ── AC-18: Persistence ─────────────────────────────────────────────────────────


class TestPersistence:
    """AC-18: Registry additions and deletions are written atomically."""

    def test_AC18_add_then_remove_persists_correctly(
        self, registry_path: Path, empty_registry: Registry, sample_entry: RegistryEntry
    ):
        """AC-18: add then remove leaves empty YAML on disk."""
        empty_registry.add(sample_entry)
        empty_registry.remove("test-model")

        reloaded = Registry(registry_path)
        assert reloaded.entries == []

    def test_AC18_multiple_entries_all_persisted(self, empty_registry: Registry):
        """AC-18: multiple adds all appear in reloaded registry."""
        for i in range(3):
            e = RegistryEntry(
                id=f"model-{i:02d}",
                path=f"/models/model-{i}.gguf",
                port=9090 + i,
                context_length=4096,
            )
            empty_registry.add(e)

        reloaded = Registry(empty_registry._path)
        assert len(reloaded.entries) == 3


# ── RM-49: legacy registry.yaml → SQLite migration ─────────────────────────────


class TestLegacyYamlMigration:
    """RM-49: a pre-existing registry.yaml is imported into the new DB once,
    non-destructively, the first time Registry opens a not-yet-existing
    .db path next to it."""

    def test_migration_from_legacy_yaml(self, tmp_path: Path):
        import yaml

        legacy = tmp_path / "registry.yaml"
        legacy.write_text(
            yaml.safe_dump(
                {
                    "models": [
                        {
                            "id": "legacy-model",
                            "port": 8080,
                            "context_length": 4096,
                            "family": "llama3",
                            "quantization": "Q4_0",
                            "path": "/models/legacy-model.gguf",
                            "downloaded": True,
                            "hf_repo": "org/repo",
                            "hf_filename": "legacy-model.gguf",
                        }
                    ]
                }
            )
        )
        db_path = tmp_path / "registry.db"

        registry = Registry(db_path)

        assert db_path.exists()
        assert not legacy.exists()
        assert (tmp_path / "registry.yaml.bak").exists()
        entry = registry.get("legacy-model")
        assert entry is not None
        assert entry.family == "llama3"
        assert entry.path == "/models/legacy-model.gguf"
        assert entry.hf_filenames == ["legacy-model.gguf"]

    def test_no_migration_when_no_legacy_file(self, tmp_path: Path):
        db_path = tmp_path / "registry.db"
        registry = Registry(db_path)
        assert registry.entries == []
        assert not (tmp_path / "registry.yaml.bak").exists()


# ── RM-51: models/instances schema split ────────────────────────────────────────


def _write_legacy_single_table_db(path: Path, rows: list[dict]) -> None:
    """Build a pre-RM-51 single-table registry.db by hand, for migration tests."""
    import sqlite3

    conn = sqlite3.connect(str(path))
    conn.executescript(
        """
        CREATE TABLE models (
            id TEXT PRIMARY KEY, context_length INTEGER NOT NULL,
            port INTEGER NOT NULL, path TEXT NOT NULL DEFAULT '',
            family TEXT NOT NULL DEFAULT '', quantization TEXT NOT NULL DEFAULT '',
            backend TEXT NOT NULL DEFAULT 'llama_cpp', modality TEXT NOT NULL DEFAULT 'text',
            mmproj_path TEXT NOT NULL DEFAULT '', downloaded INTEGER NOT NULL DEFAULT 0,
            discovery INTEGER NOT NULL DEFAULT 0, rss_estimate_mb INTEGER,
            hf_repo TEXT NOT NULL DEFAULT '', hf_sha256 TEXT NOT NULL DEFAULT '',
            hf_filenames TEXT NOT NULL DEFAULT '[]',
            vae_path TEXT NOT NULL DEFAULT '', clip_l_path TEXT NOT NULL DEFAULT '',
            t5xxl_path TEXT NOT NULL DEFAULT '', cfg_scale REAL,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    for row in rows:
        cols = ", ".join(row.keys())
        placeholders = ", ".join("?" for _ in row)
        conn.execute(f"INSERT INTO models ({cols}) VALUES ({placeholders})", tuple(row.values()))
    conn.commit()
    conn.close()


class TestModelsInstancesSplitMigration:
    """RM-51: a pre-existing single-table registry.db is split into
    (models, instances) in place, non-destructively, the first time Registry
    opens it."""

    def test_migration_from_legacy_single_table(self, registry_path: Path):
        _write_legacy_single_table_db(
            registry_path,
            [
                {
                    "id": "text-model",
                    "context_length": 4096,
                    "port": 8080,
                    "path": "/models/text-model.gguf",
                    "family": "llama3",
                    "quantization": "Q4_0",
                    "downloaded": 1,
                },
                {
                    "id": "flux-model",
                    "context_length": 0,
                    "port": 8199,
                    "path": "/models/flux1-dev.gguf",
                    "backend": "sd_cpp",
                    "modality": "image",
                    "downloaded": 1,
                    "vae_path": "/models/ae.safetensors",
                    "cfg_scale": 1.0,
                },
            ],
        )

        registry = Registry(registry_path)

        assert len(registry.entries) == 2
        assert len(registry.list_catalog()) == 2
        text_entry = registry.get("text-model")
        assert text_entry is not None
        assert text_entry.model_id == "text-model"
        assert text_entry.family == "llama3"
        flux_entry = registry.get("flux-model")
        assert flux_entry is not None
        assert flux_entry.vae_path == "/models/ae.safetensors"
        assert flux_entry.cfg_scale == 1.0
        assert registry.get_catalog("flux-model") is not None

    def test_migration_is_idempotent_and_creates_backup(self, registry_path: Path):
        _write_legacy_single_table_db(
            registry_path, [{"id": "m", "context_length": 4096, "port": 8080}]
        )
        Registry(registry_path)
        backup = registry_path.with_name(registry_path.name + ".pre-rm51.bak")
        assert backup.exists()
        backup_bytes = backup.read_bytes()

        # Reopening again must not re-migrate or touch the backup.
        registry2 = Registry(registry_path)
        assert backup.read_bytes() == backup_bytes
        assert registry2.get("m") is not None

    def test_no_migration_for_already_new_schema(self, registry_path: Path):
        registry = Registry(registry_path)
        registry.add(RegistryEntry(id="test-model", port=8080, context_length=4096))
        assert not registry_path.with_name(registry_path.name + ".pre-rm51.bak").exists()

        Registry(registry_path)
        assert not registry_path.with_name(registry_path.name + ".pre-rm51.bak").exists()

    def test_no_migration_for_brand_new_file(self, registry_path: Path):
        registry = Registry(registry_path)
        assert registry.entries == []
        assert registry.list_catalog() == []
        assert not registry_path.with_name(registry_path.name + ".pre-rm51.bak").exists()


class TestCatalogInstanceSplit:
    """RM-51: catalog and instance CRUD, and the core bug-fix invariant —
    removing an instance never removes its catalog entry."""

    def test_remove_instance_leaves_catalog_intact(self, populated_registry: Registry):
        """The literal regression test for the reported incident."""
        populated_registry.remove("test-model")
        assert populated_registry.get("test-model") is None
        assert populated_registry.get_catalog("test-model") is not None

    def test_add_instance_against_missing_catalog_raises(self, empty_registry: Registry):
        with pytest.raises(ValueError, match="No catalog entry"):
            empty_registry.add_instance("new-instance", "nonexistent-model", port=8080)

    def test_add_instance_creates_second_instance_of_same_catalog(
        self, populated_registry: Registry
    ):
        populated_registry.add_instance("test-model-2", "test-model", port=8081)
        assert populated_registry.get("test-model") is not None
        second = populated_registry.get("test-model-2")
        assert second is not None
        assert second.model_id == "test-model"
        assert second.path == "/models/test-model.gguf"  # carried from the catalog

    def test_add_instance_validates_path_against_backend(self, empty_registry: Registry):
        from prometheus_manager_core.registry import CatalogEntry

        empty_registry.add_catalog(CatalogEntry(id="bad-catalog", path="/models/bad.bin"))
        with pytest.raises(ValueError, match=r"\.gguf"):
            empty_registry.add_instance("bad-instance", "bad-catalog", port=8080)

    def test_archive_catalog_with_live_instance_raises(self, populated_registry: Registry):
        from prometheus_manager_core.registry import RegistryIntegrityError

        with pytest.raises(RegistryIntegrityError):
            populated_registry.archive_catalog("test-model")

    def test_archive_catalog_succeeds_after_instance_removed(self, populated_registry: Registry):
        """RM-74: archiving takes the model out of every operational path — it
        stops routing, listing and accepting instances — without deleting the
        row, so its name stays retired and its metadata stays answerable."""
        populated_registry.remove("test-model")
        populated_registry.archive_catalog("test-model")
        assert populated_registry.get_catalog("test-model") is None
        assert [c.id for c in populated_registry.list_archived()] == ["test-model"]

    def test_update_catalog_owned_field_via_instance_keyed_update(
        self, populated_registry: Registry
    ):
        populated_registry.update("test-model", path="/models/renamed.gguf")
        assert populated_registry.get_catalog("test-model").path == "/models/renamed.gguf"
        assert populated_registry.get("test-model").path == "/models/renamed.gguf"

    def test_update_catalog_with_no_instance_yet(self, empty_registry: Registry):
        """The download-flow regression test: a freshly-downloaded catalog
        entry has no instance yet, so update_catalog() must not require one."""
        from prometheus_manager_core.registry import CatalogEntry

        empty_registry.add_catalog(CatalogEntry(id="downloading-model"))
        empty_registry.update_catalog(
            "downloading-model", downloaded=True, path="/models/downloading-model.gguf"
        )
        entry = empty_registry.get_catalog("downloading-model")
        assert entry is not None
        assert entry.downloaded is True
        assert entry.path == "/models/downloading-model.gguf"
        assert empty_registry.get("downloading-model") is None  # still no instance


# ── spec-010 AC-1 & AC-2: discovery field ─────────────────────────────────────


class TestDiscoveryField:
    """memory/specs/010 AC-1, AC-2: discovery field persists and defaults to False."""

    def test_AC1_discovery_defaults_to_false(self, registry_path: Path):
        """AC-1: a row inserted without a discovery value defaults to False
        (the schema's own DEFAULT 0), exercised via a raw connection so the
        DB pre-exists with a row Registry itself never wrote."""
        import sqlite3

        from prometheus_manager_core.registry import _SCHEMA_SQL

        conn = sqlite3.connect(str(registry_path))
        conn.executescript(_SCHEMA_SQL)
        conn.execute(
            "INSERT INTO models (id, family, quantization, downloaded) "
            "VALUES ('no-discovery-model', 'llama3', 'Q4_0', 0)"
        )
        conn.execute(
            "INSERT INTO instances (id, model_id, port, context_length) "
            "VALUES ('no-discovery-model', 'no-discovery-model', 9090, 4096)"
        )
        conn.commit()
        conn.close()

        reg = Registry(registry_path)
        entry = reg.get("no-discovery-model")
        assert entry is not None
        assert entry.discovery is False

    def test_AC2_update_discovery_true_persists(
        self, registry_path: Path, empty_registry: Registry, sample_entry: RegistryEntry
    ):
        """AC-2: update(discovery=True) persists and all other fields unchanged."""
        empty_registry.add(sample_entry)
        original_port = sample_entry.port

        empty_registry.update("test-model", discovery=True)

        reloaded = Registry(registry_path)
        entry = reloaded.get("test-model")
        assert entry is not None
        assert entry.discovery is True
        assert entry.port == original_port

    def test_AC2_update_discovery_false_persists(
        self, registry_path: Path, empty_registry: Registry, sample_entry: RegistryEntry
    ):
        """AC-2: update(discovery=False) persists correctly."""
        sample_disc = RegistryEntry(
            id=sample_entry.id,
            path=sample_entry.path,
            port=sample_entry.port,
            context_length=sample_entry.context_length,
            discovery=True,
        )
        empty_registry.add(sample_disc)
        empty_registry.update("test-model", discovery=False)

        reloaded = Registry(registry_path)
        assert reloaded.get("test-model").discovery is False

    def test_discovery_serialized_in_to_dict(self, sample_entry: RegistryEntry):
        """discovery field is always included in to_dict output."""
        entry = RegistryEntry(
            id="x-model",
            port=8080,
            context_length=4096,
            discovery=True,
        )
        d = entry.to_dict()
        assert "discovery" in d
        assert d["discovery"] is True

        entry2 = RegistryEntry(id="y-model", port=8081, context_length=4096)
        d2 = entry2.to_dict()
        assert "discovery" in d2
        assert d2["discovery"] is False


class TestSeedCatalogRM86:
    """RM-86: registry.db is runtime state and no longer tracked; the committed
    seed is registry.db.example. These guard the two ways that arrangement
    silently rots — the seed going missing, and it filling up with one
    machine's absolute paths, which is what happened to the file it replaced."""

    @staticmethod
    def _example_path() -> Path:
        # tests/ -> core/ -> manager/
        return Path(__file__).resolve().parents[2] / "registry.db.example"

    def test_the_seed_catalog_is_committed_and_loadable(self):
        example = self._example_path()
        assert example.exists(), "registry.db.example is the committed seed — it must be there"

        reg = Registry(example)
        assert reg.list_catalog(), "the seed is meant to contain a starting catalog"

    def test_the_seed_carries_no_paths_from_anyone_s_machine(self):
        """The tracked registry.db ended up holding 26 absolute paths under one
        developer's home directory, in a public repo. Relative paths only."""
        reg = Registry(self._example_path())

        absolute = [c.path for c in reg.list_catalog() if c.path.startswith("/")]
        assert absolute == []

    def test_a_missing_registry_is_created_rather_than_fatal(self, tmp_path: Path):
        """Nothing has to be copied for a fresh checkout to work — the seed is
        an offer, not a prerequisite."""
        fresh = tmp_path / "does-not-exist-yet" / "registry.db"
        reg = Registry(fresh)

        assert fresh.exists()
        assert reg.entries == []
