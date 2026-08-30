"""Hugging Face model discovery — search, file listing, model card fetch.

Pure helper functions (infer_quant/auto_id/next_free_port/shard_filenames/
ssl_env) are the canonical versions of logic originally written in
runtime/manager/tui/src/prometheus_manager_tui/views/discovery.py — the TUI
now imports them from here instead of duplicating them, so behavior stays
identical between the terminal Discovery view and any HTTP caller (RM-48).

Implements: docs/roadmap.md — RM-48 (Models page: discover/download/manage)
"""

from __future__ import annotations

import contextlib
import os
import re
from collections.abc import Generator
from datetime import datetime
from pathlib import Path
from typing import Any, cast

# Import at module level so tests can patch these (matches downloader.py).
try:
    from huggingface_hub import HfApi, ModelCard, list_models
except ImportError:  # pragma: no cover
    HfApi = None  # type: ignore[assignment,misc]
    ModelCard = None  # type: ignore[assignment,misc]
    list_models = None  # type: ignore[assignment]

# huggingface_hub's own accepted values for list_models(sort=...) — re-declared
# here (rather than importing the private ModelSort_T) so an invalid value from
# an HTTP caller fails with our own clear error instead of a duck-typed one.
SORT_OPTIONS = ("downloads", "likes", "created_at", "last_modified", "trending_score")

_QUANT_RE = re.compile(
    r"(IQ\d[_A-Z0-9]*|Q\d[_A-Z0-9]*|F16|F32|BF16)",
    re.IGNORECASE,
)
_SLUG_RE = re.compile(r"[^a-z0-9]+")
# Matches HuggingFace multi-part shard naming: <prefix>NNNNN-of-MMMMM.gguf
# e.g. "Q4_0/DeepSeek-V3.2-Q4_0-00001-of-00008.gguf"
_SHARD_RE = re.compile(r"^(.*?-?)(\d{5})-of-(\d{5})(\.gguf)$", re.IGNORECASE)


def infer_quant(filename: str) -> str:
    """Return quantization tag inferred from a GGUF filename.

    Covers Q4_K_M, Q8_0, IQ3_M, F16, F32, BF16; returns '?' if unknown.
    """
    m = _QUANT_RE.search(filename)
    return m.group(0).upper() if m else "?"


def auto_id(filename: str, existing_ids: set[str] | None = None) -> str:
    """Slugify a GGUF filename into a registry-safe ID.

    Strips .gguf, lowercases, collapses non-alnum to '-', appends '-local'.
    Collision-resolves with -2, -3, … if existing_ids is provided.
    """
    name = re.sub(r"\.gguf$", "", filename, flags=re.IGNORECASE)
    slug = _SLUG_RE.sub("-", name.lower()).strip("-")
    base = (slug + "-local")[:63]
    if not existing_ids:
        return base
    candidate = base
    suffix = 2
    while candidate in existing_ids:
        candidate = f"{base[:59]}-{suffix}"
        suffix += 1
    return candidate


def next_free_port(used_ports: set[int]) -> int:
    """Return the lowest port >= 8081 not already in use.

    Callers pass {e.port for e in registry.entries}.
    """
    port = 8081
    while port in used_ports:
        port += 1
    return port


def shard_filenames(selected: str, all_files: list[str]) -> list[str]:
    """Return the full ordered list of shard files for a multi-part GGUF model.

    Detects the HuggingFace split-model naming pattern:
        <prefix>NNNNN-of-MMMMM.gguf  (e.g. Q4_0/Model-00001-of-00008.gguf)

    Collects all M sibling shards from *all_files* sharing the same prefix and
    total count, sorted by part number. Returns ``[selected]`` unchanged when
    the filename does not match the pattern (single-file model).
    """
    m = _SHARD_RE.match(selected)
    if not m:
        return [selected]
    prefix, total, ext = m.group(1), m.group(3), m.group(4)
    shards: list[tuple[int, str]] = []
    for f in all_files:
        fm = _SHARD_RE.match(f)
        if (
            fm
            and fm.group(1) == prefix
            and fm.group(3) == total
            and fm.group(4).lower() == ext.lower()
        ):
            shards.append((int(fm.group(2)), f))
    shards.sort()
    return [f for _, f in shards] if shards else [selected]


