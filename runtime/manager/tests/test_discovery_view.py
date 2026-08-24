"""Tests for the DiscoveryView pure helpers and app-level download action.

Implements: memory/specs/012-discovery-view-redesign.md — AC-10, AC-11, AC-12, AC-17
"""
from __future__ import annotations

import pytest

from prometheus_manager.tui.views.discovery import (
    _auto_id,
    _fmt_count,
    _infer_quant,
    _next_free_port,
    _shard_filenames,
)

# ── _infer_quant (AC-12) ──────────────────────────────────────────────────────

class TestInferQuant:
    @pytest.mark.parametrize(
        "filename, expected",
        [
            ("Llama-3.2-3B-Instruct-Q4_K_M.gguf", "Q4_K_M"),
            ("model-Q8_0.gguf", "Q8_0"),
            ("IQ3_M-quant.gguf", "IQ3_M"),
            ("phi-2.F16.gguf", "F16"),
            ("granite-F32.gguf", "F32"),
            ("gemma-BF16.gguf", "BF16"),
            ("unknown-model.gguf", "?"),
            ("model-q4_k_m.gguf", "Q4_K_M"),   # case-insensitive
        ],
    )
    def test_known_patterns(self, filename: str, expected: str) -> None:
        assert _infer_quant(filename) == expected


# ── _auto_id (AC-10) ─────────────────────────────────────────────────────────

class TestAutoId:
    def test_basic_slug(self) -> None:
        result = _auto_id("Llama-3.2-1B-Instruct-Q4_K_M.gguf")
        assert result == "llama-3-2-1b-instruct-q4-k-m-local"

    def test_strips_gguf_extension(self) -> None:
        result = _auto_id("model.gguf")
        assert not result.endswith(".gguf")

    def test_appends_local(self) -> None:
        assert _auto_id("model.gguf").endswith("-local")

    def test_no_collision(self) -> None:
        base = _auto_id("model.gguf")
        result = _auto_id("model.gguf", existing_ids=set())
        assert result == base

    def test_collision_adds_suffix(self) -> None:
        base = _auto_id("model.gguf")
        result = _auto_id("model.gguf", existing_ids={base})
        assert result != base
        assert result.endswith("-2")

    def test_multi_collision(self) -> None:
        base_id = _auto_id("model.gguf")
        first_collision = _auto_id("model.gguf", {base_id})
        second_collision = _auto_id("model.gguf", {base_id, first_collision})
        assert second_collision not in {base_id, first_collision}

    def test_truncates_at_63(self) -> None:
        long_name = "a" * 80 + ".gguf"
        result = _auto_id(long_name)
        assert len(result) <= 63

    def test_no_leading_trailing_hyphen(self) -> None:
        result = _auto_id("model.gguf")
        assert result[0] != "-"
        assert result[-1] != "-"


# ── _next_free_port (AC-11) ───────────────────────────────────────────────────

class TestNextFreePort:
    def test_returns_8081_when_empty(self) -> None:
        assert _next_free_port(set()) == 8081

    def test_skips_used_ports(self) -> None:
        assert _next_free_port({8081}) == 8082
        assert _next_free_port({8081, 8082}) == 8083

    def test_non_contiguous_gap(self) -> None:
        assert _next_free_port({8081, 8083}) == 8082

    def test_never_returns_below_8081(self) -> None:
        assert _next_free_port({8080}) == 8081


# ── _fmt_count ────────────────────────────────────────────────────────────────

class TestFmtCount:
    def test_none(self) -> None:
        assert _fmt_count(None) == "?"

    def test_small(self) -> None:
        assert _fmt_count(42) == "42"

    def test_thousands(self) -> None:
        result = _fmt_count(1500)
        assert "K" in result

    def test_millions(self) -> None:
        result = _fmt_count(1_200_000)
        assert "M" in result
        assert "1.2" in result

    def test_zero(self) -> None:
        assert _fmt_count(0) == "0"


# ── _shard_filenames ──────────────────────────────────────────────────────────

class TestShardFilenames:
    """Multi-part GGUF shard detection."""

    _ALL_FILES = [
        "Q4_0/DeepSeek-V3.2-Q4_0-00001-of-00008.gguf",
        "Q4_0/DeepSeek-V3.2-Q4_0-00002-of-00008.gguf",
        "Q4_0/DeepSeek-V3.2-Q4_0-00003-of-00008.gguf",
        "Q4_0/DeepSeek-V3.2-Q4_0-00004-of-00008.gguf",
        "Q4_0/DeepSeek-V3.2-Q4_0-00005-of-00008.gguf",
        "Q4_0/DeepSeek-V3.2-Q4_0-00006-of-00008.gguf",
        "Q4_0/DeepSeek-V3.2-Q4_0-00007-of-00008.gguf",
        "Q4_0/DeepSeek-V3.2-Q4_0-00008-of-00008.gguf",
        "DeepSeek-V3.2-Q2_K.gguf",  # unrelated single file in same repo
    ]

    def test_returns_all_shards_sorted(self) -> None:
        result = _shard_filenames("Q4_0/DeepSeek-V3.2-Q4_0-00001-of-00008.gguf", self._ALL_FILES)
        assert len(result) == 8
        assert result[0] == "Q4_0/DeepSeek-V3.2-Q4_0-00001-of-00008.gguf"
        assert result[-1] == "Q4_0/DeepSeek-V3.2-Q4_0-00008-of-00008.gguf"

    def test_selecting_middle_shard_returns_all(self) -> None:
        """Selecting shard 4 should still return all 8 shards."""
        result = _shard_filenames("Q4_0/DeepSeek-V3.2-Q4_0-00004-of-00008.gguf", self._ALL_FILES)
        assert len(result) == 8
        assert result[0] == "Q4_0/DeepSeek-V3.2-Q4_0-00001-of-00008.gguf"

    def test_single_file_returned_unchanged(self) -> None:
        """Non-shard files are returned as-is."""
        result = _shard_filenames("DeepSeek-V3.2-Q2_K.gguf", self._ALL_FILES)
        assert result == ["DeepSeek-V3.2-Q2_K.gguf"]

    def test_shard_not_in_all_files_returns_selected(self) -> None:
        """Graceful fallback: if all_files is empty, returns [selected]."""
        result = _shard_filenames("Q4_0/Model-00001-of-00003.gguf", [])
        assert result == ["Q4_0/Model-00001-of-00003.gguf"]

    def test_unrelated_shard_not_included(self) -> None:
        """Shards with a different total count are excluded."""
        files = [
            "model-A-00001-of-00003.gguf",
            "model-A-00002-of-00003.gguf",
            "model-A-00003-of-00003.gguf",
            "model-B-00001-of-00002.gguf",  # different model, different total
            "model-B-00002-of-00002.gguf",
        ]
        result = _shard_filenames("model-A-00001-of-00003.gguf", files)
        assert result == [
            "model-A-00001-of-00003.gguf",
            "model-A-00002-of-00003.gguf",
            "model-A-00003-of-00003.gguf",
        ]

    def test_returns_shards_sorted_by_part_number(self) -> None:
        """Files listed out of order must be returned sorted."""
        files = [
            "m-00003-of-00003.gguf",
            "m-00001-of-00003.gguf",
            "m-00002-of-00003.gguf",
        ]
        result = _shard_filenames("m-00002-of-00003.gguf", files)
        assert result == [
            "m-00001-of-00003.gguf",
            "m-00002-of-00003.gguf",
            "m-00003-of-00003.gguf",
        ]
