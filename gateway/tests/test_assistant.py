"""The dashboard assistant — PRM-272.

Implements: docs/roadmap.md — PRM-272.

Two properties carry this item, and both come from decisions the operator made
rather than from anything convenient:

1. **The assistant has its own principal.** Not the signed-in admin's, so that
   PRM-157's audit trail can say which of the two acted. The consequence is
   that it holds a `client_secret`, and a secret cannot be shipped to a page —
   which is why the loop is server-side at all, and why that is tested here
   rather than assumed.
2. **It can only read.** Acting lands separately, with the confirmation list;
   until then a tool surface that could write would be a capability nobody
   decided to grant.
"""

from __future__ import annotations

import json
import urllib.parse

import httpx

from prometheus_gateway.assistant import tools
from prometheus_gateway.assistant.loop import MAX_TOOL_ROUNDS, Assistant

# ── The credential is its own, and absent is said out loud ───────────────────


def test_an_assistant_without_a_credential_is_not_configured():
    """A feature that silently does nothing because one variable is unset is
    the thing `missing()` exists to prevent."""
    bare = Assistant(
        client_id=None, client_secret=None, auth_token_url=None, base_url="http://x", model="m"
    )
    assert bare.configured() is False
    assert bare.missing() == [
        "ASSISTANT_CLIENT_ID",
        "ASSISTANT_CLIENT_SECRET",
        "AUTH_SERVICE_TOKEN_URL",
    ]


def test_a_fully_configured_assistant_names_nothing_missing():
    ready = Assistant(
        client_id="id",
        client_secret="secret",
        auth_token_url="http://auth/token",
        base_url="http://gw",
        model="m",
    )
    assert ready.configured() is True
    assert ready.missing() == []


async def test_the_assistant_asks_for_read_scopes_only(respx_mock):
    """The grant a human admin issues says what it *may* do; this says what it
    *asks* for. Requesting admin:write here would be the assistant widening its
    own reach, which is the thing its separate principal exists to bound.

    The exact set, off the wire, not a substring of the source: the first
    version of this read `inspect.getsource` for `"admin:read" in source`, which
    passes just as happily when the scope is `admin:fleet:read` — and would have
    passed if PRM-274 had left the parent scope in place by mistake.
    """
    token = respx_mock.post("http://auth/token").mock(
        return_value=httpx.Response(200, json={"access_token": "t", "expires_in": 600})
    )
    respx_mock.post(f"{_GW}/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=_says("ok"))
    )
    assistant = Assistant(
        client_id="id",
        client_secret="secret",
        auth_token_url="http://auth/token",
        base_url=_GW,
        model="m",
    )
    await assistant.ask([{"role": "user", "content": "hi"}])

    form = urllib.parse.parse_qs(token.calls[0].request.content.decode())
    assert set(form["scope"][0].split()) == {
        "admin:fleet:read",
        "inference:read",
        "model:m",
    }


async def test_the_token_names_the_model_the_assistant_will_call(respx_mock):
    """This gateway authorises inference per model: `inference:read` alone is
    refused 403 by the assistant's own model. Measured against the running
    stack, after the first version of this shipped without it.

    Built from `assistant_model`, so pointing the assistant at a different
    model cannot leave the scope naming the old one.
    """
    token = respx_mock.post("http://auth/token").mock(
        return_value=httpx.Response(200, json={"access_token": "t", "expires_in": 600})
    )
    respx_mock.post(f"{_GW}/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=_says("ok"))
    )

    assistant = Assistant(
        client_id="id",
        client_secret="secret",
        auth_token_url="http://auth/token",
        base_url=_GW,
        model="some-other-model",
    )
    await assistant.ask([{"role": "user", "content": "hi"}])

    requested = token.calls[0].request.content.decode()
    assert "model%3Asome-other-model" in requested or "model:some-other-model" in requested


# ── It can only read ─────────────────────────────────────────────────────────


def test_every_tool_is_read_only():
    """The surface, asserted directly. A tool that writes is not a tool this
    item shipped — acting needs the confirmation list first."""
    assert set(tools.HANDLERS) == {
        "list_nodes",
        "list_instances",
        "list_activity",
        "tail_instance_log",
    }


def test_every_offered_tool_has_a_handler():
    """A definition the model can call and nothing implements answers "no such
    tool" — a dead option, which is the shape PRM-133's rule is about."""
    offered = {d["function"]["name"] for d in tools.DEFINITIONS}
    assert offered == set(tools.HANDLERS)


