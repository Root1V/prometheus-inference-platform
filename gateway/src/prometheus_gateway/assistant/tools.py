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

import json
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


def _usd(value: Any) -> float | None:
    return round(value, 4) if isinstance(value, (int, float)) else None


def _named(name: Any, identity: Any, key: str = "client") -> dict[str, Any]:
    """A client as the operator knows it: the name, with the id beside it.

    Two fields, not "Name (id)" in one string — given that, the model read
    "Plataforma Inteligente Documental (657cdef9-…)" and reported the client as
    known "only by its id". The gateway resolves the name (admin/names.py)
    because this token cannot read the principal list. Without a name the id is
    all there is, and it goes in the one field rather than twice.
    """
    if not name:
        return {key: identity}
    return {key: name, f"{key}_id": identity}


def _consumer_summary(c: dict[str, Any]) -> dict[str, Any]:
    today = c.get("today") or {}
    worst = c.get("worst") or {}
    out: dict[str, Any] = {
        **_named(c.get("name"), c.get("identity")),
        "connection": c.get("connection_type"),
        "last_seen_ago_s": c.get("last_seen_ago_s"),
        "today": {
            "requests": today.get("request_count"),
            "tokens": today.get("total_tokens"),
            "cost_usd": _usd(today.get("cost_usd")),
        },
        "requests_in_window": (c.get("window") or {}).get("request_count"),
        "models_today": {m["model"]: m["request_count"] for m in c.get("models") or []},
        "doing": {a["label"]: a["count"] for a in c.get("actions") or []},
        "refusals": c.get("refusals") or [],
    }
    if worst.get("percent") is not None:
        out["closest_limit"] = f"{worst['dimension']} at {worst['percent']}%"
    # `is_you` is computed against the caller's token — which is the
    # assistant's. Without saying so, the model reads its own traffic as some
    # anonymous client hammering the dashboard.
    if c.get("is_you"):
        out["note"] = "this is you, the assistant, reading the platform"
    return out


async def list_activity(client: httpx.AsyncClient, base_url: str, token: str) -> Any:
    """Who is consuming and how much, right now — PRM-235.

    It returned the raw 8 KB payload; it is a projection now like the others.
    """
    body = await _get(client, base_url, "/admin/api/activity", token)
    return {
        "window_minutes": body.get("tracker_window_minutes"),
        "consumers": [_consumer_summary(c) for c in body.get("consumers") or []],
    }


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


async def list_clients(client: httpx.AsyncClient, base_url: str, token: str) -> Any:
    """Who the clients are, by name, including those that have not called yet.

    Asked "what are the users' names", it looked for a tool about users, found
    none, and answered that it could not — two times in three, measured.
    """
    body = await _get(client, base_url, "/admin/api/clients/names", token)
    return body.get("clients", [])


async def get_overview(client: httpx.AsyncClient, base_url: str, token: str) -> Any:
    """The dashboard's first page: what needs attention and how today compares."""
    body = await _get(client, base_url, "/admin/api/overview", token)
    signals = {
        k: {f: (_usd(v) if isinstance(v, float) else v) for f, v in (d or {}).items()}
        for k, d in (body.get("signals") or {}).items()
    }
    return {
        "needs_attention": [
            {"severity": a.get("severity"), "what": a.get("what"), "page": a.get("where")}
            for a in body.get("attention") or []
        ],
        "today_vs_yesterday": signals,
        "busiest_clients_today": [
            {
                **_named(r.get("name"), r.get("client_id")),
                "requests": r.get("request_count"),
                "requests_yesterday": r.get("request_count_yesterday"),
                "tokens": r.get("total_tokens"),
                "cost_usd": _usd(r.get("cost_usd")),
            }
            for r in body.get("clients") or []
        ],
        f"busiest_models_last_{body.get('days')}_days": [
            {
                "model": m.get("model_id"),
                "requests": m.get("request_count"),
                "cost_usd": _usd(m.get("cost_usd")),
                "p95_ms": (m.get("latency") or {}).get("p95_ms"),
            }
            for m in body.get("models") or []
        ],
        "closest_to_a_limit": [
            {
                **_named(r.get("name"), r.get("identity")),
                "dimension": r.get("dimension"),
                "used": r.get("used"),
                "limit": r.get("limit"),
                "percent": r.get("percent"),
            }
            for r in body.get("pressure") or []
        ],
        "gateway": body.get("fleet"),
    }


