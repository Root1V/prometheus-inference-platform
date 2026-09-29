"""Tests for RM-07 — fine-grained per-model authorization scopes.

Covers the gateway-side enforcement in router.py's chat_completions handler
and Claims.has_model_scope(). See docs/roadmap.md RM-07 and
docs/roadmap.md RM-07. Uses the multi_model_registry fixture from
conftest.py: llama3-8b-q4, small-model, inactive-model (no backend_url),
invalid-backend-model (non-loopback backend_url).
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
import respx
from httpx import ASGITransport, AsyncClient, Response

from prometheus_gateway.auth.claims import Claims
from tests.conftest import make_token

LLAMA_URL = "http://127.0.0.1:18080"  # llama3-8b-q4, per multi_model_registry

VALID_BODY = {
    "model": "llama3-8b-q4",
    "messages": [{"role": "user", "content": "hi"}],
    "stream": False,
    "max_tokens": 10,
}

LLAMA_RESPONSE = {
    "id": "t1",
    "object": "chat.completion",
    "model": "llama3-8b-q4",
    "choices": [
        {"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}
    ],
    "usage": {"prompt_tokens": 2, "completion_tokens": 2, "total_tokens": 4},
}


@pytest.fixture
async def gw(multi_model_app):
    async with AsyncClient(
        transport=ASGITransport(app=multi_model_app), base_url="http://test"
    ) as client:
        yield client


def _headers(rsa_keys, scope: str) -> dict[str, str]:
    token = make_token(rsa_keys["private"], scope=scope)
    return {"Authorization": f"Bearer {token}"}


# ── Claims.has_model_scope ───────────────────────────────────────────────────


def test_has_model_scope_true_when_granted():
    claims = Claims(
        user_id="u",
        client_id="c",
        scope="inference:read model:llama3-8b-q4",
        expires_at=datetime.now(tz=timezone.utc),
        issued_at=datetime.now(tz=timezone.utc),
        issuer="iss",
    )
    assert claims.has_model_scope("llama3-8b-q4") is True


def test_has_model_scope_false_when_not_granted():
    claims = Claims(
        user_id="u",
        client_id="c",
        scope="inference:read model:other-model",
        expires_at=datetime.now(tz=timezone.utc),
        issued_at=datetime.now(tz=timezone.utc),
        issuer="iss",
    )
    assert claims.has_model_scope("llama3-8b-q4") is False


def test_has_model_scope_false_when_no_model_scopes_at_all():
    """Deny-by-default: inference:read alone grants no model access."""
    claims = Claims(
        user_id="u",
        client_id="c",
        scope="inference:read",
        expires_at=datetime.now(tz=timezone.utc),
        issued_at=datetime.now(tz=timezone.utc),
        issuer="iss",
    )
    assert claims.has_model_scope("llama3-8b-q4") is False


# ── /v1/chat/completions enforcement ─────────────────────────────────────────


async def test_missing_inference_scope_returns_403(gw, rsa_keys):
    """A token with only a model:* grant but no inference:read is rejected."""
    headers = _headers(rsa_keys, "model:llama3-8b-q4")
    resp = await gw.post("/v1/chat/completions", json=VALID_BODY, headers=headers)
    assert resp.status_code == 403
    assert resp.json()["type"].endswith("forbidden")


async def test_inference_read_without_model_scope_returns_403(gw, rsa_keys):
    """RM-07 deny-by-default: inference:read alone is not enough."""
    headers = _headers(rsa_keys, "inference:read")
    resp = await gw.post("/v1/chat/completions", json=VALID_BODY, headers=headers)
    assert resp.status_code == 403
    assert "not authorized to use model" in resp.json()["detail"]


async def test_wrong_model_scope_returns_403(gw, rsa_keys):
    """Granted access to a different model does not authorize this one."""
    headers = _headers(rsa_keys, "inference:read model:small-model")
    resp = await gw.post("/v1/chat/completions", json=VALID_BODY, headers=headers)
    assert resp.status_code == 403


async def test_inference_read_plus_model_scope_succeeds(gw, rsa_keys):
    headers = _headers(rsa_keys, "inference:read model:llama3-8b-q4")
    with respx.mock:
        respx.post(f"{LLAMA_URL}/v1/chat/completions").mock(
            return_value=Response(200, json=LLAMA_RESPONSE)
        )
        resp = await gw.post("/v1/chat/completions", json=VALID_BODY, headers=headers)
    assert resp.status_code == 200


async def test_streaming_requires_inference_stream_not_inference_read(gw, rsa_keys):
    """A stream:true request needs inference:stream — inference:read is not enough."""
    headers = _headers(rsa_keys, "inference:read model:llama3-8b-q4")
    body = {**VALID_BODY, "stream": True}
    resp = await gw.post("/v1/chat/completions", json=body, headers=headers)
    assert resp.status_code == 403
    assert "inference:stream" in resp.json()["detail"]


# ── RM-14: admin:write bypass (model playground) ─────────────────────────────


async def test_admin_write_bypasses_inference_and_model_scope(gw, rsa_keys):
    """The dashboard's own admin:write session can invoke any model — no
    inference:read/model:<id> needed — used by the RM-14 playground so cost/
    usage still records against the operator's own real call to /v1/chat/completions."""
    headers = _headers(rsa_keys, "admin:read admin:write")
    with respx.mock:
        respx.post(f"{LLAMA_URL}/v1/chat/completions").mock(
            return_value=Response(200, json=LLAMA_RESPONSE)
        )
        resp = await gw.post("/v1/chat/completions", json=VALID_BODY, headers=headers)
    assert resp.status_code == 200


