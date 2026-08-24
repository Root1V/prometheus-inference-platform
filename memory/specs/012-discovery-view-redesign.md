---
id: "012"
title: "Discovery View — HuggingFace Model Search & One-Key Download"
status: closed
current-agent: ""
pipeline-log: []
created: 2026-04-02
updated: 2026-04-11
---

<!-- Changelog: draft → repurposed as HF search → simplified flow → multi-shard detection (2026-04-02 to 2026-04-11) -->

# 012 — Discovery View — HuggingFace Model Search & One-Key Download

## Problem Statement

Adding a new model to Prometheus currently requires knowing the exact HuggingFace repo ID
and GGUF filename, manually editing `registry.yaml`, and then triggering a download
separately — a multi-step process that is undiscoverable for new operators.

The **Discovery tab** (tab 5) currently serves as an orphan-process inspector with very
low day-to-day value. This spec repurposes it as a focused **model search and download**
tool following one single user flow: **type → browse → press `[d]`**.

## Goals

- [x] Repurpose Discovery tab as an integrated HuggingFace model search & download tool.
- [x] `Input` widget for keyword search (e.g. `llama 3 8b`, `mistral`, `qwen`).
- [x] Search calls `list_models(filter="gguf", ...)` in a background worker — UI never blocks.
- [x] Results table shows matching repos: `Repo · Downloads · Likes · Updated`.
- [x] Moving the cursor to a repo row automatically fetches its `.gguf` file list.
- [x] Files table shows filenames and inferred quantization: `Filename · Quant`.
- [x] `[d]` on any file row: silently auto-generates ID + port, registers in `registry.yaml`,
      enqueues download, and switches to the Downloads tab.
- [x] Move orphan-process rows to Instances view (separator + dim) — no data is lost.
- [x] Reuse existing `_do_download()` worker and `download_model()` — no new download code.

## Non-Goals

- No editable ID / port form — auto-generated silently (user edits in Registry view if needed).
- No size display (HF API does not expose per-file sizes in listing endpoints).
- No pagination (cap at 30 search results).
- No HF auth UI — token read from `HF_TOKEN` env var as already configured.
- No change to `download_model()`, `DownloadState`, `DownloadsView`, `scanner.py`.

## Proposed Solution

### Layout — two panels, zero forms

```
┌─ Search ─────────────────────────────────────────────────────────────────┐
│  🔍 [ llama 3 8b                                               ]  [/]    │
│     24 results                                                            │
└───────────────────────────────────────────────────────────────────────────┘
┌─ Results ──────────────────────────────── [↑↓] navigate · [Enter] files ─┐
│  Repo                                   ↓Downloads  ★Likes   Updated     │
│ ▶ bartowski/Llama-3.2-3B-Instruct-GGUF   1.2M       892    2025-12-01   │
│   unsloth/Meta-Llama-3-8B-Instruct-GGUF  980K       654    2025-11-20   │
│   ...                                                                     │
└───────────────────────────────────────────────────────────────────────────┘
┌─ Files — bartowski/Llama-3.2-3B-Instruct-GGUF ────────── [d] Download ──┐
│  Filename                                    Quant                        │
│▶ Llama-3.2-3B-Instruct-Q4_K_M.gguf          Q4_K_M                      │
│  Llama-3.2-3B-Instruct-Q8_0.gguf            Q8_0                        │
│  Llama-3.2-3B-Instruct-IQ3_M.gguf           IQ3_M                       │
└───────────────────────────────────────────────────────────────────────────┘
```

Three containers, no form panel. Results fills the space (1fr). Files is compact (auto,
max 12 rows). Search is a single line. Full keyboard navigation with `Tab`.

### Component breakdown

**`DiscoveryView`** (`tui/views/discovery.py`) — full rewrite:
- `compose()`: `#search-group` (auto) → `#results-group` (1fr) → `#files-group` (auto).
- State: `_results`, `_files`, `_selected_repo`, `_selected_file`.
- Bindings: `/` / `Enter` → search; `d` → download; `Tab` cycles focus.
- Workers: `_worker_search(query)` and `_worker_fetch_files(repo_id)` — both `@work(thread=True)`.

**`app.py`** changes (minimal):
- `action_discovery_download()`: auto-generates ID (`_auto_id`) + port (`_next_free_port`),
  resolves collisions, calls `registry.add()`, `_do_download()`, switches to Downloads tab.
- `_next_free_port(registry)`: lowest port ≥ 8081 not in use.
- `_auto_id(filename, registry)`: slug from filename + `-local`; appends `-2`, `-3` if duplicate.
- `action_discovery_adopt()` / `action_discovery_kill()` / `_do_kill_pid()` removed.
- `_poll()` no longer calls `DiscoveryView.refresh_data()`.
- `InstancesView.refresh_data()` appends `─── Unmanaged ───` separator + orphan rows (dim).

## Acceptance Criteria

> All ACs must have a corresponding test in `runtime/manager/tests/test_discovery_view.py`.
> All HuggingFace API calls must be mocked — no live network required.

### AC-1 — Layout: three groups
`DiscoveryView.compose()` yields exactly three bordered `Vertical` containers:
`#search-group`, `#results-group` (height `1fr`), `#files-group`.

### AC-2 — Search input
`#search-group` contains a Textual `Input` (`#search-input`, placeholder:
`"Search HuggingFace for GGUF models…"`) and a `Static` (`#search-status`).

### AC-3 — Search trigger & empty guard
`Enter` in `#search-input` (or `/` binding from any widget) calls `action_search()`.
If the query is blank/whitespace, `#search-status` shows
`"[yellow]Enter a model name to search[/yellow]"` and no worker is started.

