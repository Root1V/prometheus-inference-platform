---
id: "010"
title: "Registry View Redesign — Catalog Identity + Discovery Flag"
status: closed
current-agent: ""
pipeline-log: []
created: 2026-04-02
updated: 2026-04-02
---

# 010 — Registry View Redesign — Catalog Identity + Discovery Flag

## Problem Statement

The current Registry view (`RegistryView`) is visually a near-duplicate of the Instances view:
it shows Port, Downloaded, and Path — columns that belong to runtime management — while omitting
the metadata that makes a registry valuable: context window, estimated RAM, HuggingFace source,
and whether the model is being advertised to external consumers (gateway, API clients).

Three concrete problems arise from this:

1. **Wrong concept**: Registry rows shadow Instances rows. A user cannot tell at a glance what
   the registry *knows about* versus what is *actively running*. The two views should answer
   different questions.

2. **Row count mismatch**: the current Registry always shows the same rows as the full entry
   list, but has no relationship to running state. Instances shows running-first then stopped.
   A Registry should show ALL known models (one row per YAML entry) regardless of lifecycle
   state, making it possible to have more rows than Instances (registered but not downloaded)
   or fewer (orphan processes not in registry).

3. **No discovery concept**: there is no way to tell which models are being advertised to the
   gateway and other consumers via the Manager REST API (`/v1/models`). Operators cannot
   selectively expose a subset of the registry without editing YAML manually. There is also
   no automatic link between lifecycle events (start/stop) and discovery state.

## Goals

- [ ] Redefine Registry as a **model catalog view**: one row per `registry.yaml` entry,
      focused on artifact identity and acquisition metadata.
- [ ] Add a `discovery` boolean field to `RegistryEntry` and `registry.yaml` schema.
      When `true`, the model is included in the Manager REST API `/v1/models` response
      (and therefore visible to the gateway and other consumers).
- [ ] Show the `discovery` state as a dedicated column in the table with a `●` / `○` indicator.
- [ ] Allow operators to manually toggle `discovery` with a keybinding (`[v]`).
- [ ] Automatically set `discovery: true` when an instance starts successfully.
- [ ] Automatically set `discovery: false` when an instance stops or is deregistered.
- [ ] Replace runtime columns (Port, Path/HF Repo) with catalog columns
      (Family, Quant, Ctx, Est. RAM, Source, Size).
- [ ] Add a detail panel below the table showing all fields for the selected entry.
- [ ] Downloaded models appear first (sorted by ID); not-downloaded models appear after,
      visually dimmed.

## Non-Goals

- No changes to the Manager REST API contract (spec 008 `/v1/models` endpoint).
- No changes to Dashboard view or Downloads view.
- No new CLI commands — only TUI and registry.yaml schema changes.
- No change to how the gateway consumes the Manager API (`discovery` is internal to the manager).
- No bulk-toggle of discovery for multiple entries at once.
- No changes to the Instances table columns or the Runtime Metrics / Context & Limits panels
  (only the **Identity sub-panel** is trimmed — see Instances cleanup below).

## Proposed Solution

### Discovery field in `registry.yaml`

```yaml
models:
  - id: llama3-8b-q4-local
    # ... existing fields ...
    discovery: true     # NEW — optional, defaults to false if absent
```

`discovery: true` means the model is returned by `GET /v1/models` on the Manager REST API.
The gateway polls this endpoint to discover available backends.

Lifecycle hooks in `ManagerApp` (TUI) and the lifecycle module (CLI) update this field
automatically:
- Instance start completes → `registry.update(model_id, discovery=True)`
- Instance stop/deregister → `registry.update(model_id, discovery=False)`

### Registry view columns

| Column | Source | Notes |
|--------|--------|-------|
| ID | `entry.id` | Stretched to fill available width |
| Family | `entry.family` | e.g. `llama3`, `mistral` |
| Quant | `entry.quantization` | e.g. `Q4_0`, `Q4_K_M` |
| Ctx | `entry.context_length` | Displayed as `8K`, `32K`, etc. |
| Est. RAM | `entry.rss_estimate_mb` | Displayed as `4.9 GB`; `—` if unset |
| Dl | `entry.downloaded` | `✓` / `✗` |
| Discovery | `entry.discovery` | `●` (on) / `○` (off) |
| Source | `entry.path` or `hf:repo/file` | basename of path; `hf:repo/file` if no path |
| Size | `fmt_size(entry.path)` | From spec 009; `—` if not downloaded |

Columns removed from current view: Port, full Path/HF Repo (replaced by Source).

### Detail panel

A collapsible panel below the table (collapsed by default, toggled with `[Enter]`) shows
**all catalog and configuration metadata** for the selected entry — everything that describes
the model itself, independent of whether it is running:

