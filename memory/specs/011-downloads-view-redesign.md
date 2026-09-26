---
id: "011"
title: "Downloads View Redesign — Real-Time Progress, Cancel, Retry & Detail Panel"
status: closed
current-agent: ""
pipeline-log: []
created: 2026-04-02
updated: 2026-04-11
---

<!-- Changelog: draft → CA bundle support → multi-shard download → OS-native trust store → subdirectory path fix → cancel propagation fix (2026-04-02 to 2026-04-11) -->

# 011 — Downloads View Redesign — Real-Time Progress, Cancel, Retry & Detail Panel

## Problem Statement

The current `DownloadsView` (tab 4) and the underlying `download_model()` function have
three fundamental problems that make them nearly useless in practice:

1. **No real-time progress.** `hf_hub_download` does not emit byte-level callbacks during
   transfer. The `on_progress` callback fires only at state transitions (`queued →
   downloading → verifying → done`). The progress column always shows `—` or `0.0%`
   until the whole file is on disk, then jumps to `100%`. For a 4 GB model this means
   watching a frozen table for ~5 minutes with no feedback.

2. **No operational controls.** There is no way to cancel a running download, retry a
   failed one, or clear completed entries. A failed download (e.g. network drop, SHA
   mismatch) permanently occupies a table row with no recovery path other than restarting
   the TUI.

3. **Presentation mismatch.** The current table shows `Repo` as a column (redundant — it
   is already in the detail panel of Registry) and mixes active, completed, and failed
   entries without visual separation. There is no bandwidth gauge, no ETA, and no file-
   size-to-date indicator.

## Goals

- [x] Replace `hf_hub_download` with a manual streaming download (requests + `iter_content`)
      that reports bytes in real time via the existing `on_progress` callback.
- [x] Add `speed_bps`, `eta_seconds`, and `cancel_requested` fields to `DownloadState`.
- [x] Display a visual block-char progress bar, speed, and ETA in the Downloads table.
- [x] Separate the table into two sections: **Active / Queued** (top) and **History**
      (completed, failed, cancelled — bottom), with a visual separator row.
- [x] Add `[c]` Cancel, `[r]` Retry, and `[x]` Clear-history keybindings.
- [x] Add a detail panel (below the table, always visible, toggled with `[Enter]`) showing
      full download metadata for the selected entry.
- [x] Sync the Dashboard "Active Downloads" widget to use the same progress bar format.

## Non-Goals

- No new REST API endpoints — downloads are TUI-only.
- No persistent download queue (restoring after TUI restart is out of scope).
- No parallel multi-file downloads for a single model_id (shards download sequentially).
- No change to SHA-256 verification logic (AC-21 spec 008 stays intact).
- No change to `RegistryView`, `InstancesView`, or `DashboardView` (except the
  Active Downloads widget text format).

## Proposed Solution

### Streaming downloader

Replace the call to `hf_hub_download` with a two-step approach:

1. **Resolve URL** using `huggingface_hub.hf_hub_url(repo_id, filename)` — this honours
   auth tokens and private repos without reimplementing Hub's URL routing.
2. **Stream download** using `requests.get(url, stream=True, headers={"Authorization":
   "Bearer <token>"}, verify=<ca_bundle>)` with `iter_content(chunk_size=65536)`. Each
   chunk updates `downloaded_bytes`, `speed_bps`, and `eta_seconds` on the shared
   `DownloadState` object, triggering the `on_progress` callback so the TUI can refresh.

`cancel_requested` is a plain `bool` field on `DownloadState`. The download loop checks
it once per chunk. When `True`, the loop discards the partial file and sets
`status = "cancelled"`.

**Cancel bridge (UI → downloader)**: `_do_download()` in `app.py` maintains two
`DownloadState` objects per shard: an internal one owned by `download_model()` and
a `ui_state` stored in `self._downloads` (the object the TUI reads and writes).
The `on_progress` callback copies fields from the internal state to `ui_state`, and
also propagates the cancel flag in the reverse direction:

```python
def on_progress(internal: DownloadState, _ds: DownloadState = ui_state) -> None:
    _ds.downloaded_bytes = internal.downloaded_bytes
    _ds.status = internal.status
    # ... other fields ...
    if _ds.cancel_requested:          # user pressed [c]
        internal.cancel_requested = True   # propagate to downloader
```