async def test_admin_read_alone_does_not_bypass(rsa_keys, gw):
    """Only admin:write bypasses — admin:read (list-only) does not."""
    headers = _headers(rsa_keys, "admin:read")
    resp = await gw.post("/v1/chat/completions", json=VALID_BODY, headers=headers)
    assert resp.status_code == 403


async def test_admin_write_bypasses_streaming_scope_too(gw, rsa_keys):
    """The bypass covers inference:stream as well as inference:read."""
    headers = _headers(rsa_keys, "admin:write")
    body = {**VALID_BODY, "stream": True}
    with respx.mock:
        respx.post(f"{LLAMA_URL}/v1/chat/completions").mock(
            return_value=Response(
                200,
                content=b'data: {"choices":[{"delta":{"content":"ok"}}]}\n\ndata: [DONE]\n\n',
                headers={"content-type": "text/event-stream"},
            )
        )
        resp = await gw.post("/v1/chat/completions", json=body, headers=headers)
    assert resp.status_code == 200


async def test_unknown_model_returns_400_before_403(gw, rsa_keys):
    """Model-existence (400) is checked before authorization (403) — the model
    catalog is public via GET /v1/models, so there's nothing to protect by
    reordering these checks, and a client shouldn't need model:<id> scope for
    a model that doesn't exist just to get a clear error."""
    headers = _headers(rsa_keys, "inference:read")  # no model:* grants at all
    body = {**VALID_BODY, "model": "does-not-exist-xyz"}
    resp = await gw.post("/v1/chat/completions", json=body, headers=headers)
    assert resp.status_code == 400
    assert resp.json()["type"].endswith("unknown-model")


# ── PRM-166: one catalog, and it is the caller's ─────────────────────────────


async def test_the_catalog_needs_a_token(gw):
    """It published the whole inventory — ids, family, quantization, context
    length, replica count — to anyone who could reach the port, while RM-07 made
    model access deny-by-default. Discovery allow-all beside inference
    deny-by-default is the same shape PRM-163 closed on `/metrics`."""
    resp = await gw.get("/v1/models")
    assert resp.status_code == 401
    assert resp.json()["type"].endswith("/missing-credentials")


async def test_the_catalog_answers_what_this_token_may_call(gw, rsa_keys):
    token = make_token(rsa_keys["private"], scope="inference:read model:small-model")
    resp = await gw.get("/v1/models", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200
    # The fixture serves two active models; the grant names one.
    assert [m["id"] for m in resp.json()["data"]] == ["small-model"]


async def test_a_token_with_no_grants_sees_an_empty_catalog(gw, rsa_keys):
    """And that is a true absence, not a failure — but the caller cannot tell it
    from "the platform has no models", which is why the handler's docstring says
    so and the guide says so. RM-98 in the other direction."""
    token = make_token(rsa_keys["private"], scope="inference:read")
    resp = await gw.get("/v1/models", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200
    assert resp.json()["data"] == []


async def test_admin_write_still_sees_every_model(gw, rsa_keys):
    """RM-14's carve-out: admin:write already implies full model management, so
    this is not a new privilege — and it is how the Playground lists."""
    token = make_token(rsa_keys["private"], scope="admin:write")
    resp = await gw.get("/v1/models", headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200
    assert len(resp.json()["data"]) >= 1


async def test_mine_is_an_alias_now(gw, rsa_keys):
    """Kept because it is in the guide and SDKs call it. One implementation, so
    the item shape cannot drift between the two — it was duplicated before."""
    token = make_token(rsa_keys["private"], scope="inference:read model:small-model")
    headers = {"Authorization": f"Bearer {token}"}

    public = await gw.get("/v1/models", headers=headers)
    mine = await gw.get("/v1/models/mine", headers=headers)

    assert public.status_code == mine.status_code == 200
    assert public.json() == mine.json()


async def test_the_catalog_is_not_exempt_from_authentication_any_more():
    """The exemption list is where this lived, and where it would come back."""
    from prometheus_gateway.auth.middleware import EXEMPT_PATHS

    assert "/v1/models" not in EXEMPT_PATHS
    # `/health` stays: it says only that the process answers, and A-29 asked us to
    # put that in writing rather than let it grow into a small `/metrics`.
    assert "/health" in EXEMPT_PATHS
