"""Tests for spec 001 — Gateway Core and spec 006 — Multi-Model Gateway.

Each test corresponds 1-to-1 with an Acceptance Criterion.
See memory/specs/001-gateway-core.md and memory/specs/006-multi-model-gateway.md.
"""

from __future__ import annotations

import json

import pytest
import respx
from httpx import ASGITransport, AsyncClient, Response

from tests.conftest import make_token


# ── Fixtures ───────────────────────────────────────────────────────────────


@pytest.fixture
def registry(multi_model_registry):
    """Use multi-model registry for all tests in this module."""
    return multi_model_registry


@pytest.fixture
def gateway_app(settings, registry):
    """Full gateway app wired with auth middleware + router."""
    from prometheus_gateway.main import create_app

    app = create_app(settings=settings, registry=registry)
    return app


@pytest.fixture
async def gw(gateway_app):
    """AsyncClient pointed at the gateway app."""
    async with AsyncClient(
        transport=ASGITransport(app=gateway_app), base_url="http://test"
    ) as client:
        yield client


@pytest.fixture
def auth_headers(rsa_keys):
    """Valid JWT bearer token headers — inference:read/stream + model:* (RM-07)
    for every model this test module's fixtures reference."""
    token = make_token(
        rsa_keys["private"],
        scope=(
            "inference:read inference:stream "
            "model:llama3-8b-q4 model:small-model model:inactive-model "
            "model:invalid-backend-model"
        ),
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def admin_headers(rsa_keys):
    """Admin JWT bearer token headers — admin:read scope."""
    token = make_token(rsa_keys["private"], scope="admin:read inference:read")
    return {"Authorization": f"Bearer {token}"}


# Backend URLs from multi_model_registry fixture
LLAMA_URL = "http://127.0.0.1:18080"  # llama3-8b-q4
SMALL_URL = "http://127.0.0.1:18081"  # small-model

VALID_BODY: dict = {
    "model": "llama3-8b-q4",
    "messages": [{"role": "user", "content": "Hello"}],
    "stream": False,
}

SMALL_BODY: dict = {
    "model": "small-model",
    "messages": [{"role": "user", "content": "Hi"}],
    "stream": False,
}

LLAMA_RESPONSE: dict = {
    "id": "chatcmpl-test",
    "object": "chat.completion",
    "model": "llama3-8b-q4",
    "choices": [
        {
            "index": 0,
            "message": {"role": "assistant", "content": "Hello! How can I help?"},
            "finish_reason": "stop",
        }
    ],
    "usage": {"prompt_tokens": 5, "completion_tokens": 9, "total_tokens": 14},
}


# ── AC-1: Non-streaming proxy ──────────────────────────────────────────────


@respx.mock
async def test_gateway_core_AC1_nonstreaming_proxy(gw, auth_headers):  # memory/specs/001
    """AC-1: Valid chat completions request is forwarded and response returned."""
    respx.post(f"{LLAMA_URL}/v1/chat/completions").mock(
        return_value=Response(200, json=LLAMA_RESPONSE)
    )

    resp = await gw.post("/v1/chat/completions", json=VALID_BODY, headers=auth_headers)

    assert resp.status_code == 200
    data = resp.json()
    assert data["object"] == "chat.completion"
    assert data["choices"][0]["message"]["content"] == "Hello! How can I help?"


# ── AC-2: SSE streaming proxy ──────────────────────────────────────────────


@respx.mock
async def test_gateway_core_AC2_streaming(gw, auth_headers):  # memory/specs/001
    """AC-2: stream=true returns text/event-stream with data: [DONE] at end."""
    sse_chunks = (
        b'data: {"choices": [{"delta": {"content": "Hello"}}]}\n\n'
        b'data: {"choices": [{"delta": {"content": "!"}}]}\n\n'
    )

    respx.post(f"{LLAMA_URL}/v1/chat/completions").mock(
        return_value=Response(
            200, content=sse_chunks, headers={"Content-Type": "text/event-stream"}
        )
    )

    stream_body = {**VALID_BODY, "stream": True}
    resp = await gw.post("/v1/chat/completions", json=stream_body, headers=auth_headers)

    assert resp.status_code == 200
    assert "text/event-stream" in resp.headers["content-type"]
    content = resp.text
    assert "data: [DONE]" in content


# ── AC-3: X-Request-ID header ─────────────────────────────────────────────


@respx.mock
async def test_gateway_core_AC3_request_id_header(gw, auth_headers):  # memory/specs/001
    """AC-3: Every response includes a unique X-Request-ID header."""
    respx.post(f"{LLAMA_URL}/v1/chat/completions").mock(
        return_value=Response(200, json=LLAMA_RESPONSE)
    )

    resp1 = await gw.post("/v1/chat/completions", json=VALID_BODY, headers=auth_headers)
    resp2 = await gw.post("/v1/chat/completions", json=VALID_BODY, headers=auth_headers)

    assert "x-request-id" in resp1.headers
    assert "x-request-id" in resp2.headers
    # Each request gets a unique ID
    assert resp1.headers["x-request-id"] != resp2.headers["x-request-id"]


# ── AC-4: /health endpoint ────────────────────────────────────────────────


async def test_gateway_core_AC4_health(gw):  # memory/specs/001
    """AC-4: GET /health returns {"status": "ok"} with HTTP 200, no auth required."""
    resp = await gw.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


# ── AC-5: Unknown model → 400 ─────────────────────────────────────────────


async def test_gateway_core_AC5_unknown_model(gw, auth_headers):  # memory/specs/001
    """AC-5: Unknown model returns HTTP 400 Problem Details response."""
    body = {**VALID_BODY, "model": "nonexistent-model-xyz"}
    resp = await gw.post("/v1/chat/completions", json=body, headers=auth_headers)

    assert resp.status_code == 400
    data = resp.json()
    assert data["status"] == 400
    assert "unknown-model" in data["type"]
    assert "nonexistent-model-xyz" in data["detail"]


# ── Client-supplied system messages are forwarded (RM-43) ─────────────────


@respx.mock
async def test_client_system_message_is_forwarded(gw, auth_headers):  # docs/roadmap.md RM-43
    """A caller with inference:read + model:<id> is already authorized to talk to this
    model — their own system prompt is legitimate input, same as OpenAI's API, not an
    injection to strip. (Previously stripped unconditionally per an old AC-6 rule; that
    broke any real use of a system prompt, including the RM-14 Playground's own field.)
    """
    captured_payload: dict = {}

    def capture(request):
        captured_payload.update(json.loads(request.content))
        return Response(200, json=LLAMA_RESPONSE)

    respx.post(f"{LLAMA_URL}/v1/chat/completions").mock(side_effect=capture)

    body = {
        "model": "llama3-8b-q4",
        "messages": [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "Hello"},
        ],
        "stream": False,
    }

    resp = await gw.post("/v1/chat/completions", json=body, headers=auth_headers)

    assert resp.status_code == 200
    forwarded_messages = captured_payload["messages"]
    roles = [m["role"] for m in forwarded_messages]
    assert roles == ["system", "user"]
    assert forwarded_messages[0]["content"] == "You are a helpful assistant."