This ensures the `[c]` keybinding (which sets `ui_state.cancel_requested = True`) actually
stops the download on the next chunk boundary.

### Multi-shard (split-model) sequential download

Some large models (e.g. DeepSeek-V3, Llama-3.1-70B) are distributed as multiple GGUF
shards following HuggingFace's naming convention:

```
Q4_0/ModelName-00001-of-00008.gguf
Q4_0/ModelName-00002-of-00008.gguf
...
Q4_0/ModelName-00008-of-00008.gguf
```

When the user selects any shard file and presses `[d]`, `_shard_filenames(selected,
all_files)` detects the pattern `<prefix>NNNNN-of-MMMMM.gguf` (regex `_SHARD_RE`),
collects all M sibling shards sharing the same prefix and total count from the Files
table list, and returns them sorted by part number. The full list is stored in
`RegistryEntry.hf_filenames`.

`_do_download()` creates one `DownloadState` per shard, labelled `model-id [N/M]`, and
downloads them sequentially. On failure or user cancel of any shard, all remaining
`queued` shards are set to `cancelled`. On retry, the base `model_id` is extracted by
stripping the `[N/M]` suffix and the full multi-shard sequence restarts.

**Subdirectory creation**: HuggingFace repos may store shards inside a subfolder (e.g.
`Q4_0/`). `download_model()` now calls `dest_path.parent.mkdir(parents=True,
exist_ok=True)` to create any intermediate directories in addition to `dest_dir`.

### OS-native SSL trust store via `truststore`

The `requests` library uses its bundled `certifi` CA store by default, which does not
contain corporate CAs injected into the OS keychain by IT/MDM. On machines that are NOT
running Zscaler but that have a company-managed CA in the macOS Keychain (or equivalent
on Linux/Windows), this causes `CERTIFICATE_VERIFY_FAILED` errors.

`truststore` patches Python's `ssl` module to use the OS-native trust store at startup:

```python
import truststore
truststore.inject_into_ssl()
```

This call is made unconditionally at the CLI entry point (`cli/main.py`) so it covers
both the TUI and all download workers. It is wrapped in a `try/except ImportError` so
the manager still starts if `truststore` is not installed.

The `[downloads] ca_bundle` config option remains for machines where the company CA
*is NOT* installed in the OS keychain (fully-locked Zscaler hosts). The order of
precedence is:

1. Explicit `ca_bundle` path in `manager.toml` → `requests.get(verify=<path>)`
2. `truststore` injected (default when `ca_bundle = ""`) → OS keychain via SSL
3. Fallback if `truststore` not installed → `certifi` bundle

### CA bundle for TLS interception

Some deployment environments (e.g. corporate proxies with TLS inspection, such as
Zscaler) require a custom CA certificate to successfully establish HTTPS connections to
HuggingFace. Others (developer laptops without a proxy) work with the system default
trust store.

The `requests` library `verify` parameter controls this:
- `verify=True` (default) — uses the system/certifi trust store. Works on machines
  without TLS interception.
- `verify="/path/to/ca-bundle.pem"` — uses the specified PEM file, which can contain
  one or more chained certificates. Required on machines with a corporate CA.
- `verify=False` — **never used automatically**. Not configurable; this would disable
  certificate validation entirely and is a security violation.

The path to the custom CA bundle is read from a new optional config key
`[downloads] ca_bundle` in `manager.toml`, or from the environment variable
`REQUESTS_CA_BUNDLE` (which `requests` already respects natively). When neither is set,
`verify=True` is used (system trust store). No code change is needed to support the
environment-variable path; only the explicit config option needs implementation.

### Speed and ETA calculation

A short deque (10 samples, sampled every 0.5 s) keeps a rolling mean of bytes received
per second. ETA is `(total_bytes − downloaded_bytes) / speed_bps` when both are non-zero.

### DownloadState data model changes

```python
@dataclass
class DownloadState:
    # existing fields unchanged
    model_id: str
    hf_repo: str
    hf_filename: str
    total_bytes: int = 0
    downloaded_bytes: int = 0
    status: Status = "queued"
    error: str | None = None
    started_at: datetime | None = None
    destination: Path | None = None
    # new fields
    speed_bps: float = 0.0          # rolling bytes/s
    eta_seconds: int | None = None  # None when unknown
    cancel_requested: bool = False  # set by TUI; checked by worker
```

### Downloads table layout