# ── The projections, which are what make it usable on this hardware ──────────


def test_a_node_summary_keeps_what_an_operator_would_read_out_loud():
    summary = tools._node_summary(
        {
            "name": "imac",
            "is_active": False,
            "enabled": True,
            "manager_url": "http://192.168.1.74:8090",
            "last_seen_at": "2026-10-24T07:00:00Z",
            "profile": {
                "cpu_model": "Intel(R) Core(TM) i5-5675R",
                "arch": "x86_64",
                "ram_total_mb": 8192,
                "accelerator": {"name": "Intel Iris Pro Graphics 6200"},
                "probe_errors": ["disk: boom"],
            },
            "engines_detected": [
                {"backend": "llama_cpp", "installed": True, "applicable": True},
                {
                    "backend": "mlx",
                    "installed": False,
                    "applicable": False,
                    "requires": "Apple Silicon",
                },
            ],
        }
    )
    assert summary["reachable"] is False
    assert summary["hardware"]["arch"] == "x86_64"
    assert summary["engines_installed"] == ["llama_cpp"]
    # PRM-271 survives the summary: the assistant must not tell an operator to
    # install MLX on an x86 Mac, and it can only avoid that if it is told why.
    assert summary["engines_ruled_out"] == {"mlx": "Apple Silicon"}
    # PRM-263 survives it too: a probe that failed is named, or "unknown" reads
    # as "nothing there".
    assert summary["hardware"]["probe_errors"] == ["disk: boom"]


def test_a_node_with_no_profile_does_not_invent_one():
    summary = tools._node_summary({"name": "new", "is_active": True, "enabled": True})
    assert "hardware" not in summary
    assert "engines_installed" not in summary


def test_the_instance_summary_drops_the_bulk_and_keeps_the_answer():
    """`/admin/api/instances` answers with paths, hashes and shard lists. At
    ~59 tok/s on this platform's own model, feeding that back is the difference
    between an answer in ten seconds and one in ninety."""
    summary = tools._instance_summary(
        {
            "id": "qwen3-embedding-0-6b-q8-0-local",
            "model_slug": "qwen3-embedding",
            "node": "local",
            "state": "ready",
            "backend": "llama_cpp",
            "port": 8086,
            "rss_mb": 4296.703125,
            "uptime_s": 1.177432,
            "path": "runtime/models/downloads/Qwen3-Embedding-0.6B-Q8_0.gguf",
            "hf_filenames": ["Qwen3-Embedding-0.6B-Q8_0.gguf"],
            "hf_sha256": "deadbeef" * 8,
            "error_message": None,
        }
    )
    assert summary["rss_mb"] == 4297
    assert summary["state"] == "ready"
    for noisy in ("path", "hf_filenames", "hf_sha256"):
        assert noisy not in summary


# ── The loop ─────────────────────────────────────────────────────────────────

_GW = "http://gw"


def _assistant() -> Assistant:
    return Assistant(
        client_id="id",
        client_secret="secret",
        auth_token_url="http://auth/token",
        base_url=_GW,
        model="m",
    )


def _token_route(respx_mock) -> None:
    respx_mock.post("http://auth/token").mock(
        return_value=httpx.Response(200, json={"access_token": "t", "expires_in": 600})
    )


def _says(text: str) -> dict:
    return {"choices": [{"message": {"role": "assistant", "content": text}}]}


def _calls(name: str, arguments: str = "{}") -> dict:
    return {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "c1",
                            "type": "function",
                            "function": {"name": name, "arguments": arguments},
                        }
                    ],
                }
            }
        ]
    }


async def test_a_tool_result_goes_back_to_the_model(respx_mock):
    """The loop's whole job. The second completion must be able to see what
    the first call returned, or the tool was run for nothing."""
    _token_route(respx_mock)
    respx_mock.get(f"{_GW}/admin/api/nodes").mock(
        return_value=httpx.Response(
            200, json=[{"name": "imac", "is_active": False, "enabled": True}]
        )
    )
    chat = respx_mock.post(f"{_GW}/v1/chat/completions").mock(
        side_effect=[
            httpx.Response(200, json=_calls("list_nodes")),
            httpx.Response(200, json=_says("imac is not answering.")),
        ]
    )

    reply = await _assistant().ask([{"role": "user", "content": "which nodes are down?"}])

    assert reply.content == "imac is not answering."
    assert reply.tool_calls_made == ["list_nodes"]
    sent = json.loads(chat.calls[1].request.content)
    tool_msg = [m for m in sent["messages"] if m.get("role") == "tool"]
    assert len(tool_msg) == 1
    assert "imac" in tool_msg[0]["content"]


