"""Tests for Downloader: memory/specs/008 AC-20, AC-21 and memory/specs/011 AC-1–AC-11, AC-22–AC-24."""

from __future__ import annotations

import hashlib
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from prometheus_manager.downloader import (
    DownloadError,
    DownloadState,
    _sha256,
    _validate_filename,
    download_model,
)

# ── helpers ──────────────────────────────────────────────────────────────────


def _make_response(
    content: bytes, status_code: int = 200, content_length: bool = True
) -> MagicMock:
    """Build a fake requests.Response that streams chunks."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.raise_for_status = MagicMock()
    chunks = [content[i : i + 65536] for i in range(0, len(content), 65536)] or [b""]
    resp.iter_content = MagicMock(return_value=iter(chunks))
    resp.headers = {"Content-Length": str(len(content))} if content_length else {}
    return resp


def _patch_hf(content: bytes, content_length: bool = True, hf_token: str | None = None):
    """Context manager that patches all HF/requests module-level names."""
    url = "https://huggingface.co/fake/resolve/main/m.gguf"
    resp = _make_response(content, content_length=content_length)
    mock_req = MagicMock()
    mock_req.get.return_value = resp
    return (
        patch("prometheus_manager.downloader.hf_hub_url", return_value=url),
        patch(
            "prometheus_manager.downloader.build_hf_headers",
            return_value={"user-agent": "test/1.0"},
        ),
        patch("prometheus_manager.downloader._requests", mock_req),
    ), mock_req, resp


# ── AC-1–AC-4: DownloadState model ──────────────────────────────────────────


class TestDownloadStateModel:
    """memory/specs/011 — AC-1–AC-4."""

    def test_AC1_new_fields_defaults(self):
        """AC-1: speed_bps, eta_seconds, cancel_requested have specified defaults."""
        s = DownloadState(model_id="m", hf_repo="a/b", hf_filename="m.gguf")
        assert s.speed_bps == 0.0
        assert s.eta_seconds is None
        assert s.cancel_requested is False

    def test_AC2_progress_property_unchanged(self):
        """AC-2: progress = downloaded / total; 0.0 when total is 0."""
        s = DownloadState(model_id="m", hf_repo="a/b", hf_filename="m.gguf")
        assert s.progress == 0.0
        s.total_bytes = 1000
        s.downloaded_bytes = 250
        assert s.progress == 0.25

    def test_AC3_cancel_set_readable(self):
        """AC-3: cancel_requested can be set to True without error."""
        s = DownloadState(model_id="m", hf_repo="a/b", hf_filename="m.gguf")
        s.cancel_requested = True
        assert s.cancel_requested is True

    def test_AC4_asdict_includes_new_fields(self):
        """AC-4: dataclasses.asdict includes all three new fields."""
        import dataclasses

        s = DownloadState(
            model_id="m",
            hf_repo="a/b",
            hf_filename="m.gguf",
            speed_bps=1024.0,
            eta_seconds=30,
            cancel_requested=True,
        )
        d = dataclasses.asdict(s)
        assert d["speed_bps"] == 1024.0
        assert d["eta_seconds"] == 30
        assert d["cancel_requested"] is True


# ── AC-5–AC-9: streaming download ───────────────────────────────────────────


class TestStreamingDownload:
    """memory/specs/011 — AC-5–AC-9."""

    def test_AC5_progress_called_with_downloading_and_positive_bytes(self, tmp_path: Path):
        """AC-5: on_progress called with status=downloading and downloaded_bytes>0."""
        content = b"x" * 200_000
        calls: list[DownloadState] = []
        patches, _mock_req, _resp = _patch_hf(content)
        with patches[0], patches[1], patches[2]:
            download_model(
                "m",
                "a/b",
                "m.gguf",
                tmp_path,
                on_progress=lambda s: calls.append(
                    DownloadState(
                        model_id=s.model_id,
                        hf_repo=s.hf_repo,
                        hf_filename=s.hf_filename,
                        downloaded_bytes=s.downloaded_bytes,
                        total_bytes=s.total_bytes,
                        status=s.status,
                    )
                ),
            )

        downloading_calls = [c for c in calls if c.status == "downloading"]
        assert any(c.downloaded_bytes > 0 for c in downloading_calls), (
            "At least one progress call with downloaded_bytes > 0 expected"
        )

    def test_AC6_total_bytes_and_range_valid(self, tmp_path: Path):
        """AC-6: total_bytes > 0 and downloaded_bytes in [0, total_bytes] during download."""
        content = b"y" * 150_000
        violations: list[str] = []

        def check(s: DownloadState) -> None:
            if (
                s.status == "downloading"
                and s.total_bytes > 0
                and not (0 <= s.downloaded_bytes <= s.total_bytes)
            ):
                violations.append(f"{s.downloaded_bytes} not in [0, {s.total_bytes}]")

        patches, _mock_req, _resp = _patch_hf(content)
        with patches[0], patches[1], patches[2]:
            download_model("m", "a/b", "m.gguf", tmp_path, on_progress=check)

        assert violations == [], f"Range violations: {violations}"

    def test_AC7_cancel_requested_stops_download(self, tmp_path: Path):
        """AC-7: cancel_requested=True → partial file deleted, status cancelled."""
        content = b"z" * 200_000
        state_holder: list[DownloadState] = []

        def on_progress(s: DownloadState) -> None:
            state_holder.append(s)
            if s.status == "downloading" and not s.cancel_requested:
                s.cancel_requested = True

        patches, _mock_req, _resp = _patch_hf(content)
        with patches[0], patches[1], patches[2]:
            download_model("m", "a/b", "m.gguf", tmp_path, on_progress=on_progress)

        assert any(s.status == "cancelled" for s in state_holder), (
            "Expected a cancelled status callback"
        )
        assert not (tmp_path / "m.gguf").exists(), "File must be deleted on cancel"

    def test_AC7_cancel_via_external_state_stops_download(self, tmp_path: Path):
        """AC-7 (UI path): external DownloadState.cancel_requested propagates to downloader.

        The TUI holds a 'ui_state' in self._downloads.  on_progress copies fields
        from the internal state to ui_state AND bridges cancel_requested back.
        This unit-test validates the bridge pattern directly: after a user sets
        ui_state.cancel_requested = True, the NEXT on_progress invocation must
        set internal_state.cancel_requested = True so the downloader stops.
        """
        ui_state = DownloadState(model_id="m", hf_repo="a/b", hf_filename="m.gguf")
        internal_state = DownloadState(model_id="m", hf_repo="a/b", hf_filename="m.gguf")

        # Replicate the on_progress closure written in app._do_download
        def on_progress(internal: DownloadState, _ds: DownloadState = ui_state) -> None:
            _ds.downloaded_bytes = internal.downloaded_bytes
            _ds.status = internal.status
            if _ds.cancel_requested:
                internal.cancel_requested = True

        # Before cancel: bridge must NOT propagate
        on_progress(internal_state)
        assert internal_state.cancel_requested is False

        # User presses "c" → UI sets cancel_requested on ui_state
        ui_state.cancel_requested = True

        # Next progress tick propagates it to the internal state
        on_progress(internal_state)
        assert internal_state.cancel_requested is True

    def test_AC8_content_length_sets_total_bytes(self, tmp_path: Path):
        """AC-8: Content-Length header sets total_bytes on DownloadState."""
        content = b"a" * 100_000
        seen_total: list[int] = []

        def on_progress(s: DownloadState) -> None:
            if s.total_bytes > 0:
                seen_total.append(s.total_bytes)

        patches, _mock_req, _resp = _patch_hf(content, content_length=True)
        with patches[0], patches[1], patches[2]:
            download_model("m", "a/b", "m.gguf", tmp_path, on_progress=on_progress)

        assert seen_total, "total_bytes must be reported"
        assert seen_total[0] == len(content)

    def test_AC9_no_content_length_completes(self, tmp_path: Path):
        """AC-9: missing Content-Length proceeds and sets total_bytes from file size."""
        content = b"b" * 80_000
        patches, _mock_req, _resp = _patch_hf(content, content_length=False)
        with patches[0], patches[1], patches[2]:
            calls: list[DownloadState] = []
            result = download_model(
                "m", "a/b", "m.gguf", tmp_path, on_progress=lambda s: calls.append(s)
            )

        done_calls = [c for c in calls if c.status == "done"]
        assert done_calls, "Expected done status"
        assert done_calls[-1].total_bytes == len(content)
        assert result.exists()


# ── AC-10, AC-23, AC-27: path traversal ─────────────────────────────────────


class TestPathTraversal:
    """memory/specs/011 — AC-10, AC-23, AC-27."""

    def test_AC10_path_traversal_raises_before_download(self, tmp_path: Path):
        """AC-10/AC-23/AC-27: filename with ../ raises DownloadError, no file written."""
        with pytest.raises(DownloadError, match="path traversal"):
            download_model("m", "a/b", "../../etc/passwd", tmp_path)
        assert not list(tmp_path.iterdir()), "No file must be written"

    def test_validate_filename_absolute_rejected(self):
        """Absolute hf_filename is rejected."""
        with pytest.raises(DownloadError, match="path traversal"):
            _validate_filename("/etc/passwd")

    def test_validate_filename_normal_passes(self):
        """Normal filename passes validation."""
        _validate_filename("model.gguf")
        _validate_filename("subdir/model.gguf")


# ── AC-22–AC-24: CA bundle ──────────────────────────────────────────────────


class TestCABundle:
    """memory/specs/011 — AC-22–AC-25."""

    def test_AC22_no_ca_bundle_uses_verify_true(self, tmp_path: Path):
        """AC-22: ca_bundle=None → requests.get called with verify=True."""
        content = b"data"
        patches, mock_req, _resp = _patch_hf(content)
        with patches[0], patches[1], patches[2]:
            download_model("m", "a/b", "m.gguf", tmp_path, ca_bundle=None)
            _, kwargs = mock_req.get.call_args
            assert kwargs.get("verify") is True

    def test_AC23_valid_ca_bundle_path_used(self, tmp_path: Path):
        """AC-23: ca_bundle path exists → requests.get called with verify=<path>."""
        content = b"data"
        ca = tmp_path / "bundle.pem"
        ca.write_text("-----BEGIN CERTIFICATE-----\n...\n-----END CERTIFICATE-----\n")
        patches, mock_req, _resp = _patch_hf(content)
        with patches[0], patches[1], patches[2]:
            download_model("m", "a/b", "m.gguf", tmp_path, ca_bundle=str(ca))
            _, kwargs = mock_req.get.call_args
            assert kwargs.get("verify") == str(ca)

    def test_AC24_missing_ca_bundle_raises_before_request(self, tmp_path: Path):
        """AC-24: nonexistent ca_bundle → DownloadError, no HTTP request made."""
        patches, mock_req, _resp = _patch_hf(b"x")
        with patches[0], patches[1], patches[2]:
            with pytest.raises(DownloadError, match="CA bundle not found"):
                download_model("m", "a/b", "m.gguf", tmp_path, ca_bundle="/nonexistent/path.pem")
            mock_req.get.assert_not_called()

    def test_hf_filename_with_subdirectory_creates_parent_dirs(self, tmp_path: Path):
        """hf_filename may include a repo subdir (e.g. Q4_0/file.gguf).

        The downloader must create intermediate directories so open(dest_path, 'wb')
        does not raise [Errno 2] No such file or directory.
        """
        content = b"gguf-data"
        patches, _mock_req, _resp = _patch_hf(content)
        with patches[0], patches[1], patches[2]:
            result = download_model("m", "a/b", "Q4_0/model.gguf", tmp_path)
        assert result == tmp_path / "Q4_0" / "model.gguf"
        assert result.read_bytes() == content


# ── AC-20, AC-21: existing spec-008 ACs ─────────────────────────────────────


class TestDownloaderAC20:
    """AC-20: download_model downloads file and reports progress."""

    def test_AC20_successful_download_returns_path(self, tmp_path: Path):
        """AC-20: returns Path to downloaded file on success."""
        content = b"fake gguf content"
        patches, _mock_req, _resp = _patch_hf(content)
        with patches[0], patches[1], patches[2]:
            result = download_model("test-model", "org/test-repo", "m.gguf", tmp_path)

        assert result.exists()
        assert result.read_bytes() == content

    def test_AC20_progress_callback_called_with_done(self, tmp_path: Path):
        """AC-20: progress callback is invoked and reaches done status."""
        content = b"data" * 100
        calls: list[str] = []
        patches, _mock_req, _resp = _patch_hf(content)
        with patches[0], patches[1], patches[2]:
            download_model(
                "m", "a/b", "m.gguf", tmp_path, on_progress=lambda s: calls.append(s.status)
            )

        assert "done" in calls

    def test_AC20_hub_url_error_raises_download_error(self, tmp_path: Path):
        """AC-20: URL resolution error is wrapped into DownloadError."""
        with patch(
            "prometheus_manager.downloader.hf_hub_url",
            side_effect=RuntimeError("hub down"),
        ), patch(
            "prometheus_manager.downloader.build_hf_headers",
            return_value={"user-agent": "test"},
        ), pytest.raises(DownloadError, match="URL resolution failed"):
            download_model("m", "a/b", "m.gguf", tmp_path)

    def test_AC20_http_error_raises_download_error(self, tmp_path: Path):
        """AC-20: HTTP error raises DownloadError."""
        patches, mock_req, _resp = _patch_hf(b"x")
        mock_req.get.side_effect = RuntimeError("connection refused")
        with patches[0], patches[1], patches[2], pytest.raises(
            DownloadError, match="connection refused"
        ):
            download_model("m", "a/b", "m.gguf", tmp_path)


class TestDownloaderAC21:
    """AC-21: SHA-256 verification."""

    def test_AC21_correct_sha256_passes(self, tmp_path: Path):
        """AC-21: matching SHA-256 does not raise."""
        content = b"valid gguf content"
        expected = hashlib.sha256(content).hexdigest()
        patches, _mock_req, _resp = _patch_hf(content)
        with patches[0], patches[1], patches[2]:
            result = download_model("m", "a/b", "m.gguf", tmp_path, expected_sha256=expected)

        assert result.exists()

    def test_AC21_wrong_sha256_deletes_file_and_raises(self, tmp_path: Path):
        """AC-21: SHA-256 mismatch deletes the downloaded file."""
        content = b"corrupt data"
        patches, _mock_req, _resp = _patch_hf(content)
        with patches[0], patches[1], patches[2], pytest.raises(
            DownloadError, match="SHA-256 mismatch"
        ):
            download_model("m", "a/b", "m.gguf", tmp_path, expected_sha256="a" * 64)

        assert not (tmp_path / "m.gguf").exists(), "File must be deleted on SHA-256 mismatch"

    def test_AC21_no_sha256_skips_verification(self, tmp_path: Path):
        """AC-21: when expected_sha256 is None, no verification is done."""
        content = b"any content"
        patches, _mock_req, _resp = _patch_hf(content)
        with patches[0], patches[1], patches[2]:
            result = download_model("m", "a/b", "m.gguf", tmp_path, expected_sha256=None)

        assert result.exists()


class TestSha256Helper:
    """Unit test for internal _sha256 helper."""

    def test_sha256_computes_correctly(self, tmp_path: Path):
        content = b"hello prometheus"
        f = tmp_path / "f"
        f.write_bytes(content)
        assert _sha256(f) == hashlib.sha256(content).hexdigest()
