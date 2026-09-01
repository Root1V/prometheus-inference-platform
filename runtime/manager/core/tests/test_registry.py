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

    def test_existing_db_gets_new_columns_migrated(self, registry_path: Path):
        """A registry.db created before RM-52 (no vae_path/clip_l_path/
        t5xxl_path columns) must load cleanly and accept them once reopened —
        CREATE TABLE IF NOT EXISTS alone doesn't backfill an existing table."""
        import sqlite3

        pre_rm52 = Registry(registry_path)
        pre_rm52.add(RegistryEntry(id="old-model", port=8080, context_length=4096))
        conn = sqlite3.connect(str(registry_path))
        conn.execute("ALTER TABLE models DROP COLUMN vae_path")
        conn.execute("ALTER TABLE models DROP COLUMN clip_l_path")
        conn.execute("ALTER TABLE models DROP COLUMN t5xxl_path")
        conn.execute("ALTER TABLE models DROP COLUMN cfg_scale")
        conn.commit()
        conn.close()

        reopened = Registry(registry_path)
        assert reopened.get("old-model").vae_path == ""
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
        conn.execute("UPDATE models SET port = 9999 WHERE id = 'test-model'")
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
            "INSERT INTO models (id, port, context_length, family, quantization, downloaded) "
            "VALUES ('no-discovery-model', 9090, 4096, 'llama3', 'Q4_0', 0)"
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