# ── Native tool-calling (RM-35) ─────────────────────────────────────────────

WEATHER_TOOL: dict = {
    "type": "function",
    "function": {
        "name": "get_weather",
        "description": "Get the current weather for a city",
        "parameters": {
            "type": "object",
            "properties": {"city": {"type": "string"}},
            "required": ["city"],
        },
    },
}

TOOL_CALL_RESPONSE: dict = {
    "id": "chatcmpl-tool",
    "object": "chat.completion",
    "model": "llama3-8b-q4",
    "choices": [
        {
            "index": 0,
            "message": {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "get_weather", "arguments": '{"city": "Lima"}'},
                    }
                ],
            },
            "finish_reason": "tool_calls",
        }
    ],
    "usage": {"prompt_tokens": 20, "completion_tokens": 8, "total_tokens": 28},
}


@respx.mock
async def test_tools_field_is_forwarded_to_backend(gw, auth_headers):  # docs/roadmap.md RM-35
    captured_payload: dict = {}

    def capture(request):
        captured_payload.update(json.loads(request.content))
        return Response(200, json=LLAMA_RESPONSE)

    respx.post(f"{LLAMA_URL}/v1/chat/completions").mock(side_effect=capture)

    body = {
        **VALID_BODY,
        "tools": [WEATHER_TOOL],
        "tool_choice": "auto",
    }
    resp = await gw.post("/v1/chat/completions", json=body, headers=auth_headers)

    assert resp.status_code == 200
    assert captured_payload["tools"] == [WEATHER_TOOL]
    assert captured_payload["tool_choice"] == "auto"


