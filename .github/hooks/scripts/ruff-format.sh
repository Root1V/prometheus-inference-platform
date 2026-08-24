#!/usr/bin/env bash
# PostToolUse hook — auto-formats Python files with ruff after every edit.
# VS Code injects $TOOL_INPUT_FILE_PATH with the path of the edited file.
set -euo pipefail

# Only act on Python files
[[ "${TOOL_INPUT_FILE_PATH:-}" == *.py ]] || exit 0

# File must exist (skip deletions)
[[ -f "$TOOL_INPUT_FILE_PATH" ]] || exit 0

# Run ruff format silently — errors are non-blocking (exit code != 2)
uv run ruff format "$TOOL_INPUT_FILE_PATH" --quiet 2>/dev/null || true

exit 0