```
┌─ Detail — llama3-8b-q4-local ──────────────────────────────────────────────┐
│ ┌─ Identity ───────────────────────────────────┐ ┌─ Acquisition ──────────┐│
│ │ Registry ID   llama3-8b-q4-local             │ │ HF repo   bartowski/…  ││
│ │ Family        llama3                         │ │ HF file   Meta-Llama…  ││
│ │ Quantization  Q4_0                           │ │ SHA-256   a3f9c1b2…  ✓ ││
│ │ GGUF file     Meta-Llama-3-8B-Q4_0.gguf     │ │ Downloaded ✓           ││
│ │ Full path     /Users/…/Meta-Llama-…Q4_0.gguf│ │ File size  4.3 GB      ││
│ └──────────────────────────────────────────────┘ └────────────────────────┘│
│ ┌─ Model Spec ─────────────────────────────────────────────────────────────┐│
│ │ Context window   8192 tokens    Est. RAM   4.9 GB   Log level   info     ││
│ └──────────────────────────────────────────────────────────────────────────┘│
│ ┌─ Deployment ─────────────────────────────────────────────────────────────┐│
│ │ Port   8086    Backend URL   http://127.0.0.1:8086    Discovery   ● ON   ││
│ └──────────────────────────────────────────────────────────────────────────┘│
└────────────────────────────────────────────────────────────────────────────┘
```

**Fields shown in the detail panel:**

| Section | Field | Source |
|---------|-------|--------|
| Identity | Registry ID, Family, Quantization, GGUF file (basename), Full path | `RegistryEntry` |
| Acquisition | HF repo, HF filename, SHA-256 (truncated + ✓), Downloaded (bool), File size | `RegistryEntry` + `os.stat` |
| Model Spec | Context window (tokens), Est. RAM (`rss_estimate_mb`), Log level | `RegistryEntry` |
| Deployment | Port, Backend URL, Discovery (● / ○) | `RegistryEntry` |

All fields are static — no live HTTP calls. This panel renders instantly from the in-memory registry.

### Visual grouping

Downloaded entries appear first (sorted alphabetically by ID).
Not-downloaded entries appear after a separator row `── not downloaded ──`,
rendered in a muted/dim style.

### Keybindings

| Key | Action | Description |
|-----|--------|-------------|
| `a` | add | Add entry to registry |
| `x` | delete | Remove entry from registry |
| `w` | download | Download GGUF from HF |
| `v` | toggle_discovery | Toggle discovery on/off for selected row |
| `Enter` | toggle_detail | Expand/collapse detail panel |

## Data Model

### `RegistryEntry` (updated)

```python
@dataclass
class RegistryEntry:
    id: str
    context_length: int
    port: int
    path: str = ""
    family: str = ""
    quantization: str = ""
    log_level: str = "info"
    downloaded: bool = False
    discovery: bool = False        # NEW
    rss_estimate_mb: int | None = None
    backend_url: str = ""
    hf_repo: str = ""
    hf_filename: str = ""
    hf_sha256: str = ""
```

`to_dict()` and `_load()` must handle the new field.
Existing `registry.yaml` entries without `discovery` key load as `discovery: False`.

### `Registry.update()` method

Must already support arbitrary kwargs (e.g. `reg.update(model_id, discovery=True)`).
Confirm this works or extend it.

### Manager REST API `/v1/models` filtering

`GET /v1/models` on the Manager REST API must return only entries where `discovery: true`.
If the existing implementation returns all entries, it must be updated.

### Instances view — Identity sub-panel cleanup

The Instances detail panel currently shows an **Identity** sub-section containing fields that
are entirely catalog metadata (not runtime): Family, Quantization, GGUF file, File size,
HuggingFace repo, SHA-256. These fields are meaningful without a running process and belong
in Registry.

This spec adds a small cleanup to `InstancesView._update_detail()`:

**Removed from Instances Identity** (catalog — now in Registry detail):
- Family
- Quantization
- GGUF file (basename)
- File size
- HuggingFace repo
- SHA-256

**Kept in Instances Identity** (runtime/operational — only valid when process exists):
- PID
- Port *(quick operational reference — process is listening here)*
- State
- Uptime

**Unchanged in Instances Context & Limits** (all runtime, fetched live):
- n_ctx used (from `/props` KV cache ratio)
- Active slots (from `/slots`)
- Batch size, GPU layers, Threads (from process cmdline)

**Unchanged in Instances Runtime Metrics** (all runtime):
- Uptime, tokens/s, prompt eval ms, token eval ms, tokens served, requests active,
  chat template, Backend URL.

The Instances Identity sub-panel for non-orphan entries therefore becomes:

```
┌─ Identity ─────────────────────────────────┐
│ PID     12345                               │
│ Port    8086                                │
│ State   ready                               │
│ Uptime  2h 14m 32s                          │
└────────────────────────────────────────────┘
```

> For full model metadata, switch to the Registry view.

## Security Considerations

- `discovery` is persisted to `registry.yaml` on the bare-metal host — same trust boundary
  as all other registry fields. No new attack surface introduced.