@respx.mock
async def test_response_tool_calls_pass_through_untouched(gw, auth_headers):  # RM-35
    """The backend's tool_calls (and null content) reach the client byte-for-byte —
    the gateway doesn't reconstruct the response body field-by-field."""
    respx.post(f"{LLAMA_URL}/v1/chat/completions").mock(
        return_value=Response(200, json=TOOL_CALL_RESPONSE)
    )

    body = {**VALID_BODY, "tools": [WEATHER_TOOL]}
    resp = await gw.post("/v1/chat/completions", json=body, headers=auth_headers)

    assert resp.status_code == 200
    message = resp.json()["choices"][0]["message"]
    assert message["content"] is None
    assert message["tool_calls"][0]["function"]["name"] == "get_weather"
    assert message["tool_calls"][0]["function"]["arguments"] == '{"city": "Lima"}'


@respx.mock
async def test_tool_result_message_round_trips(gw, auth_headers):  # RM-35
    """A follow-up turn with the assistant's prior tool_calls plus a "tool" role
    message answering it — the multi-turn tool-calling conversation shape."""
    captured_payload: dict = {}

    def capture(request):
        captured_payload.update(json.loads(request.content))
        return Response(200, json=LLAMA_RESPONSE)

    respx.post(f"{LLAMA_URL}/v1/chat/completions").mock(side_effect=capture)

    body = {
        "model": "llama3-8b-q4",
        "messages": [
            {"role": "user", "content": "What's the weather in Lima?"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "get_weather", "arguments": '{"city": "Lima"}'},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "call_1", "content": "22C, sunny"},
        ],
        "stream": False,
    }
    resp = await gw.post("/v1/chat/completions", json=body, headers=auth_headers)

    assert resp.status_code == 200
    forwarded = captured_payload["messages"]
    assert forwarded[1]["tool_calls"][0]["id"] == "call_1"
    assert "content" not in forwarded[1] or forwarded[1].get("content") is None
    assert forwarded[2] == {"role": "tool", "tool_call_id": "call_1", "content": "22C, sunny"}


# ── AC-7: llama.cpp unreachable → 503 ────────────────────────────────────


@respx.mock
async def test_gateway_core_AC7_backend_unavailable(gw, auth_headers):  # memory/specs/001
    """AC-7: llama.cpp unreachable returns HTTP 503 Problem Details (not raw error)."""
    import httpx

    respx.post(f"{LLAMA_URL}/v1/chat/completions").mock(side_effect=httpx.ConnectError("refused"))

    resp = await gw.post("/v1/chat/completions", json=VALID_BODY, headers=auth_headers)

    assert resp.status_code == 503
    data = resp.json()
    assert data["status"] == 503
    assert "backend-unavailable" in data["type"]


# ── AC-8 (Q2): max_tokens > context_length → 400 ─────────────────────────