async def list_models(
    client: httpx.AsyncClient,
    base_url: str,
    token: str,
    node: str | None = None,
    downloaded: bool | None = None,
) -> Any:
    """The model catalog — 25 KB raw, most of it file paths and hashes."""
    body = await _get(client, base_url, "/admin/api/models", token)
    out = []
    for m in body.get("models") or []:
        if node and m.get("node") != node:
            continue
        if downloaded is not None and bool(m.get("downloaded")) != downloaded:
            continue
        size = m.get("file_size_bytes")
        out.append(
            {
                "model": m.get("slug") or m.get("id"),
                "node": m.get("node"),
                "modality": m.get("modality"),
                "family": m.get("family") or None,
                "quantization": m.get("quantization") or None,
                "downloaded": m.get("downloaded"),
                "size_gb": round(size / 1e9, 1) if size else None,
                "instances": len(m.get("instance_ids") or []),
                "hf_repo": m.get("hf_repo"),
            }
        )
    return {"models": out, "unreachable_nodes": body.get("unreachable_nodes") or []}


async def check_limits(client: httpx.AsyncClient, base_url: str, token: str) -> Any:
    """Which ceiling is closest to refusing, and what each tier allows."""
    live = await _get(client, base_url, "/admin/api/limits/live", token)
    out: dict[str, Any] = {
        "live": live.get("available"),
        "why_not_live": live.get("reason"),
        # Already sorted closest-to-refusing first; unmetered counters last.
        "counters": [
            {
                **_named(r.get("name"), r.get("identity"), key="who"),
                "layer": r.get("layer"),
                "dimension": r.get("dimension"),
                "endpoint": r.get("endpoint"),
                "used": r.get("used"),
                "limit": r.get("limit"),
                "percent": r.get("percent"),
                "limit_from": r.get("limit_source"),
            }
            for r in (live.get("rows") or [])[:15]
        ],
        "minute_resets_in_s": live.get("minute_resets_in"),
    }
    tiers = await _get(client, base_url, "/admin/api/limits/tiers", token)
    out["tiers"] = [
        {k: v for k, v in t.items() if v is not None and k != "description"}
        for t in tiers.get("tiers") or []
    ]
    return out


async def get_spend(
    client: httpx.AsyncClient, base_url: str, token: str, period: str | None = None
) -> Any:
    """What the platform billed in a month, by client and by model."""
    body = await _get(client, base_url, "/admin/api/billing/overview", token, period=period)
    return {
        "period": body.get("period"),
        "total_usd": _usd(body.get("total_usd")),
        "subtotal_usd": _usd(body.get("subtotal_usd")),
        "tax_usd": _usd(body.get("tax_amount_usd")),
        "requests": body.get("request_count"),
        "tokens": body.get("total_tokens"),
        # Requests with no price configured bill as zero; a total that hides
        # them reads as cheaper than it was.
        "unpriced_requests": body.get("unpriced_requests"),
        "by_client": [
            {
                **_named(r.get("name"), r.get("client_id")),
                "total_usd": _usd(r.get("total_usd")),
                "requests": r.get("request_count"),
                "tokens": r.get("total_tokens"),
                "unpriced_requests": r.get("unpriced_requests"),
            }
            for r in body.get("by_client") or []
        ],
        "by_model": [
            {
                "model": r.get("model_id"),
                "cost_usd": _usd(r.get("cost_usd")),
                "requests": r.get("request_count"),
                "tokens": r.get("tokens"),
            }
            for r in body.get("by_model") or []
        ],
    }


async def list_prices(
    client: httpx.AsyncClient, base_url: str, token: str, model: str | None = None
) -> Any:
    """Configured price per model, and the default each modality falls back to."""
    body = await _get(client, base_url, "/admin/api/billing/pricing", token)
    prices = body.get("data") or {}
    if model:
        prices = {k: v for k, v in prices.items() if model.lower() in k.lower()}
    return {"prices": prices, "defaults_by_modality": body.get("defaults_by_modality")}


