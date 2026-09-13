"""RM-57 — logical (catalog) model names as routable groups of instances.

The gateway used to treat one name = one instance. manager-api already ships a
catalog `model_id` per instance (RM-51), so replicas are discoverable; these
tests cover turning that into routing.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest
import respx
from httpx import ASGITransport, AsyncClient, Response

from prometheus_gateway.models.registry import ModelEntry, ModelRegistry
from tests.conftest import make_token


def _registry(*entries: ModelEntry) -> ModelRegistry:
    registry = ModelRegistry.__new__(ModelRegistry)
    registry._models = {e.id: e for e in entries}
    return registry


def _entry(
    id: str,
    *,
    model_id: str = "",
    model_slug: str = "",
    node: str = "local",
    modality: str = "text",
    context_length: int = 4096,
    backend_url: str | None = "http://127.0.0.1:18080",
) -> ModelEntry:
    return ModelEntry(
        id=id,
        path=f"/models/{id}.gguf",
        context_length=context_length,
        family="test",
        quantization="Q4_0",
        backend_url=backend_url,
        backend_status="active" if backend_url else "inactive",
        node=node,
        modality=modality,
        model_id=model_id or id,
        model_slug=model_slug or model_id or id,
    )


# ── Single instance: behaviour must be identical to before RM-57 ────────────


def test_single_instance_resolves_to_itself():
    registry = _registry(_entry("solo-model"))

    resolved = registry.resolve("solo-model")

    assert resolved is not None
    assert [m.id for m in resolved.members] == ["solo-model"]
    assert resolved.modality == "text"
    assert resolved.context_length == 4096
    assert resolved.mismatch is None


def test_unknown_name_resolves_to_none():
    registry = _registry(_entry("solo-model"))
    assert registry.resolve("nope") is None


def test_stopped_instance_resolves_with_no_members():
    """Known but not running. It must stay distinguishable from an unknown
    name so the router can keep answering 503 model-not-loaded instead of
    demoting it to 400 unknown-model.
    """
    registry = _registry(_entry("stopped-model", backend_url=None))

    resolved = registry.resolve("stopped-model")

    assert resolved is not None
    assert resolved.members == ()


# ── Replicas under one catalog name ─────────────────────────────────────────


def test_catalog_name_groups_every_replica():
    registry = _registry(
        _entry("llama-a", model_id="llama", node="mac"),
        _entry("llama-b", model_id="llama", node="nvidia"),
    )

    resolved = registry.resolve("llama")

    assert resolved is not None
    assert [m.id for m in resolved.members] == ["llama-a", "llama-b"]


def test_an_instance_is_still_addressable_by_its_own_id():
    """Addressing one replica directly stays supported — that's today's only
    way to reach a specific instance, and RM-58's balancing shouldn't take it
    away."""
    registry = _registry(
        _entry("llama-a", model_id="llama"),
        _entry("llama-b", model_id="llama"),
    )

    resolved = registry.resolve("llama-b")

    assert resolved is not None
    assert [m.id for m in resolved.members] == ["llama-b"]


def test_group_wins_when_an_instance_is_named_after_its_catalog_entry():
    """The case that makes group-first non-negotiable: manager-api sets
    model_id = id on direct registration, so the first instance is usually
    named after the catalog entry. Matching the instance id first would send
    every request to it and silently ignore replicas added later.
    """
    registry = _registry(
        _entry("gpt-oss", model_id="gpt-oss"),  # id == catalog name
        _entry("gpt-oss-2", model_id="gpt-oss"),  # replica added later
    )

    resolved = registry.resolve("gpt-oss")

    assert resolved is not None
    assert [m.id for m in resolved.members] == ["gpt-oss", "gpt-oss-2"]


def test_only_active_replicas_are_included():
    registry = _registry(
        _entry("llama-a", model_id="llama"),
        _entry("llama-b", model_id="llama", backend_url=None),
    )

    resolved = registry.resolve("llama")

    assert resolved is not None
    assert [m.id for m in resolved.members] == ["llama-a"]


def test_member_order_is_deterministic():
    forward = _registry(
        _entry("b-inst", model_id="llama", node="mac"),
        _entry("a-inst", model_id="llama", node="mac"),
    )
    backward = _registry(
        _entry("a-inst", model_id="llama", node="mac"),
        _entry("b-inst", model_id="llama", node="mac"),
    )

    assert [m.id for m in forward.resolve("llama").members] == ["a-inst", "b-inst"]
    assert [m.id for m in backward.resolve("llama").members] == ["a-inst", "b-inst"]


# ── Heterogeneous groups ────────────────────────────────────────────────────


def test_context_length_is_the_smallest_in_the_group():
    """Validation happens before a replica is picked, so the limit has to hold
    for whichever one ends up serving."""
    registry = _registry(
        _entry("llama-a", model_id="llama", context_length=8192),
        _entry("llama-b", model_id="llama", context_length=4096),
    )

    assert registry.resolve("llama").context_length == 4096


def test_modality_disagreement_makes_the_group_unroutable():
    registry = _registry(
        _entry("llama-a", model_id="llama", modality="text"),
        _entry("llama-b", model_id="llama", modality="embedding"),
    )

    resolved = registry.resolve("llama")

    assert resolved is not None
    assert resolved.mismatch is not None
    assert "llama-a='text'" in resolved.mismatch
    assert "llama-b='embedding'" in resolved.mismatch


def test_matching_modalities_do_not_trip_the_mismatch_check():
    registry = _registry(
        _entry("llama-a", model_id="llama", modality="vision"),
        _entry("llama-b", model_id="llama", modality="vision"),
    )

    resolved = registry.resolve("llama")
    assert resolved.mismatch is None
    assert resolved.modality == "vision"


# ── Discovery listing ───────────────────────────────────────────────────────


def test_served_names_list_the_group_once_not_its_instances():
    """RM-57 listed replicas alongside the group; RM-70 stopped, because a
    client reading this to build a model picker would see three entries for
    one model. Each replica still resolves — it just isn't advertised."""
    registry = _registry(
        _entry("llama-a", model_id="llama"),
        _entry("llama-b", model_id="llama"),
    )

    names = {r.name for r in registry.list_served_names()}

    assert names == {"llama"}
    assert registry.resolve("llama-b") is not None


