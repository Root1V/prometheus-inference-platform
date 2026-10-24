"""What the assistant can look at — PRM-272.

Every tool is a call to this gateway's own `/admin/api/*` over loopback,
carrying the assistant's own bearer token. Nothing is special-cased: scope
enforcement, the rate limiter and PRM-157's audit trail all run exactly as they
do for a human, and the trail therefore names the *assistant* as the actor.
That is the whole reason the assistant has its own principal rather than
borrowing the signed-in admin's — an operator reading the audit log has to be
able to tell which of the two acted.

**Every tool returns a projection, never the raw payload.** `/admin/api/instances`
answers with ~900 tokens per instance, most of it paths and hashes, and this
platform's own model decodes at ~59 tok/s: feeding it the raw body is the
difference between an answer in ten seconds and one in ninety. The projections
below are what an operator would read out loud, and nothing else.

This module is read-only on purpose. Acting is the harder half — it needs the
confirmation list the operator decided on (deletions and critical actions ask;
everything else acts and is audited) — and it lands separately.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import httpx

_TIMEOUT_S = 20.0
#: A log tail long enough to show a crash and short enough not to drown the
#: context. Today's diagnosis needed the last few lines, not the last thousand.
_LOG_LINES = 40


def _node_summary(node: dict[str, Any]) -> dict[str, Any]:
    profile = node.get("profile") or {}
    engines = node.get("engines_detected") or []
    out: dict[str, Any] = {
        "name": node.get("name"),
        "reachable": node.get("is_active"),
        "enabled": node.get("enabled"),
        "last_seen_at": node.get("last_seen_at"),
        "url": node.get("manager_url"),
    }
    if profile:
        out["hardware"] = {
            "cpu": profile.get("cpu_model"),
            "arch": profile.get("arch"),
            "cores": profile.get("cpu_cores_physical"),
            "ram_total_mb": profile.get("ram_total_mb"),
            "ram_available_mb": profile.get("ram_available_mb"),
            "disk_free_mb": profile.get("disk_free_mb"),
            "accelerator": (profile.get("accelerator") or {}).get("name"),
        }
        # PRM-263's rule survives the summary: a reading that failed is named,
        # not dropped, or "unknown" reads as "nothing there".
        if profile.get("probe_errors"):
            out["hardware"]["probe_errors"] = profile["probe_errors"]
    if engines:
        out["engines_installed"] = [e["backend"] for e in engines if e.get("installed")]
        # PRM-271: what the hardware rules out is a different answer from what
        # is merely absent, and the assistant must not suggest the impossible.
        out["engines_ruled_out"] = {
            e["backend"]: e.get("requires") for e in engines if e.get("applicable") is False
        }
    return out


def _instance_summary(entry: dict[str, Any]) -> dict[str, Any]:
    return {
        "model": entry.get("model_slug") or entry.get("model_id"),
        "instance_id": entry.get("id"),
        "node": entry.get("node"),
        "state": entry.get("state"),
        "backend": entry.get("backend"),
        "modality": entry.get("modality"),
        "port": entry.get("port"),
        "rss_mb": round(entry["rss_mb"]) if entry.get("rss_mb") else None,
        "uptime_s": round(entry["uptime_s"]) if entry.get("uptime_s") else None,
        "error": entry.get("error_message"),
    }


async def _get(
    client: httpx.AsyncClient, base_url: str, path: str, token: str, **params: Any
) -> Any:
    resp = await client.get(
        f"{base_url}{path}",
        headers={"Authorization": f"Bearer {token}"},
        params={k: v for k, v in params.items() if v is not None} or None,
    )
    resp.raise_for_status()
    return resp.json()


async def list_nodes(client: httpx.AsyncClient, base_url: str, token: str) -> Any:
    body = await _get(client, base_url, "/admin/api/nodes", token)
    nodes = body if isinstance(body, list) else body.get("nodes", [])
    return [_node_summary(n) for n in nodes]


async def list_instances(
    client: httpx.AsyncClient, base_url: str, token: str, state: str | None = None
) -> Any:
    body = await _get(client, base_url, "/admin/api/instances", token)
    entries = body.get("instances", []) if isinstance(body, dict) else body
    summaries = [_instance_summary(e) for e in entries]
    if state:
        summaries = [s for s in summaries if s["state"] == state]
    return summaries


async def list_activity(client: httpx.AsyncClient, base_url: str, token: str) -> Any:
    """Who is consuming and how much, right now — PRM-235."""
    return await _get(client, base_url, "/admin/api/activity", token)


async def tail_instance_log(
    client: httpx.AsyncClient, base_url: str, token: str, node: str, model_id: str
) -> Any:
    """The last lines of one instance's log.

    The single most useful thing an operator reaches for, and the one that
    needed two machines and a shell today. A crash leaves the log ending
    mid-task; a stop writes `cleaning up before exit` — so the *end* of the
    file is what distinguishes them, which is why this tails rather than greps.
    """
    body = await _get(
        client,
        base_url,
        f"/admin/api/nodes/{node}/instances/{model_id}/logs",
        token,
        lines=_LOG_LINES,
    )
    return body.get("lines", body) if isinstance(body, dict) else body


#: The wire definitions handed to the model. Descriptions are written for the
#: model, not for a human reading this file: they say when to reach for the
#: tool, because a model that cannot tell two tools apart calls both.
DEFINITIONS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "list_nodes",
            "description": (
                "The machines in the fleet: whether each is reachable, when it was last "
                "heard from, its hardware, and which inference engines it has installed "
                "or is ruled out from running. Use for questions about nodes, hardware, "
                "capacity or which machines are up."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_instances",
            "description": (
                "The model instances across all nodes: which model, on which node, its "
                "state, memory and uptime. Use for questions about what is running, what "
                "is stopped, or why a model is unavailable."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "state": {
                        "type": "string",
                        "description": "Optional filter, e.g. 'ready' or 'stopped'.",
                    }
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_activity",
            "description": (
                "Who is consuming the platform right now and how much — clients, models, "
                "request counts and refusals. Use for questions about traffic, spend, or "
                "which client is causing something."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "tail_instance_log",
            "description": (
                "The last lines of one model instance's log on one node. Use when asked "
                "why something failed, crashed or will not start. Call list_instances "
                "first if you do not know the node and model id."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "node": {"type": "string", "description": "Node name, e.g. 'local'."},
                    "model_id": {"type": "string", "description": "The instance id."},
                },
                "required": ["node", "model_id"],
            },
        },
    },
]

#: Annotated loosely on purpose — the handlers take different keyword
#: arguments because the tools do, and a union that enumerated them would have
#: to be edited every time one gains a parameter.
HANDLERS: dict[str, Callable[..., Awaitable[Any]]] = {
    "list_nodes": list_nodes,
    "list_instances": list_instances,
    "list_activity": list_activity,
    "tail_instance_log": tail_instance_log,
}