@contextlib.contextmanager
def ssl_env(ca: Path | str | None) -> Generator[None, None, None]:
    """Temporarily set REQUESTS_CA_BUNDLE for the duration of an HF API call.

    huggingface_hub uses requests under the hood, which honours this env var.
    Safe to call from a worker thread (restores original value on exit).
    """
    if ca is None:
        yield
        return
    key = "REQUESTS_CA_BUNDLE"
    old = os.environ.get(key)
    os.environ[key] = str(ca)
    try:
        yield
    finally:
        if old is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = old


def search_models(
    query: str,
    limit: int = 30,
    token: str | None = None,
    ca_bundle: Path | str | None = None,
    sort: str | None = None,
) -> list[dict[str, Any]]:
    """Search Hugging Face for GGUF-tagged models matching *query*.

    Mirrors the TUI Discovery view's search worker exactly (filter="gguf").
    Returns plain dicts (JSON-serializable) rather than huggingface_hub's
    ModelInfo objects, since this is consumed over HTTP by manager-api.

    *sort* is one of SORT_OPTIONS (downloads/likes/created_at/last_modified/
    trending_score) — huggingface_hub returns results for all of these
    highest/most-recent first already, so there's no separate "direction".
    """
    if list_models is None:
        raise RuntimeError("huggingface-hub is not installed. Run: uv add huggingface-hub")
    if sort is not None and sort not in SORT_OPTIONS:
        raise ValueError(f"Unknown sort {sort!r}. Must be one of {SORT_OPTIONS}")

    with ssl_env(ca_bundle):
        # sort is runtime-validated against SORT_OPTIONS above; cast satisfies
        # list_models()'s narrower Literal type without importing huggingface_hub's
        # private ModelSort_T alias just for an annotation.
        validated_sort = cast("Any", sort)
        results = list(
            list_models(filter="gguf", search=query, limit=limit, token=token, sort=validated_sort)
        )

    return [
        {
            "id": m.id,
            "downloads": getattr(m, "downloads", None),
            "likes": getattr(m, "likes", None),
            "last_modified": _iso(getattr(m, "lastModified", None)),
        }
        for m in results
    ]


def list_model_files(
    repo_id: str,
    token: str | None = None,
    ca_bundle: Path | str | None = None,
) -> list[dict[str, Any]]:
    """List a repo's GGUF files with an inferred quantization tag and size each.

    Uses model_info(files_metadata=True) rather than list_repo_files — the
    latter returns filenames only, with no size information.
    """
    if HfApi is None:
        raise RuntimeError("huggingface-hub is not installed. Run: uv add huggingface-hub")

    with ssl_env(ca_bundle):
        info = HfApi().model_info(repo_id, files_metadata=True, token=token)

    return [
        {"filename": s.rfilename, "quantization": infer_quant(s.rfilename), "size_bytes": s.size}
        for s in info.siblings or []
        if s.rfilename.lower().endswith(".gguf")
    ]


def fetch_model_card(
    repo_id: str,
    token: str | None = None,
    ca_bundle: Path | str | None = None,
) -> dict[str, Any]:
    """Fetch a repo's model card (README + parsed metadata frontmatter).

    No existing precedent in the TUI for this — added new for RM-48's "read
    the model card" requirement.
    """
    if ModelCard is None:
        raise RuntimeError("huggingface-hub is not installed. Run: uv add huggingface-hub")

    with ssl_env(ca_bundle):
        card = ModelCard.load(repo_id, token=token)

    data = getattr(card, "data", None)
    metadata = data.to_dict() if data is not None and hasattr(data, "to_dict") else {}
    return {"repo_id": repo_id, "text": card.text, "metadata": metadata}


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt is not None else None