```
 ID                          Progress           Speed      ETA    Status
 ─────────────────────────── ────────────────── ────────── ────── ──────────
 llama3-8b-q4-local          █████████░░░░░░░░  1.4 MB/s   3m12s  downloading
 phi3-mini-4k-q4-local       queued             —          —      queued
 ─── History ────────────────────────────────────────────────────────────────
 mistral-7b-v02-q4-local     ████████████████  done       —      done
 deepseek-r1-8b-q4-local     ✗ SHA mismatch    —          —      failed
```

Progress bar: 16 block chars (`█` filled, `░` empty) proportional to `progress`.

### Detail panel

Toggled by `[Enter]`, always visible by default. Two columns:

| Left | Right |
|------|-------|
| HF Repo | `hf_filename` |
| Destination | `started_at` (local time) |
| File size | Duration (elapsed or total) |
| SHA-256 | `verified` / `not verified` / `—` |
| Error | full error text (scrollable) |

### Keybindings

| Key | Action | Condition |
|-----|--------|-----------|
| `c` | Cancel selected download | status in `{queued, downloading, verifying}` |
| `r` | Retry selected download | status in `{failed, cancelled}` |
| `x` | Clear history entries | removes all `done / failed / cancelled` rows |
| `Enter` | Toggle detail panel | always |

## Data Model

### `downloader.py` — `DownloadState`

Three new fields added (see above). All existing fields, behaviour, and the `progress`
property remain unchanged. `cancel_requested` is write-once from the TUI thread; the
downloader worker treats it as read-only.

### `registry.py` — `RegistryEntry`

One new field:

```python
hf_filenames: list[str] = field(default_factory=list)
```

For single-file models, `hf_filenames` is empty and `hf_filename` holds the sole
filename. For sharded models, `hf_filenames` contains the full ordered list. Both
fields are persisted to and loaded from `registry.yaml`.

### `downloader.py` — `download_model()`

Signature gains one new optional parameter:

```python
def download_model(
    model_id: str,
    hf_repo: str,
    hf_filename: str,
    dest_dir: Path,
    hf_token: str | None = None,
    expected_sha256: str | None = None,
    on_progress: ProgressCallback | None = None,
    ca_bundle: str | Path | None = None,   # NEW
) -> Path:
```

`ca_bundle` is forwarded as `requests.get(..., verify=ca_bundle or True)`. When `None`,
`requests` uses the system trust store (or `REQUESTS_CA_BUNDLE` env var if set).
`on_progress` is now called for every chunk (not just at state transitions), so callers
that relied on infrequent callbacks continue to work — they simply get more frequent callbacks.

### `config.py` — `DownloadsConfig`

```python
@dataclass
class DownloadsConfig:
    dir: str = "runtime/models"
    hf_token_env: str = "HF_TOKEN"
    ca_bundle: str = ""   # NEW — empty string means "use system trust store"
```

Default TOML:
```toml
[downloads]
dir = "runtime/models"
hf_token_env = "HF_TOKEN"
# ca_bundle = "/etc/pki/ca-trust/extracted/pem/tls-ca-bundle.pem"  # uncomment on RHEL/Zscaler
```

`ManagerConfig` exposes a `resolved_ca_bundle` property that returns a `Path` if
`ca_bundle` is set and non-empty, or `None` otherwise. The `_do_download()` worker in
`app.py` passes this value to `download_model(ca_bundle=...)` on every call.

## Security Considerations

- The HuggingFace `hf_token` is passed as an HTTP header (`Authorization: Bearer ...`).
  It must not be logged, stored in `DownloadState`, or rendered in the TUI.
- User-controlled fields rendered in the detail panel (`hf_repo`, `hf_filename`,
  `error`) must be escaped with `rich.markup.escape()` before display.
- Destination path for the partial file is derived from `dest_dir / hf_filename`.
  `hf_filename` must be validated to contain no path traversal components (`../`).
  If validation fails, the download is aborted with status `failed`.
- The `requests` call must set `timeout=(10, 30)` (connect, read) to prevent the
  thread from blocking indefinitely on a stalled connection.
- **TLS verification must never be disabled** (`verify=False` is not an option even
  when a CA bundle is not configured). The only valid values for `verify` are `True`
  (system/certifi trust store) or a path to a PEM file.