def test_served_names_do_not_duplicate_a_single_instance():
    """model_id == id for an ordinary model, so it must appear exactly once."""
    registry = _registry(_entry("solo-model"))

    names = [r.name for r in registry.list_served_names()]

    assert names == ["solo-model"]


# ── End-to-end routing through the group ────────────────────────────────────

REPLICA_A_URL = "http://127.0.0.1:18080"
REPLICA_B_URL = "http://127.0.0.1:18081"

CHAT_RESPONSE = {
    "id": "t1",
    "object": "chat.completion",
    "model": "llama",
    "choices": [
        {"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}
    ],
    "usage": {"prompt_tokens": 2, "completion_tokens": 2, "total_tokens": 4},
}


@pytest.fixture
def replica_app(settings):
    """Two instances of one catalog model, on different nodes."""
    from prometheus_gateway.main import create_app

    registry = ModelRegistry.__new__(ModelRegistry)
    registry._models = {
        "llama-a": ModelEntry(
            id="llama-a",
            path="/m/a.gguf",
            context_length=4096,
            family="llama",
            quantization="Q4_0",
            backend_url=REPLICA_A_URL,
            backend_status="active",
            node="mac",
            modality="text",
            model_id="llama",
        ),
        "llama-b": ModelEntry(
            id="llama-b",
            path="/m/b.gguf",
            context_length=8192,
            family="llama",
            quantization="Q4_0",
            backend_url=REPLICA_B_URL,
            backend_status="active",
            node="nvidia",
            modality="text",
            model_id="llama",
        ),
    }
    return create_app(settings=settings, registry=registry)


@pytest.fixture
async def gw(replica_app):
    async with AsyncClient(
        transport=ASGITransport(app=replica_app), base_url="http://test"
    ) as client:
        yield client


def _headers(rsa_keys, scope: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {make_token(rsa_keys['private'], scope=scope)}"}


@respx.mock
async def test_request_to_the_catalog_name_reaches_a_replica(gw, rsa_keys):
    route = respx.post(f"{REPLICA_A_URL}/v1/chat/completions").mock(
        return_value=Response(200, json=CHAT_RESPONSE)
    )

    resp = await gw.post(
        "/v1/chat/completions",
        json={"model": "llama", "messages": [{"role": "user", "content": "hi"}], "max_tokens": 5},
        headers=_headers(rsa_keys, "inference:read model:llama"),
    )

    assert resp.status_code == 200
    assert route.called


@respx.mock
async def test_an_individual_replica_is_still_addressable(gw, rsa_keys):
    route = respx.post(f"{REPLICA_B_URL}/v1/chat/completions").mock(
        return_value=Response(200, json=CHAT_RESPONSE)
    )

    resp = await gw.post(
        "/v1/chat/completions",
        json={"model": "llama-b", "messages": [{"role": "user", "content": "hi"}], "max_tokens": 5},
        headers=_headers(rsa_keys, "inference:read model:llama-b"),
    )

    assert resp.status_code == 200
    assert route.called


async def test_the_catalog_name_needs_its_own_scope(gw, rsa_keys):
    """Deny-by-default still applies: a grant on one replica doesn't imply
    the logical name, which reaches every replica."""
    resp = await gw.post(
        "/v1/chat/completions",
        json={"model": "llama", "messages": [{"role": "user", "content": "hi"}]},
        headers=_headers(rsa_keys, "inference:read model:llama-a"),
    )

    assert resp.status_code == 403


async def test_context_limit_is_the_smallest_replica(gw, rsa_keys):
    """llama-b allows 8192, llama-a only 4096 — asking for 6000 has to fail,
    since validation happens before a replica is chosen."""
    resp = await gw.post(
        "/v1/chat/completions",
        json={
            "model": "llama",
            "messages": [{"role": "user", "content": "hi"}],
            "max_tokens": 6000,
        },
        headers=_headers(rsa_keys, "inference:read model:llama"),
    )

    assert resp.status_code == 400
    assert "context-exceeded" in resp.json()["type"]


async def test_models_list_shows_one_entry_per_model(gw):
    resp = await gw.get("/v1/models")

    data = {m["id"]: m for m in resp.json()["data"]}
    assert set(data) == {"llama"}
    assert data["llama"]["served_by"] == 2
    # The group advertises the limit that holds for every replica.
    assert data["llama"]["context_length"] == 4096


async def test_models_mine_filters_on_the_routable_name(gw, rsa_keys):
    resp = await gw.get("/v1/models/mine", headers=_headers(rsa_keys, "inference:read model:llama"))

    assert [m["id"] for m in resp.json()["data"]] == ["llama"]


@respx.mock
async def test_usage_is_billed_to_the_requested_name_not_the_replica(gw, rsa_keys):
    """The operator's call (RM-57): billing follows what the client asked for,
    so one logical model has one price and one usage line however many
    replicas serve it. Metrics stay per-replica — see the test below.
    """
    from prometheus_gateway import db

    await db.create_tables(db.get_engine())
    respx.post(f"{REPLICA_A_URL}/v1/chat/completions").mock(
        return_value=Response(200, json=CHAT_RESPONSE)
    )

    resp = await gw.post(
        "/v1/chat/completions",
        json={"model": "llama", "messages": [{"role": "user", "content": "hi"}], "max_tokens": 5},
        headers=_headers(rsa_keys, "inference:read model:llama"),
    )
    assert resp.status_code == 200

    events = await db.query_usage_events_range(date(2000, 1, 1), date(2100, 1, 1))
    assert [e.model_id for e in events] == ["llama"], (
        "usage should be attributed to the logical name, not the replica that served it"
    )


@respx.mock
async def test_metrics_stay_on_the_replica_that_served(gw, rsa_keys, replica_app):
    """The other half of the split: observability has to point at the machine
    that actually did the work, or a slow replica is invisible."""
    from prometheus_gateway.telemetry import metrics_store

    respx.post(f"{REPLICA_A_URL}/v1/chat/completions").mock(
        return_value=Response(200, json=CHAT_RESPONSE)
    )

    await gw.post(
        "/v1/chat/completions",
        json={"model": "llama", "messages": [{"role": "user", "content": "hi"}], "max_tokens": 5},
        headers=_headers(rsa_keys, "inference:read model:llama"),
    )

    snapshot = await metrics_store.snapshot()
    assert "llama-a" in snapshot["backends"]
    assert "llama" not in snapshot["backends"]


# ── RM-70: the catalog slug is the public name; older spellings are aliases ──


def test_the_slug_is_what_routes():
    registry = _registry(
        _entry("inst-a", model_id="cat-id", model_slug="qwen3-0.6b"),
        _entry("inst-b", model_id="cat-id", model_slug="qwen3-0.6b"),
    )

    resolved = registry.resolve("qwen3-0.6b")

    assert resolved is not None
    assert [m.id for m in resolved.members] == ["inst-a", "inst-b"]


def test_the_old_catalog_id_still_resolves_as_an_alias():
    """Tokens and SDK calls issued before the identity split keep working —
    that's what makes the migration safe to ship without a flag day."""
    registry = _registry(_entry("inst-a", model_id="cat-id", model_slug="qwen3-0.6b"))

    resolved = registry.resolve("cat-id")

    assert resolved is not None
    assert [m.id for m in resolved.members] == ["inst-a"]


def test_an_instance_id_still_resolves_as_an_alias():
    registry = _registry(_entry("inst-a", model_id="cat-id", model_slug="qwen3-0.6b"))

    resolved = registry.resolve("inst-a")

    assert resolved is not None
    assert [m.id for m in resolved.members] == ["inst-a"]


def test_every_alias_bills_to_the_slug():
    """RM-69 made pricing key off the group rather than the string the client
    sent; RM-70 makes that key the public slug, so no alias is a cheaper or
    free route to the same model."""
    registry = _registry(_entry("inst-a", model_id="cat-id", model_slug="qwen3-0.6b"))

    keys = {
        name: registry.resolve(name).model_key  # type: ignore[union-attr]
        for name in ("qwen3-0.6b", "cat-id", "inst-a")
    }

    assert keys == {"qwen3-0.6b": "qwen3-0.6b", "cat-id": "qwen3-0.6b", "inst-a": "qwen3-0.6b"}


def test_the_model_list_shows_models_not_replicas():
    """RM-70: the aliases still resolve, but listing them would show a client
    four entries for one model with one replica — and an SDK building a model
    picker from this would render duplicates."""
    registry = _registry(
        _entry("inst-a", model_id="cat-id", model_slug="qwen3-0.6b"),
        _entry("inst-b", model_id="cat-id", model_slug="qwen3-0.6b"),
    )

    listed = registry.list_served_names()

    assert [r.name for r in listed] == ["qwen3-0.6b"]
    assert len(listed[0].members) == 2
    # …and every alias still routes, it just isn't advertised.
    assert registry.resolve("cat-id") is not None
    assert registry.resolve("inst-b") is not None


def test_two_different_models_are_both_listed():
    registry = _registry(
        _entry("a", model_id="a", model_slug="alpha"),
        _entry("b", model_id="b", model_slug="beta"),
    )

    assert {r.name for r in registry.list_served_names()} == {"alpha", "beta"}


# ── RM-70: a grant on the slug covers every alias the model answers to ──────


@respx.mock
async def test_a_slug_grant_covers_a_request_made_under_an_older_name(gw, rsa_keys):
    """Naming a model must not strand clients granted it under an older
    spelling, and a client sending the new name must not need a second grant."""
    respx.post(f"{REPLICA_A_URL}/v1/chat/completions").mock(
        return_value=Response(200, json=CHAT_RESPONSE)
    )

    resp = await gw.post(
        "/v1/chat/completions",
        json={"model": "llama-a", "messages": [{"role": "user", "content": "hi"}], "max_tokens": 5},
        # granted the group, asking for one replica by its own id
        headers=_headers(rsa_keys, "inference:read model:llama"),
    )

    assert resp.status_code == 200


async def test_no_model_grant_is_still_denied(gw, rsa_keys):
    """Deny-by-default is unchanged — widening to the slug must not become a
    way in for a token holding no model grant at all."""
    resp = await gw.post(
        "/v1/chat/completions",
        json={"model": "llama", "messages": [{"role": "user", "content": "hi"}], "max_tokens": 5},
        headers=_headers(rsa_keys, "inference:read"),
    )

    assert resp.status_code == 403


# ── RM-70/72: targeting one replica, and knowing which one answered ─────────


@respx.mock
async def test_the_response_says_which_replica_served(gw, rsa_keys):
    respx.post(f"{REPLICA_A_URL}/v1/chat/completions").mock(
        return_value=Response(200, json=CHAT_RESPONSE)
    )

    resp = await gw.post(
        "/v1/chat/completions",
        json={"model": "llama", "messages": [{"role": "user", "content": "hi"}], "max_tokens": 5},
        headers=_headers(rsa_keys, "inference:read model:llama"),
    )

    assert resp.status_code == 200
    assert resp.headers["X-Prometheus-Instance-Id"] == "llama-a"


@respx.mock
async def test_a_header_pins_the_request_to_one_replica(gw, rsa_keys):
    """`model` stays the model. Targeting a replica goes in a header, which is
    what lets one grant cover a whole group and keeps /v1/models listing models."""
    respx.post(f"{REPLICA_A_URL}/v1/chat/completions").mock(
        return_value=Response(200, json=CHAT_RESPONSE)
    )
    second = respx.post(f"{REPLICA_B_URL}/v1/chat/completions").mock(
        return_value=Response(200, json=CHAT_RESPONSE)
    )

    resp = await gw.post(
        "/v1/chat/completions",
        json={"model": "llama", "messages": [{"role": "user", "content": "hi"}], "max_tokens": 5},
        headers={
            **_headers(rsa_keys, "inference:read model:llama"),
            "X-Prometheus-Instance": "llama-b",
        },
    )

    assert resp.status_code == 200
    assert second.called
    assert resp.headers["X-Prometheus-Instance-Id"] == "llama-b"


@respx.mock
async def test_an_instance_from_another_model_is_refused(gw, rsa_keys):
    resp = await gw.post(
        "/v1/chat/completions",
        json={"model": "llama", "messages": [{"role": "user", "content": "hi"}], "max_tokens": 5},
        headers={
            **_headers(rsa_keys, "inference:read model:llama"),
            "X-Prometheus-Instance": "some-other-model",
        },
    )

    assert resp.status_code == 400
    assert resp.json()["type"].endswith("unknown-instance")


@respx.mock
async def test_usage_records_which_replica_served(gw, rsa_keys):
    """RM-73: the model is what gets billed, but a billing question has to be
    answerable down to a machine — which node produced this, why was it slow."""
    from prometheus_gateway import db

    await db.create_tables(db.get_engine())
    respx.post(f"{REPLICA_B_URL}/v1/chat/completions").mock(
        return_value=Response(200, json=CHAT_RESPONSE)
    )

    await gw.post(
        "/v1/chat/completions",
        json={"model": "llama", "messages": [{"role": "user", "content": "hi"}], "max_tokens": 5},
        headers={
            **_headers(rsa_keys, "inference:read model:llama"),
            "X-Prometheus-Instance": "llama-b",
        },
    )

    # UTC, not date.today(): record_usage stamps the day in UTC because billing
    # periods are UTC calendar months (RM-60). Querying the local date makes
    # this test fail for the five hours a day the two disagree — which is
    # exactly how it was found.
    utc_today = datetime.now(timezone.utc).date()
    events = await db.query_usage_events_range(utc_today, utc_today)
    assert [(e.model_id, e.instance_id) for e in events] == [("llama", "llama-b")]


# ── RM-77: the body names the model, never the replica ──────────────────────


@respx.mock
async def test_the_response_body_names_the_model_not_the_replica(gw, rsa_keys):
    """llama.cpp echoes its own --alias, which is the instance id. A client
    attributing cost by `response.model` would bill an identifier that isn't in
    the catalog, split across replica names nobody recognises."""
    respx.post(f"{REPLICA_A_URL}/v1/chat/completions").mock(
        return_value=Response(200, json={**CHAT_RESPONSE, "model": "llama-a"})
    )

    resp = await gw.post(
        "/v1/chat/completions",
        json={"model": "llama", "messages": [{"role": "user", "content": "hi"}], "max_tokens": 5},
        headers=_headers(rsa_keys, "inference:read model:llama"),
    )

    assert resp.json()["model"] == "llama"
    # …and the replica is still knowable, from the header where it belongs.
    assert resp.headers["X-Prometheus-Instance-Id"] == "llama-a"


@respx.mock
async def test_an_alias_request_is_answered_with_the_canonical_name(gw, rsa_keys):
    """Like OpenAI answering a `gpt-4o` request with the snapshot it resolved
    to: the body reports the identity that was actually used."""
    respx.post(f"{REPLICA_B_URL}/v1/chat/completions").mock(
        return_value=Response(200, json={**CHAT_RESPONSE, "model": "llama-b"})
    )

    resp = await gw.post(
        "/v1/chat/completions",
        json={"model": "llama-b", "messages": [{"role": "user", "content": "hi"}], "max_tokens": 5},
        headers=_headers(rsa_keys, "inference:read model:llama"),
    )

    assert resp.json()["model"] == "llama"


@respx.mock
async def test_every_streamed_chunk_names_the_model(gw, rsa_keys):
    chunks = (
        'data: {"model":"llama-a","choices":[{"delta":{"content":"hi"}}]}\n\n'
        'data: {"model":"llama-a","choices":[{"delta":{}}],"timings":{"prompt_n":3,"predicted_n":1}}\n\n'
        "data: [DONE]\n\n"
    )
    respx.post(f"{REPLICA_A_URL}/v1/chat/completions").mock(
        return_value=Response(200, text=chunks, headers={"Content-Type": "text/event-stream"})
    )

    resp = await gw.post(
        "/v1/chat/completions",
        json={
            "model": "llama",
            "messages": [{"role": "user", "content": "hi"}],
            "max_tokens": 5,
            "stream": True,
        },
        headers=_headers(rsa_keys, "inference:stream model:llama"),
    )

    body = resp.text
    assert '"model":"llama"' in body
    assert "llama-a" not in body
    assert "[DONE]" in body


async def test_an_image_model_advertises_no_context_window(gw, rsa_keys, settings):
    """RM-77: 0 reads as "a window of zero"; a client checking
    prompt_tokens < context_length would reject every image request."""
    from prometheus_gateway.main import create_app

    registry = ModelRegistry.__new__(ModelRegistry)
    registry._models = {
        "sd": _entry("sd", model_id="sd", modality="image", context_length=0),
    }
    app = create_app(settings=settings, registry=registry)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/v1/models")

    assert resp.json()["data"][0]["context_length"] is None
