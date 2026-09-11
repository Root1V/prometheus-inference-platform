"""RM-70 — model slug/name/modality and per-model instance labels.

One string used to be the catalog id, the process id, the name clients send
and the billing/scope key at once. These cover the additive first step: the
catalog gains a public `slug`, a display `name` and the `modality` that was
living on each instance, and every instance gains a `#N` label unique within
its model — so adding a replica stops asking an operator to invent a
globally-unique id.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from prometheus_manager_core.registry import (
    CatalogEntry,
    Registry,
    RegistryEntry,
    RegistryIntegrityError,
)


def _pre_rm70_db(path: Path) -> sqlite3.Connection:
    """A registry.db in the RM-51 shape — split tables, but no slug/name/label."""
    conn = sqlite3.connect(str(path))
    conn.executescript(
        """
        CREATE TABLE models (
            id TEXT PRIMARY KEY, path TEXT NOT NULL DEFAULT '',
            family TEXT NOT NULL DEFAULT '', quantization TEXT NOT NULL DEFAULT '',
            downloaded INTEGER NOT NULL DEFAULT 0, hf_repo TEXT NOT NULL DEFAULT '',
            hf_sha256 TEXT NOT NULL DEFAULT '', hf_filenames TEXT NOT NULL DEFAULT '[]',
            mmproj_path TEXT NOT NULL DEFAULT '', vae_path TEXT NOT NULL DEFAULT '',
            clip_l_path TEXT NOT NULL DEFAULT '', t5xxl_path TEXT NOT NULL DEFAULT '',
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE instances (
            id TEXT PRIMARY KEY, model_id TEXT NOT NULL REFERENCES models(id),
            port INTEGER NOT NULL, backend TEXT NOT NULL DEFAULT 'llama_cpp',
            modality TEXT NOT NULL DEFAULT 'text', context_length INTEGER NOT NULL,
            discovery INTEGER NOT NULL DEFAULT 0, rss_estimate_mb INTEGER,
            cfg_scale REAL, created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    return conn


class TestIdentityBackfill:
    def test_a_pre_rm70_database_keeps_every_id(self, registry_path: Path):
        """The migration is additive on purpose: lifecycle.py names each running
        process's PID and log file after its instance id, so renaming ids under
        a live manager would strand the processes it is supervising.
        """
        conn = _pre_rm70_db(registry_path)
        conn.execute("INSERT INTO models (id, path) VALUES ('llama', '/m/llama.gguf')")
        conn.execute(
            "INSERT INTO instances (id, model_id, port, context_length, modality) "
            "VALUES ('llama', 'llama', 8080, 4096, 'text')"
        )
        conn.commit()
        conn.close()

        registry = Registry(registry_path)

        assert [e.id for e in registry.entries] == ["llama"]
        assert registry.get_catalog("llama") is not None

    def test_slug_and_name_backfill_from_the_existing_id(self, registry_path: Path):
        """Routing must be unchanged on the first boot after upgrading — the
        current id *is* the name clients already send.
        """
        conn = _pre_rm70_db(registry_path)
        conn.execute("INSERT INTO models (id, path) VALUES ('llama', '/m/llama.gguf')")
        conn.commit()
        conn.close()

        catalog = Registry(registry_path).get_catalog("llama")

        assert catalog is not None
        assert catalog.slug == "llama"
        assert catalog.name == "llama"

    def test_modality_moves_up_from_the_instances(self, registry_path: Path):
        conn = _pre_rm70_db(registry_path)
        conn.execute("INSERT INTO models (id, path) VALUES ('embedder', '/m/e.gguf')")
        conn.execute(
            "INSERT INTO instances (id, model_id, port, context_length, modality) "
            "VALUES ('embedder', 'embedder', 8086, 4096, 'embedding')"
        )
        conn.commit()
        conn.close()

        catalog = Registry(registry_path).get_catalog("embedder")

        assert catalog is not None
        assert catalog.modality == "embedding"

    def test_a_catalog_row_with_no_instances_defaults_to_text(self, registry_path: Path):
        """A downloaded-but-never-launched model has no instance to ask."""
        conn = _pre_rm70_db(registry_path)
        conn.execute("INSERT INTO models (id, path) VALUES ('never-run', '/m/n.gguf')")
        conn.commit()
        conn.close()

        catalog = Registry(registry_path).get_catalog("never-run")

        assert catalog is not None
        assert catalog.modality == "text"

    def test_replicas_are_labelled_in_creation_order(self, registry_path: Path):
        """The oldest replica keeps the lowest number, so a label doesn't move
        between instances when the migration runs.
        """
        conn = _pre_rm70_db(registry_path)
        conn.execute("INSERT INTO models (id, path) VALUES ('llama', '/m/llama.gguf')")
        for instance_id, created in (("llama", "2026-01-01"), ("llama-second", "2026-06-01")):
            conn.execute(
                "INSERT INTO instances (id, model_id, port, context_length, created_at) "
                "VALUES (?, 'llama', 8080, 4096, ?)",
                (instance_id, created),
            )
        conn.commit()
        conn.close()

        registry = Registry(registry_path)
        labels = {e.id: e.label for e in registry.entries}

        assert labels == {"llama": "#1", "llama-second": "#2"}

    def test_running_the_migration_twice_changes_nothing(self, registry_path: Path):
        conn = _pre_rm70_db(registry_path)
        conn.execute("INSERT INTO models (id, path) VALUES ('llama', '/m/llama.gguf')")
        conn.execute(
            "INSERT INTO instances (id, model_id, port, context_length) "
            "VALUES ('llama', 'llama', 8080, 4096)"
        )
        conn.commit()
        conn.close()

        first = {e.id: e.label for e in Registry(registry_path).entries}
        second = {e.id: e.label for e in Registry(registry_path).entries}

        assert first == second == {"llama": "#1"}


class TestLabelAssignment:
    def _seed(self, registry_path: Path) -> Registry:
        registry = Registry(registry_path)
        registry.add(
            RegistryEntry(id="llama", context_length=4096, port=8080, path="/m/llama.gguf")
        )
        return registry

    def test_a_new_replica_is_labelled_without_the_operator_naming_it(self, registry_path: Path):
        registry = self._seed(registry_path)

        registry.add_instance("llama-b", "llama", port=8081, context_length=4096)

        labels = {e.id: e.label for e in registry.entries}
        assert labels == {"llama": "#1", "llama-b": "#2"}

    def test_a_removed_label_is_not_reused(self, registry_path: Path):
        """Counting rows would hand #2 to a different instance later, so the
        same label would refer to two different processes in the logs.
        """
        registry = self._seed(registry_path)
        registry.add_instance("llama-b", "llama", port=8081, context_length=4096)
        registry.add_instance("llama-c", "llama", port=8082, context_length=4096)
        registry.remove("llama-b")

        registry.add_instance("llama-d", "llama", port=8083, context_length=4096)

        labels = {e.id: e.label for e in registry.entries}
        assert labels == {"llama": "#1", "llama-c": "#3", "llama-d": "#4"}

    def test_labels_are_per_model_not_global(self, registry_path: Path):
        registry = self._seed(registry_path)
        registry.add_catalog(CatalogEntry(id="other", path="/m/other.gguf"))

        registry.add_instance("other-a", "other", port=8090, context_length=4096)

        labels = {e.id: e.label for e in registry.entries}
        assert labels["llama"] == "#1"
        assert labels["other-a"] == "#1"


class TestSlugUniqueness:
    def test_two_models_cannot_share_a_slug(self, registry_path: Path):
        """A slug is what clients route on, so a duplicate would make routing
        ambiguous rather than merely untidy.
        """
        registry = Registry(registry_path)
        registry.add_catalog(CatalogEntry(id="model-a", slug="shared", path="/m/a.gguf"))

        with pytest.raises(RegistryIntegrityError):
            registry.add_catalog(CatalogEntry(id="model-b", slug="shared", path="/m/b.gguf"))
