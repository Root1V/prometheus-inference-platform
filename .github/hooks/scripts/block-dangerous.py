#!/usr/bin/env python3
"""
PreToolUse hook — blocks dangerous commands and protects hook scripts.
Reads JSON from stdin, writes JSON decision to stdout.
Exit code 0: decision returned via JSON. Exit code 2: blocking error.
"""
import json
import re
import sys

data = json.load(sys.stdin)
tool_name = data.get("tool_name", "")
tool_input = data.get("tool_input", {})


def deny(reason: str) -> None:
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }))
    sys.exit(0)


def ask(reason: str) -> None:
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "ask",
            "permissionDecisionReason": reason,
        }
    }))
    sys.exit(0)


def allow() -> None:
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
        }
    }))
    sys.exit(0)


# ── Block dangerous terminal commands ────────────────────────────────────────
# Check for any tool that carries a `command` field — covers all terminal tools
# regardless of VS Code's internal tool name.
command = tool_input.get("command", "")
if command:

    BLOCKED = [
        (r"git\s+push\s+.*--force",         "Force push is not allowed. Ask the user for explicit confirmation."),
        (r"git\s+push\s+-f\b",              "Force push is not allowed. Ask the user for explicit confirmation."),
        (r"git\s+push\s+.*--no-verify",     "Bypassing the pre-push hook is not allowed."),
        (r"git\s+reset\s+--hard",           "Hard reset is destructive. Ask the user for explicit confirmation."),
        (r"git\s+push\s+(origin\s+)?(main|develop)\b",
                                             "Direct push to main/develop is not allowed — use a feature branch."),
        (r"\brm\s+-rf\s+/",                 "Recursive force delete from root is blocked."),
    ]
    for pattern, reason in BLOCKED:
        if re.search(pattern, command, re.IGNORECASE):
            deny(reason)

# ── Block destructive MCP GitHub operations ──────────────────────────────────
# mcp_github_push_files can push directly to any branch — enforce same rules as terminal.
if tool_name == "mcp_github_push_files":
    branch = tool_input.get("branch", "")
    if re.search(r"^(main|develop)$", branch, re.IGNORECASE):
        deny("Direct push to main/develop via MCP is not allowed — use a feature branch.")

# mcp_github_create_or_update_file also writes directly to a branch.
if tool_name == "mcp_github_create_or_update_file":
    branch = tool_input.get("branch", "")
    if re.search(r"^(main|develop)$", branch, re.IGNORECASE):
        deny("Direct write to main/develop via MCP is not allowed — use a feature branch.")

# mcp_github_delete_file is irreversible — always ask.
if tool_name == "mcp_github_delete_file":
    ask("Deleting a file via MCP requires explicit user approval.")

# mcp_github_merge_pull_request is irreversible — always ask.
if tool_name == "mcp_github_merge_pull_request":
    ask("Merging a PR via MCP requires explicit user approval.")

# ── Protect hook scripts from agent modification ──────────────────────────────
READ_ONLY_TOOLS = {"read_file", "grep_search", "file_search", "list_dir", "semantic_search"}
file_path = (
    tool_input.get("filePath", "")
    or tool_input.get("file_path", "")
    or tool_input.get("path", "")
)
if ".github/hooks" in file_path and tool_name not in READ_ONLY_TOOLS:
    ask("Modifying hook scripts requires explicit user approval (security policy).")

allow()
