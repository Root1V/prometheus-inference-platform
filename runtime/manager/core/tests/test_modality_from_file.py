"""PRM-107 — the weights file contradicts a wrong modality.

A reranker was registered as "text", started without --reranking, and answered
chat requests with plausible nonsense for weeks. Nothing failed: "text" is the
one modality that never errors, which is what made it dangerous as a default.

These build real GGUF headers rather than mocking the reader, because the thing
under test *is* the reading.
"""

from __future__ import annotations

import struct

import pytest

from prometheus_manager_core.hf_discovery import (
    modality_conflict,
    read_gguf_modality_evidence,
)

T_UINT32, T_STRING, T_ARRAY = 4, 8, 9


def _kv(key: str, vtype: int, payload: bytes) -> bytes:
    k = key.encode()
    return struct.pack("<Q", len(k)) + k + struct.pack("<I", vtype) + payload


def _u32(v: int) -> bytes:
    return struct.pack("<I", v)


def _str_array(values: list[str]) -> bytes:
    out = struct.pack("<I", T_STRING) + struct.pack("<Q", len(values))
    for v in values:
        b = v.encode()
        out += struct.pack("<Q", len(b)) + b
    return out


def _gguf(tmp_path, name: str, entries: list[bytes]):
    path = tmp_path / name
    body = b"GGUF" + struct.pack("<I", 3) + struct.pack("<Q", 0) + struct.pack("<Q", len(entries))
    path.write_bytes(body + b"".join(entries))
    return str(path)


def _chat(tmp_path):
    return _gguf(tmp_path, "chat.gguf", [_kv("qwen3.block_count", T_UINT32, _u32(28))])


def _embedding(tmp_path):
    return _gguf(tmp_path, "emb.gguf", [_kv("qwen3.pooling_type", T_UINT32, _u32(3))])


def _reranker(tmp_path):
    return _gguf(
        tmp_path,
        "rr.gguf",
        [
            _kv("qwen3.pooling_type", T_UINT32, _u32(4)),
            _kv("qwen3.classifier.output_labels", T_ARRAY, _str_array(["yes", "no"])),
        ],
    )


# ── What the file asserts ──────────────────────────────────────────────────


def test_a_classifier_head_means_reranker(tmp_path):
    """The real Qwen3-Reranker carries output labels ['yes', 'no']."""
    assert read_gguf_modality_evidence(_reranker(tmp_path)) == "rerank"


def test_pooling_without_a_classifier_means_embedding(tmp_path):
    assert read_gguf_modality_evidence(_embedding(tmp_path)) == "embedding"


def test_a_plain_model_asserts_nothing(tmp_path):
    """Not "text" — nothing. A chat model, a vision model whose projector is a
    separate file, and an image model served by another engine are all
    indistinguishable here, and pretending otherwise is how you reject a
    legitimate registration."""
    assert read_gguf_modality_evidence(_chat(tmp_path)) == ""


def test_an_unreadable_file_asserts_nothing(tmp_path):
    missing = str(tmp_path / "nope.gguf")
    assert read_gguf_modality_evidence(missing) == ""
    junk = tmp_path / "junk.gguf"
    junk.write_bytes(b"not a gguf at all")
    assert read_gguf_modality_evidence(str(junk)) == ""


# ── The conflict, which is asymmetric on purpose ───────────────────────────


def test_the_exact_mistake_that_caused_this(tmp_path):
    conflict = modality_conflict(_reranker(tmp_path), "text")
    assert conflict
    assert "reranker" in conflict
    assert "--modality rerank" in conflict


def test_an_embedding_registered_as_text_is_refused(tmp_path):
    assert modality_conflict(_embedding(tmp_path), "text")


def test_the_right_modality_is_not_a_conflict(tmp_path):
    assert modality_conflict(_reranker(tmp_path), "rerank") == ""
    assert modality_conflict(_embedding(tmp_path), "embedding") == ""


@pytest.mark.parametrize("modality", ["text", "vision", "image", "embedding", "rerank"])
def test_silence_never_blocks_anything(tmp_path, modality):
    """The measured half: of 28 catalogue files, the 4 that disagreed with what
    was registered were all files asserting nothing — two vision, two image.
    Blocking on silence would have rejected every one of them."""
    assert modality_conflict(_chat(tmp_path), modality) == ""


