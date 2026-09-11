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
    # RM-08 phase 2: which configured manager node this model is served from.
    # "" for the single-node case (MANAGER_URL) or the static registry.yaml fallback.
    node: str = ""
    # RM-09: "text" (default, /v1/chat/completions), "vision" (chat completions
    # with image content parts), or "embedding" (/v1/embeddings). Determines
    # request routing/validation — see memory/wiki/model-registry.md.
    modality: str = "text"
    # RM-51: which catalog (models) entry this instance belongs to, on its
    # manager node. Falls back to this entry's own id (matching manager-api's
    # pre-upgrade behavior) when a node hasn't shipped model_id yet.
    # RM-57: this is now also the *logical* name a client can route to — every
    # instance sharing a model_id is a replica of the same servable model.
    model_id: str = ""
    # RM-70: the catalog's public, immutable name. Falls back to model_id for a
    # manager node that hasn't shipped it yet, so a rolling deploy resolves the
    # same way it did before.
    model_slug: str = ""
    # RM-70: this instance's handle within its model ("#1", "#2"). Display and
    # ops only — never a routing key.
    label: str = ""


@dataclass(frozen=True)
class ModelResolution:
    """What a client-facing model name resolves to — RM-57.

    `members` is every active instance serving `name`. It has one element for
    the ordinary single-instance case, which is why routing through this is
    behaviour-preserving; more than one means replicas, and picking among them
    is RM-58's job.
    """

    name: str
    members: tuple[ModelEntry, ...]
    # Agreed across all members. context_length is the *minimum* of the group:
    # a request that fits the smallest replica fits every one of them, so
    # validation can happen before a replica is chosen.
    modality: str
    context_length: int
    # RM-69: the catalog id shared by the group — what pricing, usage and the
    # spend cap key off. Deliberately NOT `name`: a client addressing a replica
    # by its own instance id would otherwise miss the price table entirely,
    # which records the request at NULL cost *and* skips the budget reservation,
    # so the same model would be free and uncapped under one of its names.
    model_key: str = ""
    # Set when members disagree on something that makes the group unroutable
    # (see _resolve_group) — the caller turns this into a 400 rather than
    # silently serving from an arbitrary subset.
    mismatch: str | None = None


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
                modality=entry.get("modality", "text"),
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

    # ── RM-57: logical (catalog) names as routable groups ────────────────────

    def resolve(self, name: str) -> ModelResolution | None:
        """Resolve a client-supplied model name to the instances serving it.

        Group first, instance id second — and deliberately so. manager-api
        sets `model_id = id` when a model is registered directly, so the first
        instance of a model usually *is* named after its catalog entry. Looking
        up the instance id first would therefore match that one exactly and
        never notice the replicas added later under the same catalog name: the
        operator would think they were load-balancing while every request went
        to a single instance.

        Returns None only when the name is unknown entirely. A known name whose
        instances are all stopped resolves with no members, so the caller can
        still answer "registered but not loaded" (503) rather than demoting it
        to "no such model" (400).
        """
        # RM-70: the slug is the public name, and the two older spellings stay
        # resolvable as aliases so every token and SDK call issued before the
        # identity split keeps working. Today the backfill makes all three the
        # same string; they only diverge once a model is given a real slug.
        known = [m for m in self._models.values() if m.model_slug and m.model_slug == name]
        if not known:
            known = [m for m in self._models.values() if m.model_id == name]
        if not known:
            # Not a catalog name — fall back to addressing one instance
            # directly by its own id, which stays supported.
            entry = self._models.get(name)
            if entry is None:
                return None
            known = [entry]
        members = [m for m in known if m.backend_url is not None]
        if not members:
            return ModelResolution(
                name=name,
                members=(),
                modality=known[0].modality,
                context_length=known[0].context_length,
                model_key=known[0].model_slug or known[0].model_id or name,
            )
        # Stable order so "which replica" is deterministic until RM-58 makes
        # it a real decision.
        members.sort(key=lambda m: (m.node, m.id))
        return self._resolve_group(name, members)

    @staticmethod
    def _resolve_group(name: str, members: list[ModelEntry]) -> ModelResolution:
        modalities = {m.modality for m in members}
        mismatch = None
        if len(modalities) > 1:
            # Serving a chat request from an embedding backend would produce
            # confident nonsense rather than an error, so refuse the whole
            # group and name the disagreement instead of quietly dropping the
            # odd instance out.
            detail = ", ".join(
                f"{m.id}={m.modality!r}" for m in sorted(members, key=lambda m: m.id)
            )
            mismatch = (
                f"Instances serving {name!r} disagree on modality ({detail}). "
                "Fix the mismatched instance, or give it its own model id."
            )
        return ModelResolution(
            name=name,
            members=tuple(members),
            modality=members[0].modality,
            context_length=min(m.context_length for m in members),
            mismatch=mismatch,
            model_key=members[0].model_slug or members[0].model_id or name,
        )

    def list_served_names(self) -> list[ModelResolution]:
        """One entry per servable model — RM-70.

        Models, not names: the catalog id and each instance id still *resolve*
        as aliases, but advertising them here would show a client three
        entries for what is one model with one replica, and an SDK building a
        model picker from this would render duplicates. A client that wants a
        specific replica addresses it deliberately; it shouldn't have to tell
        replicas apart from models in a list.
        """
        resolutions: dict[str, ModelResolution] = {}
        for entry in self.list_active_models():
            name = entry.model_slug or entry.model_id or entry.id
            if name and name not in resolutions:
                resolved = self.resolve(name)
                if resolved is not None:
                    resolutions[name] = resolved
        return list(resolutions.values())


def load_registry(path: Path | str | None = None) -> ModelRegistry:
    """Convenience factory — creates and returns a ModelRegistry."""
    return ModelRegistry(path)
