"""HuggingFace GGUF downloader with SHA-256 verification and real-time progress.

Implements: memory/specs/008-llama-server-manager.md — AC-20, AC-21
Implements: memory/specs/011-downloads-view-redesign.md — AC-5–AC-11, AC-22–AC-24
Implements: memory/specs/018-observability-telemetry.md — AC-3
"""

from __future__ import annotations

import hashlib
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

# Import at module level so tests can patch these
try:
    from huggingface_hub import hf_hub_url
    from huggingface_hub.utils import build_hf_headers  # type: ignore[attr-defined]
except ImportError:  # pragma: no cover
    hf_hub_url = None  # type: ignore[assignment]
    build_hf_headers = None  # type: ignore[assignment]

try:
    import requests as _requests
except ImportError:  # pragma: no cover
    _requests = None  # type: ignore[assignment]

from .telemetry import get_logger

logger = get_logger(__name__)

Status = Literal["queued", "downloading", "verifying", "done", "failed", "cancelled", "paused"]


@dataclass
class DownloadState:
    """In-memory state of an active or completed download.

    Implements: memory/specs/008-llama-server-manager.md — Data Model / DownloadState
    Implements: memory/specs/011-downloads-view-redesign.md — AC-1
    """

    model_id: str
    hf_repo: str
    hf_filename: str
    total_bytes: int = 0
    downloaded_bytes: int = 0
    status: Status = "queued"
    error: str | None = None
    started_at: datetime | None = None
    destination: Path | None = None
    # See memory/specs/011-downloads-view-redesign.md — AC-1
    speed_bps: float = 0.0
    eta_seconds: int | None = None
    cancel_requested: bool = False
    # RM-48 follow-up: pause keeps the partial file on disk (unlike cancel,
    # which deletes it) so a later download_model(..., resume=True) call can
    # continue via an HTTP Range request instead of restarting from byte 0.
    pause_requested: bool = False

    @property
    def progress(self) -> float:
        if self.total_bytes == 0:
            return 0.0
        return self.downloaded_bytes / self.total_bytes


ProgressCallback = Callable[[DownloadState], None]


class DownloadError(Exception):
    pass


def _validate_filename(hf_filename: str) -> None:
    """Reject path traversal components. See memory/specs/011 — AC-10, AC-23, AC-27."""
    p = Path(hf_filename)
    if ".." in p.parts or p.is_absolute():
        raise DownloadError(f"Unsafe hf_filename rejected (path traversal): {hf_filename!r}")