async def test_gateway_core_Q2_max_tokens_exceeds_context(gw, auth_headers):  # memory/specs/001
    """Q2: max_tokens exceeding model context_length returns HTTP 400."""
    body = {
        "model": "llama3-8b-q4",  # context_length: 8192
        "messages": [{"role": "user", "content": "Hello"}],
        "max_tokens": 99999,
    }
    resp = await gw.post("/v1/chat/completions", json=body, headers=auth_headers)

    assert resp.status_code == 400
    data = resp.json()
    assert "context-exceeded" in data["type"]
    assert "8192" in data["detail"]


# ==========================================================================
# Spec 006 — Multi-Model Gateway
# ==========================================================================


# ── AC-1: GET /v1/models returns only active models ───────────────────────


async def test_multi_model_gateway_AC1_active_models_only(gw):  # memory/specs/006
    """AC-1: GET /v1/models returns only models with backend_url set."""
    resp = await gw.get("/v1/models")
    assert resp.status_code == 200
    ids = [m["id"] for m in resp.json()["data"]]
    assert "llama3-8b-q4" in ids
    assert "small-model" in ids
    assert "inactive-model" not in ids
    assert "invalid-backend-model" not in ids


# ── RM-45: GET /v1/models/mine — caller's authorized subset only ──────────


async def test_rm45_models_mine_requires_auth(gw):
    """RM-45: unlike GET /v1/models, /v1/models/mine requires a Bearer token."""
    resp = await gw.get("/v1/models/mine")
    assert resp.status_code == 401


