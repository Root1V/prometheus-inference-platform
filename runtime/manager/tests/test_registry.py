"""Tests for Registry: AC-3, AC-15, AC-16, AC-17, AC-18."""

from __future__ import annotations

from pathlib import Path

import pytest

from prometheus_manager.registry import (
    Registry,
    RegistryEntry,
    _validate_id,
    _validate_path,
)

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
        """AC-3: reload() re-reads the YAML file."""
        # Directly mutate YAML
        import yaml

        with open(registry_path) as f:
            data = yaml.safe_load(f)
        data["models"][0]["port"] = 9999
        with open(registry_path, "w") as f:
            yaml.safe_dump(data, f)
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


# ── spec-010 AC-1 & AC-2: discovery field ─────────────────────────────────────


class TestDiscoveryField:
    """memory/specs/010 AC-1, AC-2: discovery field persists and defaults to False."""

    def test_AC1_discovery_defaults_to_false(self, registry_path: Path):
        """AC-1: entries loaded from YAML without a discovery key default to False."""
        import yaml

        data = {
            "models": [
                {
                    "id": "no-discovery-model",
                    "port": 9090,
                    "context_length": 4096,
                    "family": "llama3",
                    "quantization": "Q4_0",
                    "downloaded": False,
                }
            ]
        }
        registry_path.write_text(yaml.safe_dump(data))
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
