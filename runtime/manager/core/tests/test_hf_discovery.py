"""Tests for hf_discovery — Hugging Face model search/files/card (RM-48).

The pure helpers (infer_quant/auto_id/next_free_port/shard_filenames) were
ported from the TUI's Discovery view — see
runtime/manager/tui/tests/test_discovery_view.py for the original,
still-passing coverage against the same logic (now imported from here).
This file adds direct manager-core coverage plus the new search/files/card
functions manager-api will call over HTTP.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from prometheus_manager_core.hf_discovery import (
    auto_id,
    fetch_model_card,
    infer_quant,
    list_model_files,
    next_free_port,
    search_models,
    shard_filenames,
)

# ── Pure helpers — representative coverage (full matrix already in the TUI) ──


class TestInferQuant:
    def test_known_pattern(self) -> None:
        assert infer_quant("Llama-3.2-1B-Instruct-Q4_K_M.gguf") == "Q4_K_M"

    def test_unknown_returns_placeholder(self) -> None:
        assert infer_quant("model.gguf") == "?"


class TestAutoId:
    def test_basic_slug(self) -> None:
        assert auto_id("Llama-3.2-1B-Instruct-Q4_K_M.gguf").endswith("-local")

    def test_collision_adds_suffix(self) -> None:
        base = auto_id("model.gguf")
        assert auto_id("model.gguf", existing_ids={base}) != base


class TestNextFreePort:
    def test_returns_8081_when_empty(self) -> None:
        assert next_free_port(set()) == 8081

    def test_skips_used_ports(self) -> None:
        assert next_free_port({8081, 8082}) == 8083


class TestShardFilenames:
    def test_single_file_returned_unchanged(self) -> None:
        assert shard_filenames("model-Q4.gguf", []) == ["model-Q4.gguf"]

    def test_returns_all_shards_sorted(self) -> None:
        files = [
            "Q4_0/m-00002-of-00003.gguf",
            "Q4_0/m-00001-of-00003.gguf",
            "Q4_0/m-00003-of-00003.gguf",
        ]
        result = shard_filenames("Q4_0/m-00002-of-00003.gguf", files)
        assert result == [
            "Q4_0/m-00001-of-00003.gguf",
            "Q4_0/m-00002-of-00003.gguf",
            "Q4_0/m-00003-of-00003.gguf",
        ]


# ── search_models / list_model_files / fetch_model_card ──────────────────────


class TestSearchModels:
    def test_search_maps_fields_to_plain_dicts(self) -> None:
        fake_result = SimpleNamespace(
            id="bartowski/Llama-3.2-1B-GGUF",
            downloads=1234,
            likes=56,
            lastModified=None,
        )
        with patch(
            "prometheus_manager_core.hf_discovery.list_models", return_value=[fake_result]
        ) as mock_search:
            results = search_models("llama", limit=10, token="tok")

        assert results == [
            {
                "id": "bartowski/Llama-3.2-1B-GGUF",
                "downloads": 1234,
                "likes": 56,
                "last_modified": None,
            }
        ]
        mock_search.assert_called_once_with(
            filter="gguf", search="llama", limit=10, token="tok", sort=None
        )

    def test_raises_when_huggingface_hub_missing(self) -> None:
        with (
            patch("prometheus_manager_core.hf_discovery.list_models", None),
            pytest.raises(RuntimeError, match="huggingface-hub"),
        ):
            search_models("llama")

    def test_rejects_unknown_sort(self) -> None:
        with pytest.raises(ValueError, match="Unknown sort"):
            search_models("llama", sort="popularity")

    def test_passes_valid_sort_through(self) -> None:
        with patch(
            "prometheus_manager_core.hf_discovery.list_models", return_value=[]
        ) as mock_search:
            search_models("llama", sort="downloads")
        mock_search.assert_called_once_with(
            filter="gguf", search="llama", limit=30, token=None, sort="downloads"
        )


class TestListModelFiles:
    def test_filters_to_gguf_infers_quant_and_size(self) -> None:
        fake_info = SimpleNamespace(
            siblings=[
                SimpleNamespace(rfilename="README.md", size=100),
                SimpleNamespace(rfilename="model-Q4_K_M.gguf", size=4_000_000),
                SimpleNamespace(rfilename="config.json", size=50),
            ]
        )
        fake_api = MagicMock()
        fake_api.model_info.return_value = fake_info
        with patch("prometheus_manager_core.hf_discovery.HfApi", return_value=fake_api):
            files = list_model_files("bartowski/Llama-3.2-1B-GGUF")

        assert files == [
            {"filename": "model-Q4_K_M.gguf", "quantization": "Q4_K_M", "size_bytes": 4_000_000}
        ]
        fake_api.model_info.assert_called_once_with(
            "bartowski/Llama-3.2-1B-GGUF", files_metadata=True, token=None
        )


class TestFetchModelCard:
    def test_returns_text_and_metadata(self) -> None:
        fake_card = MagicMock()
        fake_card.text = "# Llama 3.2\n\nA model card."
        fake_card.data.to_dict.return_value = {"license": "llama3.2"}
        with patch("prometheus_manager_core.hf_discovery.ModelCard.load", return_value=fake_card):
            result = fetch_model_card("meta-llama/Llama-3.2-1B")

        assert result == {
            "repo_id": "meta-llama/Llama-3.2-1B",
            "text": "# Llama 3.2\n\nA model card.",
            "metadata": {"license": "llama3.2"},
        }