async def test_a_failing_tool_is_reported_to_the_model_not_raised(respx_mock):
    """The model asking for a log that does not exist is an ordinary turn.
    Failing the request would tell the operator nothing and lose the answer."""
    _token_route(respx_mock)
    respx_mock.get(f"{_GW}/admin/api/nodes").mock(return_value=httpx.Response(500, text="boom"))
    chat = respx_mock.post(f"{_GW}/v1/chat/completions").mock(
        side_effect=[
            httpx.Response(200, json=_calls("list_nodes")),
            httpx.Response(200, json=_says("I could not read the node list.")),
        ]
    )

    reply = await _assistant().ask([{"role": "user", "content": "nodes?"}])

    assert reply.content == "I could not read the node list."
    tool_msg = [
        m for m in json.loads(chat.calls[1].request.content)["messages"] if m.get("role") == "tool"
    ]
    assert "500" in tool_msg[0]["content"]


async def test_an_unknown_tool_name_is_answered_not_crashed(respx_mock):
    _token_route(respx_mock)
    respx_mock.post(f"{_GW}/v1/chat/completions").mock(
        side_effect=[
            httpx.Response(200, json=_calls("drop_everything")),
            httpx.Response(200, json=_says("There is no such tool.")),
        ]
    )
    reply = await _assistant().ask([{"role": "user", "content": "do it"}])
    assert reply.content == "There is no such tool."


def test_the_round_cap_is_small_enough_to_be_a_bound():
    """The cap has to be *small*, not merely finite.

    The termination test below reads `MAX_TOOL_ROUNDS` to build its
    expectation, so it moves with the constant and passes just as happily at
    400 — verified by mutating it. That makes it a test of termination and not
    of the bound, so the bound gets its own assertion.

    Each round is a full completion on this platform's own model, measured at
    ~59 tok/s output at concurrency 1. The number is what an operator waits,
    and it is exactly the constant someone raises to "fix" a model that keeps
    failing to answer — at a cost that is invisible at the point of the edit.
    """
    assert MAX_TOOL_ROUNDS <= 6


async def test_the_loop_cannot_run_forever(respx_mock):
    """A loop that can spin will, on the day a tool returns something the model
    keeps re-reading. This proves it terminates; the test above bounds where."""
    _token_route(respx_mock)
    respx_mock.get(f"{_GW}/admin/api/nodes").mock(return_value=httpx.Response(200, json=[]))
    chat = respx_mock.post(f"{_GW}/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=_calls("list_nodes"))
    )

    reply = await _assistant().ask([{"role": "user", "content": "loop"}])

    assert reply.rounds == MAX_TOOL_ROUNDS + 1
    assert len(chat.calls) == MAX_TOOL_ROUNDS + 1
    assert "ran out of steps" in reply.content


async def test_the_last_round_is_asked_without_tools(respx_mock):
    """Or the model can spend the cap and leave the operator with nothing."""
    _token_route(respx_mock)
    respx_mock.get(f"{_GW}/admin/api/nodes").mock(return_value=httpx.Response(200, json=[]))
    chat = respx_mock.post(f"{_GW}/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=_calls("list_nodes"))
    )

    await _assistant().ask([{"role": "user", "content": "loop"}])

    assert "tools" in json.loads(chat.calls[0].request.content)
    assert "tools" not in json.loads(chat.calls[-1].request.content)


async def test_the_assistant_authenticates_as_itself(respx_mock):
    """Not as the operator asking. This is the traceability decision, on the
    wire: every tool call carries the assistant's own bearer token, so the
    audit trail records the assistant as the actor."""
    _token_route(respx_mock)
    nodes = respx_mock.get(f"{_GW}/admin/api/nodes").mock(return_value=httpx.Response(200, json=[]))
    respx_mock.post(f"{_GW}/v1/chat/completions").mock(
        side_effect=[
            httpx.Response(200, json=_calls("list_nodes")),
            httpx.Response(200, json=_says("none")),
        ]
    )

    await _assistant().ask([{"role": "user", "content": "nodes?"}])

    assert nodes.calls[0].request.headers["authorization"] == "Bearer t"