def test_no_path_is_not_a_conflict(tmp_path):
    """A model can be registered before its file is downloaded."""
    assert modality_conflict(None, "text") == ""
    assert modality_conflict("", "text") == ""


# ── Against the real files, not a synthesised header ───────────────────────

_REAL = {
    "runtime/models/downloads/Qwen3-Reranker-0.6B-Q4_K_M.gguf": "rerank",
    "runtime/models/downloads/Qwen3-Embedding-0.6B-Q8_0.gguf": "embedding",
    "runtime/models/downloads/Qwen3-0.6B-IQ4_NL.gguf": "",
}


@pytest.mark.parametrize("rel,expected", sorted(_REAL.items()))
def test_against_the_real_downloaded_weights(rel, expected):
    """Synthesised headers prove the parser; these prove the premise — that
    real Qwen3 GGUFs actually carry these keys. Skipped where the file is not
    present, so the suite still runs on a machine without the downloads.
    """
    from pathlib import Path as _P

    root = _P(__file__).resolve().parents[4]
    path = root / rel
    if not path.exists():
        pytest.skip(f"{rel} not downloaded here")
    assert read_gguf_modality_evidence(path) == expected


# ── PRM-108: cataloguing derives it, registering refuses a wrong one ────────


def test_cataloguing_a_reranker_does_not_leave_it_as_text(tmp_path):
    """The download path. Nobody chose "text" here — it is the dataclass
    default, and RM-89 already established that a default indistinguishable
    from a choice gets filled in at add_catalog() rather than at each call site.
    """
    from prometheus_manager_core.registry import CatalogEntry, Registry

    reg = Registry(tmp_path / "reg.db")
    reg.add_catalog(CatalogEntry(id="rr-model", path=_reranker(tmp_path), downloaded=True))
    assert reg.get_catalog("rr-model").modality == "rerank"


def test_cataloguing_leaves_a_silent_file_alone(tmp_path):
    """A vision model's projector is a separate file, so the weights assert
    nothing — overriding the caller here would make vision unregisterable."""
    from prometheus_manager_core.registry import CatalogEntry, Registry

    reg = Registry(tmp_path / "reg.db")
    reg.add_catalog(
        CatalogEntry(id="vl-model", path=_chat(tmp_path), modality="vision", downloaded=True)
    )
    assert reg.get_catalog("vl-model").modality == "vision"


def test_registering_an_instance_with_the_wrong_modality_is_refused(tmp_path):
    """The explicit path. Here someone *did* choose, so correcting silently
    would hide the mistake instead of teaching it."""
    from prometheus_manager_core.registry import Registry, RegistryEntry

    reg = Registry(tmp_path / "reg.db")
    with pytest.raises(ValueError, match="reranker"):
        reg.add(
            RegistryEntry(
                id="rr-instance",
                path=_reranker(tmp_path),
                port=9999,
                context_length=4096,
                modality="text",
                backend="llama_cpp",
            )
        )


# ── PRM-109: the model owns it, not the instance ───────────────────────────


def test_every_instance_of_a_model_reports_the_catalog_modality(tmp_path):
    """RM-70 said putting modality on the model row made replicas disagreeing
    about it impossible. It did not: the entry handed to the gateway read the
    instance copy, so two replicas of one model could route differently. This
    is that guarantee, finally enforced where it is read.
    """
    from prometheus_manager_core.registry import CatalogEntry, Registry

    db = tmp_path / "reg.db"
    reg = Registry(db)
    reg.add_catalog(CatalogEntry(id="rr-model", path=_reranker(tmp_path), downloaded=True))
    for port in (9101, 9102):
        reg.add_instance(
            id=f"rr-{port}",
            model_id="rr-model",
            port=port,
            backend="llama_cpp",
            modality="rerank",
            context_length=4096,
        )

    # Force the instance rows out of step the only way left — straight at the
    # database, which is what a stale row from before this change looks like.
    import sqlite3

    raw = sqlite3.connect(db)
    raw.execute("UPDATE instances SET modality = 'text' WHERE id = 'rr-9101'")
    raw.commit()
    raw.close()

    reloaded = Registry(db)
    served = {e.id: e.modality for e in reloaded.entries}
    assert served == {"rr-9101": "rerank", "rr-9102": "rerank"}, (
        "an instance row disagreeing with its model must not change how it routes"
    )
