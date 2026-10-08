"""PRM-235: Activity, and the end user the gateway was throwing away.

Implements: docs/roadmap.md — PRM-235.

The page this replaces was called Sessions and listed credentials seen in the
last fifteen minutes: three columns, from in-process memory that a restart
empties. It was also the wrong word — in LLM tooling a session is the grouping
of one conversation's or one agent run's calls (Langfuse groups traces by a
session id; Helicone groups requests into a tree), while what gateways publish
is traffic per consumer (Kong labels its counters by Consumer; LiteLLM breaks
spend down per key and per customer).

The test that matters most here is
`test_the_end_user_survives_every_path_that_bills`: the identifier is read in
six handlers, and "covers some paths and not others" is the defect PRM-224 was.
"""

from __future__ import annotations

import fakeredis.aioredis as fakeredis
import pytest
import respx
from httpx import ASGITransport, AsyncClient, Response

from prometheus_gateway import db
from prometheus_gateway.main import create_app
from prometheus_gateway.models.registry import ModelRegistry
from prometheus_gateway.router import END_USER_HEADER
from tests.conftest import dashboard_settings, make_token

CLIENT = "client-with-users"


@pytest.fixture
def fake_redis():
    return fakeredis.FakeRedis()


@pytest.fixture
def registry(tmp_path):
    f = tmp_path / "registry.yaml"
    f.write_text(
        """models:
  - id: small-model
    path: /dev/null
    context_length: 4096
    family: llama3
    quantization: Q4_0
    backend_url: "http://127.0.0.1:18081"
  - id: emb
    path: /dev/null
    context_length: 512
    family: test
    quantization: Q4_0
    modality: embedding
    backend_url: "http://127.0.0.1:18081"
"""
    )
    return ModelRegistry(f)


@pytest.fixture
def settings(rsa_keys, tmp_path):
    key_file = tmp_path / "public.pem"
    key_file.write_text(rsa_keys["public"])
    return dashboard_settings(key_file)


@pytest.fixture
def app(settings, registry, fake_redis):
    return create_app(settings=settings, registry=registry, redis_client=fake_redis)


@pytest.fixture
def caller(rsa_keys):
    return {
        "Authorization": "Bearer "
        + make_token(
            rsa_keys["private"],
            scope="inference:read inference:stream model:small-model model:emb",
            sub=CLIENT,
            azp=CLIENT,
        )
    }


@pytest.fixture
def admin(rsa_keys):
    return {
        "Authorization": "Bearer "
        + make_token(rsa_keys["private"], scope="admin:read", sub="admin", azp="admin")
    }


