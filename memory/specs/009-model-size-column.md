---
id: "009"
title: "Model Size Column in TUI Tables"
status: closed
current-agent: ""
pipeline-log: []
created: 2026-04-02
---

# 009 — Model Size Column in TUI Tables

## Context

The Registry and Instances views in the TUI (`pmgr tui`) display model metadata in
DataTable widgets. Users have no quick way to see the disk size of each model GGUF file
without leaving the TUI. When `downloaded = true` and a `path` is set, the file size is
directly readable from the filesystem.

## Goal

Add a **Size** column to the Registry and Dashboard (compact instances) tables showing
the GGUF file size in a human-readable format (e.g. `4.3 GB`, `737 MB`). When the file
is not present or the path is unknown, display `—`.

## Scope

- Registry view table (`RegistryView`, `memory/specs/008`)
- Dashboard compact instance table (`DashboardView`, `memory/specs/008`)
- Instances view table (`InstancesView`, `memory/specs/008`)
- No changes to `RegistryEntry` dataclass or `registry.yaml` schema — size is computed
  at display time from `entry.path` via `os.path.getsize`.

## Out of Scope

- Persisting file size in `registry.yaml`
- Download view (size shown during download progress as total bytes)

## Acceptance Criteria

| ID | Criterion |
|----|-----------|
| AC-1 | Registry table has a **Size** column as the last column. |
| AC-2 | The Size column always shows `X.X GB` (one decimal, always GB units). |
| AC-3 | When `entry.downloaded` is `False` or `entry.path` is empty, Size shows `—`. |
| AC-4 | When `entry.path` is set but the file does not exist on disk, Size shows `—` (no exception). |
| AC-5 | Dashboard compact table has a **Size** column as the last column. |
| AC-6 | Dashboard Size column uses the same formatting as AC-2 / AC-3 / AC-4. |
| AC-7 | Instances view table has a **Size** column as the last column. |
| AC-8 | Instances Size column uses the same formatting as AC-2 / AC-3 / AC-4. |
| AC-9 | A `_fmt_size(path: str) -> str` helper is defined once in `tui/utils.py` and covered by unit tests. |
| AC-10 | All existing manager tests continue to pass. |

## Implementation Notes

- `fmt_size(path)` reads `Path(path).stat().st_size` wrapped in a `try/except OSError`
  and returns `—` on any error.
- Format: always `f"{size / 1_000_000_000:.1f} GB"` — consistent unit regardless of file size.
- Column width: `7` characters is sufficient (`"4.3 GB"` = 6 chars with padding).
- The helper should live in a shared `utils.py` module under `tui/` to avoid duplication
  between Dashboard and Registry views.
