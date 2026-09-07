"""Tests for RM-09 — VLM (vision content parts) + /v1/embeddings.

Uses the modality_app/modality_registry fixtures from conftest.py:
llama3-8b-q4 (text), vlm-model (vision), embed-model (embedding).
See docs/roadmap.md RM-09 and memory/wiki/model-registry.md.
"""

from __future__ import annotations

import pytest
import respx
from httpx import ASGITransport, AsyncClient, Response

from tests.conftest import make_token

TEXT_URL = "http://127.0.0.1:18080"
VLM_URL = "http://127.0.0.1:18082"
EMBED_URL = "http://127.0.0.1:18083"
IMAGE_URL = "http://127.0.0.1:18084"

TINY_PNG_DATA_URI = (
    "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBA"
    "scY42YAAAAASUVORK5CYII="
)


@pytest.fixture
async def gw(modality_app):
    async with AsyncClient(
        transport=ASGITransport(app=modality_app), base_url="http://test"
    ) as client:
        yield client


def _headers(rsa_keys, scope: str) -> dict[str, str]:
    token = make_token(rsa_keys["private"], scope=scope)
    return {"Authorization": f"Bearer {token}"}


# ── Schema validation: image_url must be a data: URI ─────────────────────────


def test_image_content_part_rejects_http_url():
    from pydantic import ValidationError

    from prometheus_gateway.models.schemas import ChatCompletionRequest

    with pytest.raises(ValidationError, match="data: URI"):
        ChatCompletionRequest(
            model="vlm-model",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": "http://evil.example.com/x.png"}}
                    ],
                }
            ],
        )


def test_image_content_part_accepts_data_uri():
    from prometheus_gateway.models.schemas import ChatCompletionRequest

    req = ChatCompletionRequest(
        model="vlm-model",
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "what is this?"},
                    {"type": "image_url", "image_url": {"url": TINY_PNG_DATA_URI}},
                ],
            }
        ],
    )
    assert req.messages[0].content[1].image_url.url == TINY_PNG_DATA_URI


# ── /v1/chat/completions modality enforcement ────────────────────────────────


async def test_image_content_on_text_model_returns_400(gw, rsa_keys):
    headers = _headers(rsa_keys, "inference:read model:llama3-8b-q4")
    body = {
        "model": "llama3-8b-q4",
        "messages": [
            {
                "role": "user",
                "content": [{"type": "image_url", "image_url": {"url": TINY_PNG_DATA_URI}}],
            }
        ],
    }
    resp = await gw.post("/v1/chat/completions", json=body, headers=headers)
    assert resp.status_code == 400
    assert resp.json()["type"].endswith("modality-mismatch")


async def test_image_content_on_vision_model_forwards_ok(gw, rsa_keys):
    headers = _headers(rsa_keys, "inference:read model:vlm-model")
    body = {
        "model": "vlm-model",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "describe this"},
                    {"type": "image_url", "image_url": {"url": TINY_PNG_DATA_URI}},
                ],
            }
        ],
    }
    vlm_response = {
        "id": "t1",
        "object": "chat.completion",
        "model": "vlm-model",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "a red pixel"},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
    }
    with respx.mock:
        route = respx.post(f"{VLM_URL}/v1/chat/completions").mock(
            return_value=Response(200, json=vlm_response)
        )
        resp = await gw.post("/v1/chat/completions", json=body, headers=headers)
    assert resp.status_code == 200
    # image content part must reach the backend unchanged
    forwarded = route.calls[0].request.content
    assert b"image_url" in forwarded


async def test_text_only_message_on_vision_model_still_works(gw, rsa_keys):
    """A vision-capable model must still accept plain text-only chat."""
    headers = _headers(rsa_keys, "inference:read model:vlm-model")
    body = {"model": "vlm-model", "messages": [{"role": "user", "content": "hello"}]}
    vlm_response = {
        "id": "t1",
        "object": "chat.completion",
        "model": "vlm-model",
        "choices": [
            {"index": 0, "message": {"role": "assistant", "content": "hi"}, "finish_reason": "stop"}
        ],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }
    with respx.mock:
        respx.post(f"{VLM_URL}/v1/chat/completions").mock(
            return_value=Response(200, json=vlm_response)
        )
        resp = await gw.post("/v1/chat/completions", json=body, headers=headers)
    assert resp.status_code == 200