### AC-4 — Non-blocking search worker
`action_search()` spawns a `@work(thread=True)` worker calling
`list_models(filter="gguf", search=query, limit=30, token=hf_token)`.
`#search-status` shows `"[yellow]Searching…[/yellow]"` during the call;
`"N results"` after success; `"[red]Search failed: <msg>[/red]"` on exception.

### AC-5 — Results table
`#results-table` columns: `Repo`, `↓Downloads`, `★Likes`, `Updated`.
- `Downloads` / `Likes`: `_fmt_count()` — `1.2K`, `980K`, `1.2M`.
- `Updated`: `YYYY-MM-DD` string.

### AC-6 — Cursor on results → auto-fetch files
Moving the cursor to a results row starts `_worker_fetch_files(repo_id)` (debounced —
skip if the same repo is already in-flight).
`#files-group.border_title` = `"Files — fetching…"` while in-flight,
then `"Files — <repo_id>"` on completion,
or `"Files — error: <msg>"` on failure.

### AC-7 — Files table
`#files-table` columns: `Filename`, `Quant`.
- `Quant` = `_infer_quant(filename)`: regex match for `Q4_K_M`, `Q8_0`, `IQ3_M`,
  `F16`, `F32`, `BF16`, etc. Returns `?` if none matched.
- Only `.gguf` files are shown.

### AC-8 — `[d]` Download key
`[d]` is available from any focused widget in the view.
If `_selected_file` is empty, `#search-status` shows
`"[yellow]Select a file first[/yellow]"` and aborts.

### AC-9 — `action_discovery_download()` in `app.py`
1. `shard_files = _shard_filenames(_selected_file, _files)` — detects multi-part shards;
   returns `[_selected_file]` for single-file models.
2. `model_id = _auto_id(shard_files[0], registry)` — unique, no collision.
3. `port = _next_free_port(registry)`.
4. `registry.add(RegistryEntry(id=model_id, port=port, hf_repo=_selected_repo,
   hf_filename=shard_files[0], hf_filenames=shard_files[1:] if len>1 else [],
   downloaded=False, context_length=4096))`.
5. `self._do_download(entry)` — downloads all shards sequentially.
6. `self.action_switch_tab('downloads')`.
7. `self.notify(f"Queued download: {model_id}", severity="information")`.

### AC-10 — `_auto_id(filename, registry) -> str`
- Strip `.gguf`, lowercase, replace `[^a-z0-9]+` with `-`, strip leading/trailing `-`,
  append `-local`, truncate to 63 chars.
- If ID already exists in registry, append `-2`, `-3`, … until unique.

### AC-11 — `_next_free_port(registry) -> int`
Returns the lowest integer ≥ 8081 not already used by any registry entry.

### AC-12 — `_infer_quant(filename) -> str`
Pure function. Cases to cover in tests:
`Q4_K_M`, `Q8_0`, `IQ3_M`, `F16`, `F32`, `BF16`, unknown → `?`.

### AC-13 — HF token passthrough
Both workers pass `token=self._config.hf_token` (may be `None` for anonymous access).

### AC-14 — Tab focus cycle
`Tab` cycles focus: `#search-input` → `#results-table` → `#files-table` → `#search-input`.

### AC-15 — Orphan rows in Instances view
`InstancesView.refresh_data()` appends, after managed rows, a dim separator row
`"─── Unmanaged processes ───"` and then one row per `not s.managed` state.
If no orphans exist, no separator is added.

### AC-16 — `DiscoveryView` has no `refresh_data()`
`_poll()` in `app.py` does not call anything on `DiscoveryView`. The view is purely
event-driven (user input → worker → table update).

### AC-17 — Tests
`runtime/manager/tests/test_discovery_view.py` covers:
- AC-3: empty search guard.
- AC-10: `_auto_id()` — basic slug, collision suffix.
- AC-11: `_next_free_port()` — empty, single, collision.
- AC-12: `_infer_quant()` — all quant patterns + unknown.
- AC-9: `action_discovery_download()` happy path (mocked `registry`, `_do_download`).
- AC-4: search exception → status shows error.
- AC-6: file fetch exception → border_title shows error.
- AC-8: `[d]` with no file selected → notification, no registry add.
All HF calls mocked via `unittest.mock.patch`.

### AC-18 — Multi-shard detection
`_shard_filenames(selected, all_files)` (pure function, importable by tests):
- Given a filename matching `<prefix>NNNNN-of-MMMMM.gguf`, returns all M sibling
  shards from `all_files` sharing the same prefix and total, sorted by part number.
- Given a non-shard filename, returns `[selected]` unchanged.
- Given an empty `all_files`, returns `[selected]` as a safe fallback.
- Test coverage: at least 5 cases (ordered, middle shard selected, non-shard,
  empty list, unrelated sibling with different total excluded).

## Implementation Notes

- `huggingface_hub.list_models()` — relevant `ModelInfo` fields: `id`, `downloads`,
  `likes`, `lastModified` (datetime or None), `tags`.
- `huggingface_hub.list_repo_files()` — returns `Iterable[str]` of all paths in repo.
  Filter: `[f for f in files if f.lower().endswith('.gguf')]`.
- CSS added to `app.py` global `CSS` block under `/* ── Discovery ── */`.
- `DiscoveryView` receives `registry: Registry` reference via `__init__` so it can call
  `_next_free_port` and `_auto_id` at download time.
- `#results-group` and `#files-group` DataTables use `cursor_type = "row"`.
- `#files-group` starts with `border_title = "Files"` and `height: auto; max-height: 12`.