def download_model(
    model_id: str,
    hf_repo: str,
    hf_filename: str,
    dest_dir: Path,
    hf_token: str | None = None,
    expected_sha256: str | None = None,
    on_progress: ProgressCallback | None = None,
    ca_bundle: str | Path | None = None,
    resume: bool = False,
) -> Path:
    """Download a GGUF file from HuggingFace Hub with real-time progress reporting.

    Uses hf_hub_url + build_hf_headers + requests.get(stream=True) so the download
    runs in a plain thread without asyncio/httpx interference.

    build_hf_headers() provides the correct User-Agent that the HuggingFace CDN
    requires — without it, HF returns an HTML page instead of the binary.

    RM-48 follow-up: when resume=True and a partial file from an earlier
    paused download exists at the destination, requests an HTTP Range for the
    remaining bytes and appends to it instead of restarting from byte 0. The
    server is the authority on whether this is honored — a 206 response means
    it was; anything else (e.g. a 200, meaning Range was ignored) falls back
    to a normal full download, truncating any partial file.

    Implements: memory/specs/008-llama-server-manager.md — AC-20, AC-21
    Implements: memory/specs/011-downloads-view-redesign.md — AC-5–AC-11, AC-22–AC-24
    Returns the absolute path to the downloaded file.
    """
    if _requests is None:
        raise DownloadError("requests is not installed. Run: uv add requests")
    if hf_hub_url is None or build_hf_headers is None:
        raise DownloadError("huggingface-hub is not installed. Run: uv add huggingface-hub")

    # AC-10, AC-23, AC-27: reject path traversal
    _validate_filename(hf_filename)

    # AC-24: validate CA bundle path exists before any network call
    verify: str | bool
    if ca_bundle is not None:
        ca_path = Path(ca_bundle)
        if not ca_path.is_file():
            raise DownloadError(
                f"CA bundle not found: {ca_path} — "
                "set [downloads] ca_bundle in manager.toml to a valid PEM file path"
            )
        verify = str(ca_path)
    else:
        verify = True

    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_path = dest_dir / hf_filename
    # hf_filename may include a repo subdirectory (e.g. "Q4_0/file.gguf").
    # dest_dir.mkdir() only creates the base dir; create intermediate dirs too.
    dest_path.parent.mkdir(parents=True, exist_ok=True)

    existing_bytes = dest_path.stat().st_size if resume and dest_path.exists() else 0

    state = DownloadState(
        model_id=model_id,
        hf_repo=hf_repo,
        hf_filename=hf_filename,
        status="downloading",
        started_at=datetime.now(tz=UTC),
        downloaded_bytes=existing_bytes,
    )
    if on_progress:
        on_progress(state)

    logger.info(
        "download.start",
        extra={"model_id": model_id, "hf_repo": hf_repo, "hf_filename": hf_filename},
    )

    # Resolve URL via huggingface_hub (handles private repos, LFS redirects)
    try:
        url: str = hf_hub_url(repo_id=hf_repo, filename=hf_filename)
    except Exception as exc:
        state.status = "failed"
        state.error = str(exc)
        if on_progress:
            on_progress(state)
        raise DownloadError(f"URL resolution failed for {model_id}: {exc}") from exc

    # build_hf_headers provides the correct User-Agent that HF CDN requires.
    # Without it, HF returns an HTML login/redirect page instead of the binary.
    headers: dict[str, str] = dict(build_hf_headers(token=hf_token))
    if existing_bytes > 0:
        headers["Range"] = f"bytes={existing_bytes}-"

    try:
        resp = _requests.get(
            url,
            stream=True,
            headers=headers,
            verify=verify,
            timeout=(10, 60),
        )
        resp.raise_for_status()
    except Exception as exc:
        state.status = "failed"
        state.error = str(exc)
        if on_progress:
            on_progress(state)
        raise DownloadError(f"Download failed for {model_id}: {exc}") from exc

    # RM-48 follow-up: 206 means the server honored our Range request — the
    # true total comes from Content-Range ("bytes X-Y/TOTAL"), and
    # Content-Length here is only the *remaining* bytes. Anything else (a
    # plain 200) means Range was ignored — write mode falls back to a fresh,
    # full download regardless of what resume/existing_bytes said.
    resumed = existing_bytes > 0 and resp.status_code == 206
    if existing_bytes > 0 and not resumed:
        existing_bytes = 0
        state.downloaded_bytes = 0

    if resumed:
        content_range = resp.headers.get("Content-Range", "")
        if "/" in content_range:
            state.total_bytes = int(content_range.rsplit("/", 1)[-1])
    else:
        # AC-8: read Content-Length if present
        content_length = resp.headers.get("Content-Length")
        if content_length:
            state.total_bytes = int(content_length)

    # Rolling speed window: deque of (timestamp, bytes_received) samples
    _SPEED_WINDOW = 10
    _SAMPLE_INTERVAL = 0.5  # seconds between speed samples
    speed_samples: deque[tuple[float, int]] = deque(maxlen=_SPEED_WINDOW)
    last_sample_time = time.monotonic()
    bytes_since_sample = 0

    try:
        with open(dest_path, "ab" if resumed else "wb") as fh:
            for chunk in resp.iter_content(chunk_size=65536):
                if not chunk:
                    continue

                # AC-7: honour cancellation
                if state.cancel_requested:
                    fh.close()
                    dest_path.unlink(missing_ok=True)
                    state.status = "cancelled"
                    if on_progress:
                        on_progress(state)
                    return dest_path

                # RM-48 follow-up: pause keeps the partial file (unlike
                # cancel) so a later resume=True call can continue it.
                if state.pause_requested:
                    fh.close()
                    state.status = "paused"
                    if on_progress:
                        on_progress(state)
                    return dest_path

                fh.write(chunk)
                state.downloaded_bytes += len(chunk)
                bytes_since_sample += len(chunk)

                now = time.monotonic()
                if now - last_sample_time >= _SAMPLE_INTERVAL:
                    speed_samples.append((now, bytes_since_sample))
                    bytes_since_sample = 0
                    last_sample_time = now

                    if len(speed_samples) >= 2:
                        total_b = sum(s[1] for s in speed_samples)
                        elapsed = speed_samples[-1][0] - speed_samples[0][0]
                        state.speed_bps = total_b / elapsed if elapsed > 0 else 0.0
                    elif speed_samples:
                        state.speed_bps = speed_samples[0][1] / _SAMPLE_INTERVAL

                    remaining = state.total_bytes - state.downloaded_bytes
                    if state.speed_bps > 0 and remaining > 0:
                        state.eta_seconds = int(remaining / state.speed_bps)
                    else:
                        state.eta_seconds = None

                    if on_progress:
                        on_progress(state)

    except Exception as exc:
        dest_path.unlink(missing_ok=True)
        state.status = "failed"
        state.error = str(exc)
        if on_progress:
            on_progress(state)
        raise DownloadError(f"Download failed for {model_id}: {exc}") from exc

    # Final callback so callers always see > 0 bytes before done
    if on_progress:
        on_progress(state)

    # AC-9: set total_bytes from actual file size if Content-Length was absent
    actual_size = dest_path.stat().st_size
    state.total_bytes = actual_size
    state.downloaded_bytes = actual_size
    state.speed_bps = 0.0
    state.eta_seconds = None

    # AC-21 (spec 008): SHA-256 verification
    if expected_sha256:
        state.status = "verifying"
        if on_progress:
            on_progress(state)

        actual = _sha256(dest_path)
        if actual.lower() != expected_sha256.lower():
            dest_path.unlink(missing_ok=True)
            state.status = "failed"
            state.error = (
                f"SHA-256 mismatch for {model_id}: "
                f"expected {expected_sha256[:12]}\u2026, got {actual[:12]}\u2026"
            )
            if on_progress:
                on_progress(state)
            raise DownloadError(state.error)

        logger.info("download.verified", extra={"model_id": model_id})

    state.status = "done"
    state.destination = dest_path
    if on_progress:
        on_progress(state)

    logger.info(
        "download.complete",
        extra={"model_id": model_id, "path": str(dest_path)},
    )
    return dest_path


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()