# ── /v1/models exposes modality ──────────────────────────────────────────────


async def test_list_models_includes_modality(gw):
    resp = await gw.get("/v1/models")
    assert resp.status_code == 200
    by_id = {m["id"]: m for m in resp.json()["data"]}
    assert by_id["llama3-8b-q4"]["modality"] == "text"
    assert by_id["vlm-model"]["modality"] == "vision"
    assert by_id["embed-model"]["modality"] == "embedding"


# ── /v1/embeddings ────────────────────────────────────────────────────────────


async def test_embeddings_unknown_model_returns_400(gw, rsa_keys):
    headers = _headers(rsa_keys, "inference:read model:embed-model")
    resp = await gw.post(
        "/v1/embeddings", json={"model": "no-such-model", "input": "hi"}, headers=headers
    )
    assert resp.status_code == 400
    assert resp.json()["type"].endswith("unknown-model")


async def test_embeddings_on_text_model_returns_modality_mismatch(gw, rsa_keys):
    headers = _headers(rsa_keys, "inference:read model:llama3-8b-q4")
    resp = await gw.post(
        "/v1/embeddings", json={"model": "llama3-8b-q4", "input": "hi"}, headers=headers
    )
    assert resp.status_code == 400
    assert resp.json()["type"].endswith("modality-mismatch")


async def test_embeddings_without_inference_scope_returns_403(gw, rsa_keys):
    headers = _headers(rsa_keys, "model:embed-model")
    resp = await gw.post(
        "/v1/embeddings", json={"model": "embed-model", "input": "hi"}, headers=headers
    )
    assert resp.status_code == 403


async def test_embeddings_without_model_scope_returns_403(gw, rsa_keys):
    """RM-07 deny-by-default applies to embeddings too."""
    headers = _headers(rsa_keys, "inference:read")
    resp = await gw.post(
        "/v1/embeddings", json={"model": "embed-model", "input": "hi"}, headers=headers
    )
    assert resp.status_code == 403
    assert "not authorized to use model" in resp.json()["detail"]


async def test_embeddings_success_forwards_and_returns_backend_response(gw, rsa_keys):
    headers = _headers(rsa_keys, "inference:read model:embed-model")
    backend_response = {
        "object": "list",
        "data": [{"object": "embedding", "index": 0, "embedding": [0.1, 0.2, 0.3]}],
        "model": "embed-model",
        "usage": {"prompt_tokens": 2, "total_tokens": 2},
    }
    with respx.mock:
        route = respx.post(f"{EMBED_URL}/v1/embeddings").mock(
            return_value=Response(200, json=backend_response)
        )
        resp = await gw.post(
            "/v1/embeddings", json={"model": "embed-model", "input": "hello world"}, headers=headers
        )
    assert resp.status_code == 200
    assert resp.json() == backend_response
    assert route.calls[0].request.method == "POST"


async def test_embeddings_accepts_list_input(gw, rsa_keys):
    headers = _headers(rsa_keys, "inference:read model:embed-model")
    backend_response = {"object": "list", "data": [], "model": "embed-model", "usage": {}}
    with respx.mock:
        respx.post(f"{EMBED_URL}/v1/embeddings").mock(
            return_value=Response(200, json=backend_response)
        )
        resp = await gw.post(
            "/v1/embeddings",
            json={"model": "embed-model", "input": ["a", "b"]},
            headers=headers,
        )
    assert resp.status_code == 200


async def test_embeddings_admin_write_bypasses_scope_checks(gw, rsa_keys):
    """RM-37: the Playground's admin:write session has no inference:read/
    model:<id> grants of its own — same bypass as RM-14's chat completions."""
    headers = _headers(rsa_keys, "admin:write")
    backend_response = {
        "object": "list",
        "data": [{"object": "embedding", "index": 0, "embedding": [0.1, 0.2, 0.3]}],
        "model": "embed-model",
        "usage": {"prompt_tokens": 2, "total_tokens": 2},
    }
    with respx.mock:
        respx.post(f"{EMBED_URL}/v1/embeddings").mock(
            return_value=Response(200, json=backend_response)
        )
        resp = await gw.post(
            "/v1/embeddings", json={"model": "embed-model", "input": "hello"}, headers=headers
        )
    assert resp.status_code == 200