async def test_rm45_models_mine_filters_to_granted_scopes(gw, rsa_keys):
    """RM-45: only models the token's model:<id> scopes grant are returned."""
    token = make_token(
        rsa_keys["private"],
        scope="inference:read model:small-model",
    )
    resp = await gw.get("/v1/models/mine", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    ids = [m["id"] for m in resp.json()["data"]]
    assert ids == ["small-model"]


async def test_rm45_models_mine_empty_for_zero_grants(gw, rsa_keys):
    """RM-45: a token with no model:<id> scope at all sees an empty list, not an error."""
    token = make_token(rsa_keys["private"], scope="inference:read")
    resp = await gw.get("/v1/models/mine", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json()["data"] == []


async def test_rm45_models_mine_admin_write_sees_all(gw, rsa_keys):
    """RM-45: admin:write bypasses per-model grants, same carve-out as RM-14."""
    token = make_token(rsa_keys["private"], scope="admin:write")
    resp = await gw.get("/v1/models/mine", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    ids = {m["id"] for m in resp.json()["data"]}
    assert {"llama3-8b-q4", "small-model"} <= ids


# ── AC-2: Routes to correct backend for llama3-8b-q4 ─────────────────────


@respx.mock
async def test_multi_model_gateway_AC2_routes_to_large_backend(
    gw, auth_headers
):  # memory/specs/006
    """AC-2: llama3-8b-q4 request forwards to :18080 and NOT :18081."""
    route_large = respx.post(f"{LLAMA_URL}/v1/chat/completions").mock(
        return_value=Response(200, json=LLAMA_RESPONSE)
    )
    route_small = respx.post(f"{SMALL_URL}/v1/chat/completions").mock(
        return_value=Response(200, json=LLAMA_RESPONSE)
    )

    resp = await gw.post("/v1/chat/completions", json=VALID_BODY, headers=auth_headers)

    assert resp.status_code == 200
    assert route_large.called
    assert not route_small.called


# ── AC-3: Routes to correct backend for small-model ───────────────────────


@respx.mock
async def test_multi_model_gateway_AC3_routes_to_small_backend(
    gw, auth_headers
):  # memory/specs/006
    """AC-3: small-model request forwards to :18081 and returns backend response."""
    route_large = respx.post(f"{LLAMA_URL}/v1/chat/completions").mock(
        return_value=Response(200, json=LLAMA_RESPONSE)
    )
    route_small = respx.post(f"{SMALL_URL}/v1/chat/completions").mock(
        return_value=Response(200, json=LLAMA_RESPONSE)
    )

    resp = await gw.post("/v1/chat/completions", json=SMALL_BODY, headers=auth_headers)

    assert resp.status_code == 200
    assert route_small.called
    assert not route_large.called


# ── AC-4: Inactive model → 503 model-not-loaded ───────────────────────────


async def test_multi_model_gateway_AC4_inactive_model_503(gw, auth_headers):  # memory/specs/006
    """AC-4: Model with no backend_url returns 503 model-not-loaded."""
    body = {
        "model": "inactive-model",
        "messages": [{"role": "user", "content": "Hi"}],
        "stream": False,
    }
    resp = await gw.post("/v1/chat/completions", json=body, headers=auth_headers)

    assert resp.status_code == 503
    data = resp.json()
    assert "model-not-loaded" in data["type"]
    assert "inactive-model" in data["detail"]


# ── AC-5: Unknown model → 400 (preserved behaviour) ──────────────────────


async def test_multi_model_gateway_AC5_unknown_model_400(gw, auth_headers):  # memory/specs/006
    """AC-5: Model absent from registry returns 400 unknown-model."""
    body = {"model": "ghost-model-xyz", "messages": [{"role": "user", "content": "Hi"}]}
    resp = await gw.post("/v1/chat/completions", json=body, headers=auth_headers)

    assert resp.status_code == 400
    assert "unknown-model" in resp.json()["type"]


# ── AC-6: Active model, backend unreachable → 503 backend-unavailable ─────


@respx.mock
async def test_multi_model_gateway_AC6_backend_unreachable_503(
    gw, auth_headers
):  # memory/specs/006
    """AC-6: Active model with unreachable backend returns 503 backend-unavailable."""
    import httpx as _httpx

    respx.post(f"{LLAMA_URL}/v1/chat/completions").mock(
        side_effect=_httpx.ConnectError("connection refused")
    )

    resp = await gw.post("/v1/chat/completions", json=VALID_BODY, headers=auth_headers)

    assert resp.status_code == 503
    assert "backend-unavailable" in resp.json()["type"]


# ── AC-7: SSE streaming routed to correct backend ─────────────────────────


@respx.mock
async def test_multi_model_gateway_AC7_streaming_correct_backend(
    gw, auth_headers
):  # memory/specs/006
    """AC-7: stream=true request forwarded to correct backend, returns SSE."""
    sse = b'data: {"choices": [{"delta": {"content": "hi"}}]}\n\n'
    route_large = respx.post(f"{LLAMA_URL}/v1/chat/completions").mock(
        return_value=Response(200, content=sse, headers={"Content-Type": "text/event-stream"})
    )
    respx.post(f"{SMALL_URL}/v1/chat/completions").mock(
        return_value=Response(200, content=b"", headers={"Content-Type": "text/event-stream"})
    )

    resp = await gw.post(
        "/v1/chat/completions", json={**VALID_BODY, "stream": True}, headers=auth_headers
    )

    assert resp.status_code == 200
    assert "text/event-stream" in resp.headers["content-type"]
    assert "data: [DONE]" in resp.text
    assert route_large.called


# ── AC-8: Log contains model + backend_url fields ─────────────────────────


@respx.mock
async def test_multi_model_gateway_AC8_log_contains_model_and_backend(  # memory/specs/006
    gw, auth_headers, capsys
):
    """AC-8: Structured log entry contains model and backend_url."""
    import json

    respx.post(f"{LLAMA_URL}/v1/chat/completions").mock(
        return_value=Response(200, json=LLAMA_RESPONSE)
    )

    resp = await gw.post("/v1/chat/completions", json=VALID_BODY, headers=auth_headers)

    assert resp.status_code == 200
    # structlog writes JSON lines to stdout — parse and search
    captured = capsys.readouterr()
    log_events = []
    for line in captured.out.splitlines():
        try:
            log_events.append(json.loads(line))
        except (json.JSONDecodeError, ValueError):
            pass
    forwarding = [e for e in log_events if e.get("event") == "llama.forwarding"]
    assert forwarding, "Expected a 'llama.forwarding' log entry"
    record = forwarding[0]
    assert record.get("model") == "llama3-8b-q4"
    assert record.get("backend_url") == "http://127.0.0.1:18080"


# ── AC-9: Non-loopback backend_url rejected at load ───────────────────────


async def test_multi_model_gateway_AC9_invalid_backend_absent(gw):  # memory/specs/006
    """AC-9: Model with non-loopback backend_url absent from GET /v1/models."""
    ids = [m["id"] for m in (await gw.get("/v1/models")).json()["data"]]
    assert "invalid-backend-model" not in ids


# ── AC-10: LLAMA_CPP_URL triggers deprecation warning ────────────────────


def test_multi_model_gateway_AC10_llama_cpp_url_deprecated(  # memory/specs/006
    settings, multi_model_registry, monkeypatch, capsys
):
    """AC-10: Setting LLAMA_CPP_URL triggers a WARN-level deprecation log."""
    import prometheus_gateway.telemetry as tel

    tel._CONFIGURED = False  # reset idempotency guard so configure runs again

    from prometheus_gateway.main import create_app

    monkeypatch.setenv("LLAMA_CPP_URL", "http://127.0.0.1:8080")
    create_app(settings=settings, registry=multi_model_registry)

    captured = capsys.readouterr()
    assert "LLAMA_CPP_URL" in captured.out or "LLAMA_CPP_URL" in captured.err
    assert "deprecated" in captured.out.lower() or "deprecated" in captured.err.lower()


# ── AC-13: create_app() succeeds without llama_cpp_url ───────────────────


def test_multi_model_gateway_AC13_create_app_no_url_arg(  # memory/specs/006
    settings, multi_model_registry
):
    """AC-13: create_app() without llama_cpp_url arg starts without error."""
    from prometheus_gateway.main import create_app

    app = create_app(settings=settings, registry=multi_model_registry)
    assert app is not None


# ── AC-14: GET /v1/backends admin endpoint ────────────────────────────────


async def test_multi_model_gateway_AC14_backends_admin_success(
    gw, admin_headers
):  # memory/specs/006
    """AC-14: GET /v1/backends with admin:read returns all models + status."""
    resp = await gw.get("/v1/backends", headers=admin_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["object"] == "list"
    entries = {e["id"]: e for e in data["data"]}
    assert entries["llama3-8b-q4"]["status"] == "active"
    assert entries["small-model"]["status"] == "active"
    assert entries["inactive-model"]["status"] == "inactive"
    assert entries["inactive-model"]["backend_url"] is None
    assert entries["invalid-backend-model"]["status"] == "invalid"


async def test_multi_model_gateway_AC14_backends_forbidden_without_admin(  # memory/specs/006
    gw, auth_headers
):
    """AC-14: GET /v1/backends without admin:read returns 403."""
    resp = await gw.get("/v1/backends", headers=auth_headers)
    assert resp.status_code == 403


# ── AC-15: Shared connection pool ─────────────────────────────────────────


@respx.mock
async def test_multi_model_gateway_AC15_shared_pool(gateway_app, auth_headers):  # memory/specs/006
    """AC-15: Two requests to same model share exactly one AsyncClient in pool."""
    respx.post(f"{LLAMA_URL}/v1/chat/completions").mock(
        return_value=Response(200, json=LLAMA_RESPONSE)
    )

    async with AsyncClient(
        transport=ASGITransport(app=gateway_app), base_url="http://test"
    ) as client:
        await client.post("/v1/chat/completions", json=VALID_BODY, headers=auth_headers)
        await client.post("/v1/chat/completions", json=VALID_BODY, headers=auth_headers)

    pool = gateway_app.state.backend_pool
    assert len(pool._clients) == 1
    assert "http://127.0.0.1:18080" in pool._clients