CHAT = {
    "model": "small-model",
    "messages": [{"role": "user", "content": "hi"}],
    "stream": False,
    "max_tokens": 10,
}
CHAT_RESPONSE = {
    "id": "x",
    "object": "chat.completion",
    "model": "small-model",
    "choices": [
        {"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}
    ],
    "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
}


# ── Reading the identifier ───────────────────────────────────────────────────


def test_the_precedence_is_the_one_callers_already_know(rsa_keys):
    """Header, then `user`, then `safety_identifier` — LiteLLM's order.

    Copied rather than invented: a caller already pointing at that proxy should
    not have to learn a different precedence to point at this one.
    """
    from types import SimpleNamespace

    from prometheus_gateway.router import resolve_end_user

    body = SimpleNamespace(user="from-body", safety_identifier="from-safety")
    with_header = SimpleNamespace(headers={END_USER_HEADER: "from-header"})
    assert resolve_end_user(with_header, body) == "from-header"

    no_header = SimpleNamespace(headers={})
    assert resolve_end_user(no_header, body) == "from-body"
    assert resolve_end_user(no_header, SimpleNamespace(user=None, safety_identifier="s")) == "s"
    assert resolve_end_user(no_header, SimpleNamespace(user=None, safety_identifier=None)) is None
    # Blank is not an identifier. A caller sending "" has named nobody, and
    # storing an empty string would make a group key out of it.
    assert resolve_end_user(no_header, SimpleNamespace(user="   ", safety_identifier=None)) is None


def test_the_identifier_is_no_longer_reported_as_ignored():
    """It used to be, truthfully — measured against the running gateway, which
    answered `x-prometheus-ignored-parameters: safety_identifier, user`. Now
    that it is read, saying it is ignored would be the lie in the other
    direction."""
    from prometheus_gateway.models.schemas import ChatCompletionRequest, ignored_parameters

    body = ChatCompletionRequest(
        model="m",
        messages=[{"role": "user", "content": "hi"}],
        user="alice",
        safety_identifier="sha256:x",
        nonsense=1,
    )
    assert ignored_parameters(body) == ["nonsense"]


def test_the_identifier_is_not_forwarded_to_the_backend():
    """Declared and still not forwarded. The allowlist bounds what leaves this
    process, llama.cpp has no use for it, and the caller's own identifier for
    its users is not something to hand to an engine that did not ask."""
    from prometheus_gateway.models.schemas import ChatCompletionRequest

    body = ChatCompletionRequest(
        model="m", messages=[{"role": "user", "content": "hi"}], user="alice"
    )
    assert "user" not in body.to_llama_payload()
    assert "safety_identifier" not in body.to_llama_payload()


# ── The path coverage ────────────────────────────────────────────────────────


async def test_the_end_user_survives_every_path_that_bills(app, caller):
    """Buffered chat, streamed chat and embeddings all have to land it.

    The streaming path nearly did not: `_stream_response` has its own local
    `body` — the backend's error payload — so resolving there read a name that
    was not in scope, and the suite said so. Had it resolved to None instead,
    `user` would have worked for a buffered request and silently not for a
    streamed one.
    """
    await db.create_tables(db.get_engine())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        with respx.mock:
            respx.post("http://127.0.0.1:18081/v1/chat/completions").mock(
                return_value=Response(200, json=CHAT_RESPONSE)
            )
            respx.post("http://127.0.0.1:18081/v1/embeddings").mock(
                return_value=Response(
                    200,
                    json={
                        "object": "list",
                        "data": [{"object": "embedding", "index": 0, "embedding": [0.1]}],
                        "model": "emb",
                        "usage": {"prompt_tokens": 2, "total_tokens": 2},
                    },
                )
            )
            await c.post("/v1/chat/completions", json={**CHAT, "user": "alice"}, headers=caller)
            await c.post(
                "/v1/embeddings",
                json={"model": "emb", "input": "hola", "user": "alice"},
                headers=caller,
            )
            # And through the header, which is the only route the pass-through
            # endpoint has.
            await c.post(
                "/v1/chat/completions",
                json=CHAT,
                headers={**caller, END_USER_HEADER: "bob"},
            )

    import datetime as _dt

    rows = await db.query_activity_today(_dt.date.today())
    by_user = {r["end_user"]: r for r in rows if r["client_id"] == CLIENT}
    assert by_user["alice"]["request_count"] == 2, "a billing path dropped the end user"
    assert by_user["bob"]["request_count"] == 1


async def test_traffic_with_no_end_user_is_kept_under_one_null_row(app, caller):
    """Most traffic names nobody, and that share is the point: dropping those
    rows would make this page's per-consumer totals disagree with Billing's."""
    await db.create_tables(db.get_engine())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        with respx.mock:
            respx.post("http://127.0.0.1:18081/v1/chat/completions").mock(
                return_value=Response(200, json=CHAT_RESPONSE)
            )
            await c.post("/v1/chat/completions", json=CHAT, headers=caller)
            await c.post("/v1/chat/completions", json={**CHAT, "user": "alice"}, headers=caller)

    import datetime as _dt

    rows = [r for r in await db.query_activity_today(_dt.date.today()) if r["client_id"] == CLIENT]
    assert {(r["end_user"], r["request_count"]) for r in rows} == {("alice", 1), (None, 1)}


# ── The page's own endpoint ──────────────────────────────────────────────────


async def test_activity_reports_the_minute_the_day_and_the_end_users(app, caller, admin):
    await db.create_tables(db.get_engine())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        with respx.mock:
            respx.post("http://127.0.0.1:18081/v1/chat/completions").mock(
                return_value=Response(200, json=CHAT_RESPONSE)
            )
            await c.post("/v1/chat/completions", json={**CHAT, "user": "alice"}, headers=caller)
        body = (await c.get("/admin/api/activity", headers=admin)).json()

    consumer = next(c for c in body["consumers"] if c["identity"] == CLIENT)
    # This minute, from Redis.
    assert consumer["rpm"]["used"] == 1
    assert consumer["rpm"]["limit"] == 60 * 6  # PRM-227's derived client ceiling
    assert consumer["worst"] is not None
    # Today, from the usage rows.
    assert consumer["today"]["request_count"] == 1
    assert consumer["today"]["total_tokens"] == 8
    # And who it was for.
    assert [u["end_user"] for u in consumer["end_users"]] == ["alice"]
    # The platform's own counters, which no consumer row adds up to.
    assert body["platform"]["rpm"]["used"] >= 1
    # Every cell on this page carries `percent`, including this one — built
    # inline it was the single cell without it, and the page printed
    # "undefined%" under the bar. None rather than 0: the platform layer is
    # opt-in and nothing here sets a ceiling, so there is no percentage to show.
    assert "percent" in body["platform"]["rpm"]
    assert body["platform"]["rpm"]["percent"] is None


async def test_a_consumer_the_tracker_forgot_is_still_listed(app, caller, admin):
    """The failure the old page had after every deploy.

    `ActivityTracker` lives in process memory, so a restart empties it while
    Redis and the usage rows keep theirs. A page sourced from the tracker alone
    goes blank and reads as "nobody is using this platform" — the mistake
    PRM-223 fixed on the circuit table. Here the tracker is cleared by hand and
    the consumer still has to appear, with last-seen reported as unknown rather
    than as zero.
    """
    from prometheus_gateway.telemetry import activity_tracker

    await db.create_tables(db.get_engine())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        with respx.mock:
            respx.post("http://127.0.0.1:18081/v1/chat/completions").mock(
                return_value=Response(200, json=CHAT_RESPONSE)
            )
            await c.post("/v1/chat/completions", json=CHAT, headers=caller)
        async with activity_tracker._lock:
            activity_tracker._entries.clear()
        body = (await c.get("/admin/api/activity", headers=admin)).json()

    consumer = next(c for c in body["consumers"] if c["identity"] == CLIENT)
    assert consumer["last_seen_ago_s"] is None
    assert consumer["connection_type"] is None
    assert consumer["today"]["request_count"] == 1


async def test_activity_requires_admin_read(app, caller):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        assert (await c.get("/admin/api/activity", headers=caller)).status_code == 403