- The `ca_bundle` path, if configured via `manager.toml` or environment variable, must
  be validated to exist and be a readable file before the first download attempt. If the
  path is set but the file does not exist, `download_model()` raises `DownloadError`
  immediately rather than proceeding with an invalid bundle.
- The `ca_bundle` path must not be logged at DEBUG level or below to avoid leaking
  filesystem layout to untrusted log consumers.

## Acceptance Criteria

### DownloadState model (AC-1 – AC-4)

- [x] AC-1: `DownloadState` has `speed_bps: float = 0.0`, `eta_seconds: int | None = None`,
      and `cancel_requested: bool = False` fields with the specified defaults.
- [x] AC-2: The `progress` property is unchanged and still returns
      `downloaded_bytes / total_bytes` (0.0 when total is 0).
- [x] AC-3: Setting `cancel_requested = True` on a `DownloadState` instance does not
      raise an error and the field is readable.
- [x] AC-4: `DownloadState` serialises to a plain `dict` (via `dataclasses.asdict`)
      without error, including the three new fields.

### Streaming downloader (AC-5 – AC-11)

- [x] AC-5: Given a model with `hf_repo` and `hf_filename`, when `download_model()` is
      called, then `on_progress` is called at least once with `status == "downloading"`
      and `downloaded_bytes > 0` before the final `done` callback.
- [x] AC-6: Given a successful download, when `on_progress` fires with `status ==
      "downloading"`, then `total_bytes > 0` and `0 <= downloaded_bytes <= total_bytes`.
- [x] AC-7: Given a running download with `cancel_requested = True` set on the shared
      `DownloadState`, when the next chunk is processed, then the partial file is deleted,
      `status` is set to `"cancelled"`, `on_progress` is called once with that status,
      and `DownloadError` is NOT raised.
- [x] AC-8: Given a response whose `Content-Length` header is present, when the download
      starts, then `total_bytes` on the `DownloadState` equals the header value.
- [x] AC-9: Given a response with no `Content-Length`, when the download runs,
      then the downloader proceeds without error and `total_bytes` is set to the actual
      file size upon completion.
- [x] AC-10: Given an `hf_filename` containing a path traversal component (e.g.
      `../../etc/passwd`), when `download_model()` is called, then it raises
      `DownloadError` and no file is written.
- [x] AC-11: Given a network timeout (simulated by a slow/stalled response), when
      `download_model()` is called, then it raises `DownloadError` within 30 seconds
      of the last received byte.

### Downloads view — table (AC-12 – AC-15)

- [x] AC-12: Given a download in status `"downloading"`, when `refresh_data` is called,
      then its table row shows a block-char progress bar of exactly 16 characters where
      filled chars ≈ `progress × 16`.
- [x] AC-13: Given a download with `speed_bps > 0`, when the row is rendered, then the
      Speed column shows a human-readable value ending in `B/s`, `KB/s`, or `MB/s`.
- [x] AC-14: Given a download with `eta_seconds` set, when the row is rendered, then the
      ETA column shows `Xm Ys` format (e.g. `3m 12s`); when `eta_seconds` is `None`, the
      column shows `—`.
- [x] AC-15: Given downloads in mixed statuses, when `refresh_data` is called, then
      active/queued entries appear above a separator row and `done/failed/cancelled`
      entries appear below it in the same table.

### Downloads view — detail panel (AC-16 – AC-17)

- [x] AC-16: Given a row is selected, when `DownloadsView` is mounted, then the detail
      panel is visible and shows the `hf_repo`, `hf_filename`, `destination`, `started_at`,
      and `error` fields for the selected entry.
- [x] AC-17: Given the detail panel is visible, when `[Enter]` is pressed, then it hides;
      pressing `[Enter]` again shows it. The toggle persists across `refresh_data` calls.

### Downloads view — keybindings (AC-18 – AC-20)

- [x] AC-18: Given a download with status `"downloading"` is selected, when `[c]` is
      pressed, then `cancel_requested` is set to `True` on that `DownloadState` and a
      `notify` message is shown.
- [x] AC-19: Given a download with status `"failed"` is selected, when `[r]` is pressed,
      then `_do_download(model_id)` is called and a new `DownloadState` replaces the
      failed entry in `self._downloads`.
- [x] AC-20: Given the history section contains at least one `done/failed/cancelled`
      entry, when `[x]` is pressed, then all history entries are removed from
      `self._downloads` and the table is refreshed.