- Toggling `discovery` does not start or stop any process — it only changes what the REST
  API returns to callers. Callers (gateway) already authenticate via RS256 JWT.
- Auto-toggle hooks run on the same host process as lifecycle management; no external input
  is involved in the toggle.

## Acceptance Criteria

- [ ] **AC-1**: Given `registry.yaml` with existing entries that have no `discovery` field,
      when `Registry._load()` parses them, then each `RegistryEntry.discovery` is `False`.

- [ ] **AC-2**: Given `Registry.update(model_id, discovery=True)` is called, when the file
      is reloaded, then `entry.discovery` is `True` and all other fields are unchanged.

- [ ] **AC-3**: Given 4 entries in `registry.yaml` and 2 running instances, when
      `RegistryView.refresh_data(entries)` is called, then the table has exactly 4 rows
      (one per registry entry, not one per running instance).

- [ ] **AC-4**: Given a registry with 2 downloaded and 2 not-downloaded entries, when the
      table renders, then downloaded entries appear before not-downloaded entries, and the
      not-downloaded block is visually separated.

- [ ] **AC-5**: Given an entry with `discovery: True`, when the Registry table renders,
      then the Discovery cell for that row shows `●`.

- [ ] **AC-6**: Given an entry with `discovery: False`, when the Registry table renders,
      then the Discovery cell for that row shows `○`.

- [ ] **AC-7**: Given a row is selected in the Registry table, when the operator presses `v`,
      then `entry.discovery` is toggled, the change is persisted to `registry.yaml`, and the
      Discovery cell updates immediately without a full table refresh.

- [ ] **AC-8**: Given an instance start completes successfully (lifecycle `start` action),
      when the manager updates the registry, then `entry.discovery` is set to `True` for
      that model ID.

- [ ] **AC-9**: Given an instance stops (lifecycle `stop` or `deregister` action),
      when the manager updates the registry, then `entry.discovery` is set to `False` for
      that model ID.

- [ ] **AC-10**: Given the Registry table is rendered, then columns Port, CPU%, RAM%, GPU%,
      and sparklines are NOT present.

- [ ] **AC-11**: Given an entry with `context_length: 8192`, when the table renders, then
      the Ctx cell shows `8K`.

- [ ] **AC-12**: Given an entry with `rss_estimate_mb: 5000`, when the table renders, then
      the Est. RAM cell shows `4.9 GB`.

- [ ] **AC-13**: Given an entry with `rss_estimate_mb: None`, when the table renders, then
      the Est. RAM cell shows `—`.

- [ ] **AC-14**: Given an entry with a local path and `downloaded: true`, when the Source
      cell renders, then it shows only the filename (basename), not the full path.

- [ ] **AC-15**: Given an entry with no path but `hf_repo` and `hf_filename` set, when the
      Source cell renders, then it shows `hf:<repo>/<filename>`.

- [ ] **AC-16**: Given a row is selected, when `Enter` is pressed, then the detail panel
      below the table expands showing all fields: Registry ID, Family, Quantization, GGUF
      file, full path, HF repo, HF filename, SHA-256, Downloaded, File size, context window,
      Est. RAM, log level, Port, Backend URL, and Discovery state.

- [ ] **AC-17**: Given the detail panel is expanded, when `Enter` is pressed again, then
      the panel collapses and the table reclaims the full height.

- [ ] **AC-18**: Given `GET /v1/models` is called on the Manager REST API, when 2 of 4
      entries have `discovery: true`, then the response contains exactly those 2 entries.

**Instances Identity cleanup ACs:**

- [ ] **AC-19**: Given a non-orphan running instance is selected in the Instances view,
      when the Identity sub-panel renders, then it shows ONLY: PID, Port, State, Uptime —
      and does NOT show Family, Quantization, GGUF file, File size, HF repo, or SHA-256.

- [ ] **AC-20**: Given a stopped (non-running, non-orphan) entry is selected in the
      Instances view, when the Identity sub-panel renders, then it shows PID `—`, Port,
      State `stopped`, Uptime `—` — and does NOT show catalog fields.

- [ ] **AC-21**: Given an orphan (running but not in registry) is selected in the Instances
      view, when the Identity sub-panel renders, then it shows PID, Port, Uptime, and the
      warning "Not in registry — use [a] to register" — unchanged from current behaviour.

## Open Questions

- [ ] Q1: Should `discovery` default to `true` for entries that already have `downloaded: true`,
      or always default to `false` for backward compatibility? Current decision: always `false`
      (explicit opt-in is safer — operators decide what to expose).

- [ ] Q2: Should stopping an instance immediately set `discovery: false`, or should there be a
      grace period (e.g. 30 s) to allow restart? Current decision: immediate — the gateway
      will detect the backend as unavailable anyway; hiding it from discovery is correct.

## References

- Related specs: `memory/specs/008-llama-server-manager.md` (AC-22e Registry view, data model)
- Related specs: `memory/specs/009-model-size-column.md` (fmt_size helper reused here)
