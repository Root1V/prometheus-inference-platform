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


class TestArchivedModels:
    """RM-74: a published name is never handed to a different model.

    Usage is billed against the slug and `model:<slug>` grants are written
    against it, so recycling one would merge two models' invoices *and*
    silently give everyone who could reach the old model access to the new.
    Archiving rather than deleting also keeps the answer to "what was this
    model?" for usage already recorded under that name.
    """

    def _model(self, registry: Registry, model_id: str, slug: str) -> None:
        registry.add_catalog(CatalogEntry(id=model_id, slug=slug, path=f"/m/{model_id}.gguf"))

    def test_an_archived_model_leaves_every_operational_path(self, registry_path: Path):
        registry = Registry(registry_path)
        self._model(registry, "old-model", "retired-name")

        registry.archive_catalog("old-model")

        assert registry.get_catalog("old-model") is None
        assert [c.id for c in registry.list_catalog()] == []
        assert [c.id for c in registry.list_archived()] == ["old-model"]

    def test_its_slug_cannot_be_taken_by_another_model(self, registry_path: Path):
        registry = Registry(registry_path)
        self._model(registry, "old-model", "retired-name")
        registry.archive_catalog("old-model")

        with pytest.raises(RegistryIntegrityError) as exc:
            self._model(registry, "new-model", "retired-name")

        assert "old-model" in str(exc.value)

    def test_its_id_cannot_be_taken_either(self, registry_path: Path):
        """The upsert is INSERT OR REPLACE, so without this guard a new model
        reusing the id would overwrite the archived row and inherit its slug
        and history."""
        registry = Registry(registry_path)
        self._model(registry, "old-model", "retired-name")
        registry.archive_catalog("old-model")

        with pytest.raises(RegistryIntegrityError):
            self._model(registry, "old-model", "a-totally-different-name")

        assert [c.slug for c in registry.list_archived()] == ["retired-name"]

    def test_naming_a_live_model_cannot_take_a_retired_name(self, registry_path: Path):
        """set_slug() goes through the same guard, or the one-time naming path
        would be a way around the reservation."""
        registry = Registry(registry_path)
        self._model(registry, "gone", "retired-name")
        registry.archive_catalog("gone")
        registry.add(
            RegistryEntry(id="live-model", context_length=4096, port=8080, path="/m/l.gguf")
        )

        with pytest.raises(RegistryIntegrityError):
            registry.set_slug("live-model", "retired-name")

    def test_restoring_brings_it_back_with_its_name(self, registry_path: Path):
        """The escape hatch that lets archiving be the default: archiving the
        wrong model is undoable, losing its name is not."""
        registry = Registry(registry_path)
        self._model(registry, "old-model", "retired-name")
        registry.archive_catalog("old-model")

        registry.restore_catalog("old-model")

        restored = registry.get_catalog("old-model")
        assert restored is not None
        assert restored.slug == "retired-name"
        assert restored.archived_at is None
        assert registry.list_archived() == []

    def test_restoring_something_that_was_never_archived_raises(self, registry_path: Path):
        with pytest.raises(KeyError):
            Registry(registry_path).restore_catalog("never-existed")

    def test_a_different_name_is_unaffected(self, registry_path: Path):
        registry = Registry(registry_path)
        self._model(registry, "old-model", "retired-name")
        registry.archive_catalog("old-model")

        self._model(registry, "new-model", "another-name")

        assert registry.get_catalog("new-model") is not None

    def test_archiving_survives_a_reload(self, registry_path: Path):
        registry = Registry(registry_path)
        self._model(registry, "old-model", "retired-name")
        registry.archive_catalog("old-model")

        reloaded = Registry(registry_path)

        assert reloaded.get_catalog("old-model") is None
        assert [c.id for c in reloaded.list_archived()] == ["old-model"]