### Dashboard sync (AC-21)

- [x] AC-21: Given an active download, when `DashboardView.refresh_data` is called,
      then the "Active Downloads" widget shows the same 16-char block-char progress bar
      as the Downloads table row for that download.

### CA bundle (AC-22 – AC-25)

- [x] AC-22: Given `ca_bundle=None`, when `download_model()` is called, then `requests.get`
      is invoked with `verify=True` (system trust store).
- [x] AC-23: Given `ca_bundle="/path/to/bundle.pem"` and the file exists, when
      `download_model()` is called, then `requests.get` is invoked with
      `verify="/path/to/bundle.pem"`.
- [x] AC-24: Given `ca_bundle="/nonexistent/path.pem"`, when `download_model()` is
      called, then it raises `DownloadError` with a message referencing the missing
      path, and no HTTP request is made.
- [x] AC-25: Given `DownloadsConfig` with `ca_bundle = "/path/to/bundle.pem"`, when
      `ManagerConfig.resolved_ca_bundle` is accessed, then it returns a `Path` object
      equal to `Path("/path/to/bundle.pem")`; when `ca_bundle` is `""` or unset, it
      returns `None`.

### Security (AC-26 – AC-27)

- [x] AC-26: Given a `DownloadState` with Rich markup in `error` or `hf_repo`, when
      the detail panel renders it, then the markup is escaped and no coloured/bold
      text injection occurs.
- [x] AC-27: Given a model whose `hf_filename` contains `../`, when `download_model()`
      is called, then it raises `DownloadError` without writing any file.

### Multi-shard downloads (AC-28 – AC-32)

- [x] AC-28: Given a sharded GGUF repo (filenames matching `<prefix>NNNNN-of-MMMMM.gguf`),
      when `_shard_filenames(selected, all_files)` is called with any shard in the set,
      then it returns all M shards sorted by part number.
- [x] AC-29: Given `_shard_filenames` called with a non-shard filename, then it returns
      `[selected]` unchanged.
- [x] AC-30: Given a sharded model queued for download, when `_do_download()` runs, then
      one `DownloadState` per shard is created (labelled `model-id [N/M]`), all shards
      appear in the Downloads tab as `queued`, and each is downloaded sequentially.
- [x] AC-31: Given shard N fails or is cancelled, then all remaining `queued` shards
      are set to `cancelled` immediately, and no further HTTP requests are made.
- [x] AC-32: Given an `hf_filename` containing a subfolder path (e.g. `Q4_0/file.gguf`),
      when `download_model()` is called, then intermediate directories are created and
      the file is written without error.

### Cancel bridge (AC-33)

- [x] AC-33: Given a running download, when `ui_state.cancel_requested = True` is set
      (e.g. via the `[c]` keybinding), then the `on_progress` bridge propagates
      `cancel_requested` to the internal `DownloadState`, causing the downloader to
      stop on the next chunk boundary.

### OS-native trust store (AC-34)

- [x] AC-34: Given `ca_bundle = ""` in `manager.toml` and the `truststore` package
      installed, when the CLI starts, then `truststore.inject_into_ssl()` is called
      and HTTPS connections to HuggingFace use the OS keychain CAs, allowing downloads
      to succeed on machines without Zscaler.

## Open Questions

- [ ] Q1: `huggingface_hub.hf_hub_url` is a public API but marked internal in some
      versions. If unavailable, fallback to constructing
      `https://huggingface.co/{repo}/resolve/main/{filename}` directly — is this
      acceptable for the models in our registry (all public or HF-token auth)?
- [ ] Q2: Should speed/ETA be persisted across TUI restarts (e.g. in a sidecar file)?
      Current scope says no — history is in-memory only.
- [ ] Q3: Should `ca_bundle` also apply to the `hf_hub_url` resolve step (which uses
      `huggingface_hub` under the hood)? `huggingface_hub` itself reads
      `REQUESTS_CA_BUNDLE` natively, so setting that env var covers both steps. Explicit
      support in the config is only needed for the `requests.get` streaming call.

## References

- `memory/specs/008-llama-server-manager.md` — AC-20, AC-21 (download, SHA-256 verification)
- `runtime/manager/src/prometheus_manager/downloader.py`
- `runtime/manager/src/prometheus_manager/tui/views/downloads.py`
- `runtime/manager/src/prometheus_manager/tui/views/dashboard.py`
