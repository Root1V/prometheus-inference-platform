"""PRM-194 — the model says its own context length.

`pmgr register` prompted `Context length [4096]` and nothing anywhere compared
that number to the model. Nine of the ten `llama_cpp` rows in this deployment
were therefore a default somebody pressed enter on, audited by reading
`<arch>.context_length` out of each GGUF rather than trusting the registry:

    qwen36-35b-a3b-q4     4,096 of 262,144     1.6%
    qwen3vl-8b-q4         8,192 of 262,144     3.1%
    qwen3-8b-q6         131,072 of  40,960   320%

**The last row is the one that is actually wrong**, and it fails in the
direction nobody notices: llama.cpp does not refuse a context beyond the trained
length — it RoPE-scales and keeps answering, worse, with no error and nothing in
the registry to say so. It also held 18.0 GiB of KV cache to do it, against
5.6 GiB at its real length.

These tests build GGUF headers byte by byte rather than shipping fixtures, so
what is pinned is the parsing rather than one vendor's file.
"""

from __future__ import annotations

import struct

import pytest

from prometheus_manager_core.hf_discovery import read_gguf_context_length

_STRING, _ARRAY, _UINT32, _UINT64 = 8, 9, 4, 10


def _kv(key: str, value_type: int, payload: bytes) -> bytes:
    k = key.encode()
    return struct.pack("<Q", len(k)) + k + struct.pack("<I", value_type) + payload


def _string(value: str) -> bytes:
    b = value.encode()
    return struct.pack("<Q", len(b)) + b


def _gguf(entries: list[bytes], magic: bytes = b"GGUF") -> bytes:
    return (
        magic
        + struct.pack("<I", 3)
        + struct.pack("<Q", 0)
        + struct.pack("<Q", len(entries))
        + b"".join(entries)
    )


def _write(tmp_path, data: bytes, name: str = "m.gguf"):
    p = tmp_path / name
    p.write_bytes(data)
    return p


# ── reading it ─────────────────────────────────────────────────────────────


def test_it_reads_the_architectures_own_key(tmp_path):
    f = _write(
        tmp_path,
        _gguf(
            [
                _kv("general.architecture", _STRING, _string("qwen35moe")),
                _kv("qwen35moe.context_length", _UINT32, struct.pack("<I", 262144)),
            ]
        ),
    )
    assert read_gguf_context_length(f) == 262144


def test_another_architectures_key_is_not_taken(tmp_path):
    """A file can carry more than one `*.context_length`; only the one belonging
    to its own architecture is the model's."""
    f = _write(
        tmp_path,
        _gguf(
            [
                _kv("general.architecture", _STRING, _string("llama")),
                _kv("clip.context_length", _UINT32, struct.pack("<I", 77)),
                _kv("llama.context_length", _UINT32, struct.pack("<I", 131072)),
            ]
        ),
    )
    assert read_gguf_context_length(f) == 131072


def test_the_key_may_come_before_the_architecture(tmp_path):
    """Ordering is conventional, not specified — assuming it would read the
    file correctly today and silently wrongly on someone else's quantisation."""
    f = _write(
        tmp_path,
        _gguf(
            [
                _kv("qwen3.context_length", _UINT64, struct.pack("<Q", 40960)),
                _kv("general.architecture", _STRING, _string("qwen3")),
            ]
        ),
    )
    assert read_gguf_context_length(f) == 40960


def test_an_array_before_it_is_stepped_over(tmp_path):
    """Token lists are arrays and they come early. Mis-stepping one would read
    a number from an arbitrary offset — a plausible answer from the wrong
    bytes, which is worse than no answer."""
    tokens = struct.pack("<I", _STRING) + struct.pack("<Q", 2) + _string("a") + _string("b")
    f = _write(
        tmp_path,
        _gguf(
            [
                _kv("general.architecture", _STRING, _string("llama")),
                _kv("tokenizer.ggml.tokens", _ARRAY, tokens),
                _kv("llama.context_length", _UINT32, struct.pack("<I", 8192)),
            ]
        ),
    )
    assert read_gguf_context_length(f) == 8192


# ── admitting it does not know ─────────────────────────────────────────────


@pytest.mark.parametrize(
    "name",
    ["missing file", "not a gguf", "no context key", "truncated"],
)
def test_it_returns_none_rather_than_raising(tmp_path, name):
    """A model can be registered before it is downloaded, so an unreadable file
    is ordinary here. Raising would turn a routine registration into a crash."""
    if name == "missing file":
        target = tmp_path / "nope.gguf"
    elif name == "not a gguf":
        target = _write(tmp_path, b"not a gguf at all")
    elif name == "no context key":
        target = _write(tmp_path, _gguf([_kv("general.architecture", _STRING, _string("llama"))]))
    else:
        target = _write(tmp_path, _gguf([_kv("general.architecture", _STRING, _string("x"))])[:12])
    assert read_gguf_context_length(target) is None


def test_an_unknown_value_type_stops_rather_than_guessing(tmp_path):
    """Once the position in the file is untrustworthy, every later number is a
    plausible-looking value from the wrong offset."""
    f = _write(
        tmp_path,
        _gguf(
            [
                _kv("weird", 99, b""),
                _kv("general.architecture", _STRING, _string("llama")),
                _kv("llama.context_length", _UINT32, struct.pack("<I", 4096)),
            ]
        ),
    )
    assert read_gguf_context_length(f) is None


# ── the widened value reader, which three callers already use ──────────────


def test_the_existing_architecture_reader_still_works(tmp_path):
    """PRM-194 made `_gguf_read_value` return numbers where it used to discard
    them. The callers that predate it must be unaffected."""
    from prometheus_manager_core.hf_discovery import read_gguf_architecture

    f = _write(
        tmp_path,
        _gguf(
            [
                _kv("general.quantization_version", _UINT32, struct.pack("<I", 2)),
                _kv("general.architecture", _STRING, _string("qwen2_5_vl")),
            ]
        ),
    )
    assert read_gguf_architecture(f) == "qwen2_5_vl"