#: Enough to answer "who did that" without reading the whole trail into the
#: context; the filter is what narrows it, not a bigger number.
_AUDIT_MAX = 50


async def list_audit(
    client: httpx.AsyncClient,
    base_url: str,
    token: str,
    contains: str | None = None,
    limit: int = 20,
) -> Any:
    """Who changed what, newest first — PRM-157.

    Fetches a wider slice than it returns so a filter still finds an older
    event. Request and trace ids, IPs and user agents are dropped: they answer
    forensic questions, not "who restarted it".
    """
    limit = max(1, min(int(limit), _AUDIT_MAX))
    body = await _get(client, base_url, "/admin/api/audit", token, limit=200)
    out = []
    for e in body.get("events") or []:
        actor = e.get("actor") or {}
        row = {
            "at": e.get("at"),
            # A person signs in with an email; a credential has a name.
            **(
                {"who": actor["email"]}
                if actor.get("email")
                else _named(actor.get("name"), actor.get("client_id"), key="who")
            ),
            "action": e.get("action"),
            "target": e.get("target"),
            "outcome": e.get("outcome"),
            "status": e.get("status_code"),
        }
        if contains and contains.lower() not in json.dumps(row, default=str).lower():
            continue
        out.append(row)
        if len(out) >= limit:
            break
    return out


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
                "Who is calling the platform right now and today, by client name: requests, "
                "tokens, today's cost, which models, what they are doing and any refusals. "
                "Use for questions about current traffic or which client is causing "
                "something. For a month's bill use get_spend instead."
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
    {
        "type": "function",
        "function": {
            "name": "list_clients",
            "description": (
                "Every client the platform knows — API credentials and dashboard users — "
                "by name, with its id and whether it is active. `name` is what the "
                "dashboard shows; `client_name`, when present, is the client's own name — "
                "look a client up by either. Use for 'who are the users', 'what are the "
                "clients called', 'is client X active', 'what is X's id', or to turn an id "
                "into a name. It has no emails, scopes or roles."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_overview",
            "description": (
                "The dashboard's summary: what needs attention right now, today against "
                "yesterday (requests, tokens, cost, errors), the busiest clients and models, "
                "and who is closest to a rate limit. Use first for open questions like "
                "'is anything wrong?' or 'how is the platform doing?'."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_models",
            "description": (
                "The model catalog: every model each node knows, its modality, "
                "quantization, size, whether it is downloaded and how many instances it "
                "has. Use for 'which models do I have' or 'where is model X'. For what "
                "is running now use list_instances."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "node": {"type": "string", "description": "Optional node name filter."},
                    "downloaded": {
                        "type": "boolean",
                        "description": "Optional: true for downloaded only, false for not.",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_limits",
            "description": (
                "Live rate-limit counters, closest to refusing first, by client, plus what "
                "each tier allows. Use when asked why a client gets 429s, who is near a "
                "ceiling, or what a tier's limits are."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_spend",
            "description": (
                "What the platform billed for one month: total, tax, and the breakdown by "
                "client and by model, including requests with no price configured. Use for "
                "questions about cost, spend or invoices over a month."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "period": {
                        "type": "string",
                        "description": "Month as YYYY-MM. Omit for the current month.",
                    }
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_prices",
            "description": (
                "The configured price per model (per million prompt and completion "
                "tokens, per image) and the default each modality falls back to. Use for "
                "'how much does model X cost'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "model": {
                        "type": "string",
                        "description": "Optional: part of a model name to filter by.",
                    }
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_audit",
            "description": (
                "The audit trail, newest first: who did what, when, to what, and whether "
                "it worked. Use for 'who restarted X', 'what changed today', 'who created "
                "this client'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "contains": {
                        "type": "string",
                        "description": (
                            "Optional text to match in the actor, action or target, e.g. "
                            "a model id, 'stop', 'limits'."
                        ),
                    },
                    "limit": {"type": "integer", "description": "How many, at most 50."},
                },
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
    "list_clients": list_clients,
    "get_overview": get_overview,
    "list_models": list_models,
    "check_limits": check_limits,
    "get_spend": get_spend,
    "list_prices": list_prices,
    "list_audit": list_audit,
}