async def test_embeddings_backend_unreachable_returns_503(gw, rsa_keys):
    import httpx

    headers = _headers(rsa_keys, "inference:read model:embed-model")
    with respx.mock:
        respx.post(f"{EMBED_URL}/v1/embeddings").mock(side_effect=httpx.ConnectError("refused"))
        resp = await gw.post(
            "/v1/embeddings", json={"model": "embed-model", "input": "hi"}, headers=headers
        )
    assert resp.status_code == 503
    assert resp.json()["type"].endswith("backend-unavailable")


# ── /v1/images/generations ───────────────────────────────────────────────────


async def test_images_unknown_model_returns_400(gw, rsa_keys):
    headers = _headers(rsa_keys, "inference:read model:image-model")
    resp = await gw.post(
        "/v1/images/generations",
        json={"model": "no-such-model", "prompt": "a fox in snow"},
        headers=headers,
    )
    assert resp.status_code == 400
    assert resp.json()["type"].endswith("unknown-model")


async def test_images_on_text_model_returns_modality_mismatch(gw, rsa_keys):
    headers = _headers(rsa_keys, "inference:read model:llama3-8b-q4")
    resp = await gw.post(
        "/v1/images/generations",
        json={"model": "llama3-8b-q4", "prompt": "a fox in snow"},
        headers=headers,
    )
    assert resp.status_code == 400
    assert resp.json()["type"].endswith("modality-mismatch")


async def test_images_without_inference_scope_returns_403(gw, rsa_keys):
    headers = _headers(rsa_keys, "model:image-model")
    resp = await gw.post(
        "/v1/images/generations",
        json={"model": "image-model", "prompt": "a fox in snow"},
        headers=headers,
    )
    assert resp.status_code == 403


async def test_images_without_model_scope_returns_403(gw, rsa_keys):
    """RM-07 deny-by-default applies to images too."""
    headers = _headers(rsa_keys, "inference:read")
    resp = await gw.post(
        "/v1/images/generations",
        json={"model": "image-model", "prompt": "a fox in snow"},
        headers=headers,
    )
    assert resp.status_code == 403
    assert "not authorized to use model" in resp.json()["detail"]


async def test_images_success_forwards_and_returns_backend_response(gw, rsa_keys):
    headers = _headers(rsa_keys, "inference:read model:image-model")
    backend_response = {
        "created": 1700000000,
        "data": [{"b64_json": "aGVsbG8="}],
        "output_format": "png",
    }
    with respx.mock:
        route = respx.post(f"{IMAGE_URL}/v1/images/generations").mock(
            return_value=Response(200, json=backend_response)
        )
        resp = await gw.post(
            "/v1/images/generations",
            json={"model": "image-model", "prompt": "a fox in snow"},
            headers=headers,
        )
    assert resp.status_code == 200
    assert resp.json() == backend_response
    assert route.calls[0].request.method == "POST"


async def test_images_admin_write_bypasses_scope_checks(gw, rsa_keys):
    """Same admin:write carve-out as chat/embeddings — the Playground's Images
    tab runs under the admin dashboard's session."""
    headers = _headers(rsa_keys, "admin:write")
    backend_response = {"created": 1700000000, "data": [{"b64_json": "aGVsbG8="}]}
    with respx.mock:
        respx.post(f"{IMAGE_URL}/v1/images/generations").mock(
            return_value=Response(200, json=backend_response)
        )
        resp = await gw.post(
            "/v1/images/generations",
            json={"model": "image-model", "prompt": "a fox in snow"},
            headers=headers,
        )
    assert resp.status_code == 200


async def test_images_backend_unreachable_returns_503(gw, rsa_keys):
    import httpx

    headers = _headers(rsa_keys, "inference:read model:image-model")
    with respx.mock:
        respx.post(f"{IMAGE_URL}/v1/images/generations").mock(
            side_effect=httpx.ConnectError("refused")
        )
        resp = await gw.post(
            "/v1/images/generations",
            json={"model": "image-model", "prompt": "a fox in snow"},
            headers=headers,
        )
    assert resp.status_code == 503
    assert resp.json()["type"].endswith("backend-unavailable")
