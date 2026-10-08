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

    rows = [r for r in await db.query_activity_today(_dt.date.today()) if r["client_id"] == CLIENT]
    by_user: dict[str | None, int] = {}
    for row in rows:
        by_user[row["end_user"]] = by_user.get(row["end_user"], 0) + row["request_count"]
    assert by_user["alice"] == 2, "a billing path dropped the end user"
    assert by_user["bob"] == 1
    # PRM-236: and what each of them ran. Alice's two requests were two
    # different models, which is the grouping the detail view reads.
    assert {(r["end_user"], r["model"], r["request_kind"]) for r in rows} >= {
        ("alice", "small-model", "chat"),
        ("alice", "emb", "embedding"),
        ("bob", "small-model", "chat"),
    }


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
    assert consumer["today"]["request_count"] == 1
    # PRM-238: and the row is not blank. The first version reported the tracker's
    # silence as the consumer's — "not tracked since the gateway started" across
    # every column, above a drawer showing seven requests. What the usage rows
    # know is filled in, and flagged as coming from them.
    assert consumer["last_seen_source"] == "usage"
    assert consumer["last_seen_ago_s"] is not None
    assert [(a["action"], a["count"], a["source"]) for a in consumer["actions"]] == [
        ("chat", 1, "usage")
    ]
    # The connection type genuinely has no second source: nothing but the
    # tracker ever knew which kind of route it was.
    assert consumer["connection_type"] is None


async def test_a_reconstructed_row_cannot_show_what_never_bills(app, caller, admin):
    """The fallback's own limit, stated as a test.

    Usage rows exist for inference and nothing else, so a credential that only
    listed its models leaves no trace there. After a restart its row is empty
    — correctly — and that is why this is the fallback and not the source.
    """
    from prometheus_gateway.telemetry import activity_tracker

    await db.create_tables(db.get_engine())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        await c.get("/v1/models", headers=caller)
        async with activity_tracker._lock:
            activity_tracker._entries.clear()
        body = (await c.get("/admin/api/activity", headers=admin)).json()

    assert all(c["identity"] != CLIENT for c in body["consumers"])


async def test_activity_requires_admin_read(app, caller):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        assert (await c.get("/admin/api/activity", headers=caller)).status_code == 403


# ── PRM-236: what did they do ────────────────────────────────────────────────


def test_every_kind_of_call_has_a_label():
    """The question the old page could not answer. A credential that listed
    its models and one that ran a thousand completions both read as
    `connection_type: api`, which is the URL prefix of whichever request
    happened to be last."""
    from prometheus_gateway.telemetry import ACTION_LABELS, classify_action

    cases = {
        "/v1/chat/completions": "chat",
        "/v1/embeddings": "embeddings",
        "/v1/rerank": "rerank",
        "/v1/images/generations": "images",
        "/v1/models/qwen/predict": "predict",
        "/v1/models": "models.list",
        "/v1/backends": "backends",
        "/v1/usage/abc": "usage",
        "/admin/api/instances": "dashboard",
        "/v1/something-new": "other",
    }
    for path, expected in cases.items():
        assert classify_action(path) == expected, path
        assert expected in ACTION_LABELS


def test_the_playground_is_not_an_integration():
    """Same credential, same route — only the dashboard can say which it is,
    and an operator trying a model out should not read as production traffic.
    Self-reported, and only ever a label."""
    from prometheus_gateway.telemetry import classify_action

    assert classify_action("/v1/chat/completions", "playground") == "playground"
    assert classify_action("/v1/chat/completions", None) == "chat"


def test_the_label_set_is_closed():
    """These become per-identity counters in memory. A label taken from the URL
    unbounded is an unbounded dictionary, so an unknown path lands in `other`
    rather than minting a key."""
    from prometheus_gateway.telemetry import ACTION_LABELS, classify_action

    for path in ("/v1/a", "/v1/b", "/v1/c/d/e", "/x"):
        assert classify_action(path) == "other"
    assert len(ACTION_LABELS) == 11


async def test_activity_reports_what_each_consumer_did(app, caller, admin):
    """Two different kinds of call from one credential, both named."""
    await db.create_tables(db.get_engine())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        with respx.mock:
            respx.post("http://127.0.0.1:18081/v1/chat/completions").mock(
                return_value=Response(200, json=CHAT_RESPONSE)
            )
            await c.post("/v1/chat/completions", json=CHAT, headers=caller)
            await c.get("/v1/models", headers=caller)
        body = (await c.get("/admin/api/activity", headers=admin)).json()

    consumer = next(c for c in body["consumers"] if c["identity"] == CLIENT)
    did = {a["action"]: a["count"] for a in consumer["actions"]}
    assert did == {"chat": 1, "models.list": 1}
    # And the model it ran, which listing does not produce a usage row for.
    assert [(m["model"], m["request_kind"]) for m in consumer["models"]] == [
        ("small-model", "chat")
    ]


async def test_an_end_user_is_reported_across_every_consumer_that_served_them(
    app, caller, admin, rsa_keys
):
    """One person, two credentials. Nested inside each consumer that fact is
    invisible, which is why this list exists beside them rather than inside."""
    other = {
        "Authorization": "Bearer "
        + make_token(
            rsa_keys["private"],
            scope="inference:read model:small-model",
            sub="second-client",
            azp="second-client",
        )
    }
    await db.create_tables(db.get_engine())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        with respx.mock:
            respx.post("http://127.0.0.1:18081/v1/chat/completions").mock(
                return_value=Response(200, json=CHAT_RESPONSE)
            )
            await c.post("/v1/chat/completions", json={**CHAT, "user": "alice"}, headers=caller)
            await c.post("/v1/chat/completions", json={**CHAT, "user": "alice"}, headers=other)
        body = (await c.get("/admin/api/activity", headers=admin)).json()

    alice = next(u for u in body["end_users_today"] if u["end_user"] == "alice")
    assert alice["request_count"] == 2
    assert sorted(alice["consumers"]) == ["client-with-users", "second-client"]
    assert alice["models"] == ["small-model"]


async def test_an_end_user_row_names_the_models_it_ran(app, caller, admin):
    """ "Which client interacted with which user, on which model" is one row."""
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
        body = (await c.get("/admin/api/activity", headers=admin)).json()

    consumer = next(c for c in body["consumers"] if c["identity"] == CLIENT)
    alice = next(u for u in consumer["end_users"] if u["end_user"] == "alice")
    assert alice["request_count"] == 2
    assert {(m["model"], m["request_kind"]) for m in alice["models"]} == {
        ("small-model", "chat"),
        ("emb", "embedding"),
    }


# ── PRM-237: the number under the label ──────────────────────────────────────


async def test_the_action_count_is_the_window_not_a_running_total():
    """The pill said 284 under a heading that said "in the last 15 minutes".

    It was a cumulative count since the tracker first saw that credential, so
    an operator with the dashboard open read their own polling as a
    fifteen-minute figure that only ever grew. Counted in minute buckets now,
    and the snapshot sums the ones inside the window.
    """
    import time as _time

    from prometheus_gateway.telemetry import ActivityTracker

    tracker = ActivityTracker()
    now = _time.time()
    bucket = int(now // 60)
    await tracker.touch("c", "c", "dashboard", "dashboard")
    # Two buckets that aged out, planted directly: one inside the window and
    # one beyond it, so the sum can be wrong in both directions.
    entry = tracker._entries["c"]["actions"]["dashboard"]
    entry["buckets"][bucket - 3] = 50
    entry["buckets"][bucket - 100] = 900

    actions = {a["action"]: a["count"] for a in (await tracker.snapshot())[0]["actions"]}
    assert actions["dashboard"] == 51, "the window must exclude what fell out of it"


async def test_an_action_that_aged_out_entirely_is_not_listed():
    """Zero is not a thing a credential did. A row reading "Chat completions
    ×0" would be the page reporting an absence as an activity."""
    import time as _time

    from prometheus_gateway.telemetry import ActivityTracker

    tracker = ActivityTracker()
    await tracker.touch("c", "c", "api", "chat")
    await tracker.touch("c", "c", "dashboard", "dashboard")
    old_bucket = int(_time.time() // 60) - 99
    tracker._entries["c"]["actions"]["chat"]["buckets"] = {old_bucket: 7}

    actions = [a["action"] for a in (await tracker.snapshot())[0]["actions"]]
    assert actions == ["dashboard"]


async def test_the_page_says_which_row_is_you(app, caller, admin):
    """An operator who has only opened this page finds themselves at the top of
    it, because the dashboard polls. The traffic is real and belongs here; what
    was missing was the page saying whose it is."""
    await db.create_tables(db.get_engine())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        with respx.mock:
            respx.post("http://127.0.0.1:18081/v1/chat/completions").mock(
                return_value=Response(200, json=CHAT_RESPONSE)
            )
            await c.post("/v1/chat/completions", json=CHAT, headers=caller)
        body = (await c.get("/admin/api/activity", headers=admin)).json()

    mine = [c["identity"] for c in body["consumers"] if c["is_you"]]
    assert mine == ["admin"]
    assert next(c for c in body["consumers"] if c["identity"] == CLIENT)["is_you"] is False


# ── PRM-238: each table answers for its own window ───────────────────────────


async def test_the_live_detail_counts_the_live_window_not_the_day(app, caller, admin):
    """The contradiction the page used to print.

    One consumer showed `Chat completions ×23` — the last fifteen minutes —
    directly above its own detail reading `89 requests`, which was the day.
    Both true, neither labelled. Here the day holds a request from outside the
    window, and the two breakdowns have to disagree *by exactly that one*.
    """
    import datetime as _dt

    await db.create_tables(db.get_engine())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        with respx.mock:
            respx.post("http://127.0.0.1:18081/v1/chat/completions").mock(
                return_value=Response(200, json=CHAT_RESPONSE)
            )
            await c.post("/v1/chat/completions", json={**CHAT, "user": "alice"}, headers=caller)

        # An hour-old row for the same client and day, written directly: it
        # belongs to today and not to the live window.
        await db.record_usage(
            CLIENT,
            "small-model",
            5,
            3,
            end_user="alice",
            model_slug="small-model",
            request_id="older",
        )
        async with db.get_session_factory()() as session:
            from sqlalchemy import update

            await session.execute(
                update(db.UsageEvent)
                .where(db.UsageEvent.request_id == "older")
                .values(
                    recorded_at=_dt.datetime.now(_dt.timezone.utc).replace(tzinfo=None)
                    - _dt.timedelta(hours=1)
                )
            )
            await session.commit()

        body = (await c.get("/admin/api/activity", headers=admin)).json()

    consumer = next(c for c in body["consumers"] if c["identity"] == CLIENT)
    assert consumer["today"]["request_count"] == 2
    assert consumer["window"]["request_count"] == 1, "the live window swallowed the day"
    assert [u["request_count"] for u in consumer["window_end_users"]] == [1]
    assert [u["request_count"] for u in consumer["end_users"]] == [2]


def test_both_halves_of_the_window_start_at_the_same_instant():
    """The action counters are minute buckets and the detail is a timestamp
    filter on the usage rows. A rolling 900 seconds would include requests the
    buckets exclude, and the detail would not add up to the pill above it."""
    import time as _time

    from prometheus_gateway.telemetry import ActivityTracker

    now = _time.time()
    start = ActivityTracker.window_started_at(now)
    # Exactly on a bucket boundary, and covering the 15 buckets the snapshot
    # sums — never a fraction of a sixteenth.
    assert start % 60 == 0
    assert int(now // 60) - int(start // 60) == ActivityTracker._WINDOW_BUCKETS - 1


async def test_a_last_call_from_outside_the_window_does_not_put_you_in_it(app, caller, admin):
    """The fix that nearly wrote a second incoherence.

    When the tracker forgets, last-seen is reconstructed from the usage rows —
    but reconstructing it from *any* row today listed a consumer last seen
    seventeen minutes ago in a table whose first line promises the last
    fifteen. Only rows inside the window count.
    """
    import datetime as _dt

    from prometheus_gateway.telemetry import activity_tracker

    await db.create_tables(db.get_engine())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        with respx.mock:
            respx.post("http://127.0.0.1:18081/v1/chat/completions").mock(
                return_value=Response(200, json=CHAT_RESPONSE)
            )
            await c.post("/v1/chat/completions", json=CHAT, headers=caller)
        async with db.get_session_factory()() as session:
            from sqlalchemy import update

            await session.execute(
                update(db.UsageEvent)
                .where(db.UsageEvent.client_id == CLIENT)
                .values(
                    recorded_at=_dt.datetime.now(_dt.timezone.utc).replace(tzinfo=None)
                    - _dt.timedelta(minutes=40)
                )
            )
            await session.commit()
        async with activity_tracker._lock:
            activity_tracker._entries.clear()
        body = (await c.get("/admin/api/activity", headers=admin)).json()

    consumer = next(c for c in body["consumers"] if c["identity"] == CLIENT)
    assert consumer["last_seen_ago_s"] is None, "40 minutes ago is not 'here now'"
    assert consumer["window"]["request_count"] == 0
    # Still on the page, under today, where it belongs.
    assert consumer["today"]["request_count"] == 1


async def test_a_tracked_consumer_says_so(app, caller, admin):
    """The two sources are distinguishable, because one of them is partial."""
    await db.create_tables(db.get_engine())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        with respx.mock:
            respx.post("http://127.0.0.1:18081/v1/chat/completions").mock(
                return_value=Response(200, json=CHAT_RESPONSE)
            )
            await c.post("/v1/chat/completions", json=CHAT, headers=caller)
        body = (await c.get("/admin/api/activity", headers=admin)).json()

    consumer = next(c for c in body["consumers"] if c["identity"] == CLIENT)
    assert consumer["last_seen_source"] == "tracker"
    assert all(a.get("source") != "usage" for a in consumer["actions"])


async def test_the_drawer_shows_the_subtraction(app, caller, admin, rsa_keys):
    """Ten arrived and six billed, a line apart, read as a bug.

    They differ for real reasons — a stream still open, a caller that
    disconnected, a request refused before it reached a backend — and measured
    on the live deployment the gap was four, with the gateway log agreeing
    (10 auth.ok, 5 inference.complete). A page that shows both numbers has to
    name the difference, or every reader does the subtraction and concludes
    the page is broken.

    Here one request is refused for a model it was never granted, so it
    arrives and never bills.
    """
    import time as _time

    from prometheus_gateway.telemetry import metrics_store

    # The subtraction is only offered once the process has been up for the
    # whole window; a test process is seconds old. See the test below.
    metrics_store._start_time = _time.monotonic() - 3600
    await db.create_tables(db.get_engine())
    ungranted = {
        "Authorization": "Bearer "
        + make_token(
            rsa_keys["private"],
            # No `model:small-model` — RM-07 is deny-by-default.
            scope="inference:read",
            sub=CLIENT,
            azp=CLIENT,
        )
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        with respx.mock:
            respx.post("http://127.0.0.1:18081/v1/chat/completions").mock(
                return_value=Response(200, json=CHAT_RESPONSE)
            )
            await c.post("/v1/chat/completions", json=CHAT, headers=caller)
            refused = await c.post("/v1/chat/completions", json=CHAT, headers=ungranted)
        body = (await c.get("/admin/api/activity", headers=admin)).json()

    assert refused.status_code == 403
    consumer = next(c for c in body["consumers"] if c["identity"] == CLIENT)
    assert consumer["window_arrived"] == 2, "both requests arrived"
    assert consumer["window"]["request_count"] == 1, "only one of them billed"


async def test_a_reconstructed_row_claims_no_subtraction(app, caller, admin):
    """When the actions come from the usage rows, arrived and billed are the
    same number by construction — printing "1 arrived, 1 billed" there would be
    the page asserting something it did not measure."""
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
    assert consumer["window_arrived"] is None


async def test_the_subtraction_is_withheld_on_a_young_process(app, caller, admin):
    """A gateway up for two minutes has counted two minutes of arrivals, while
    the usage rows still cover the whole fifteen.

    Measured right after a restart: two calls arrived and four billed — a page
    reporting fewer requests than results, which cannot happen. Clamping the
    query to the uptime would make the two agree by throwing away the detail
    that survives a restart, which is the thing PRM-235 built. The detail keeps
    the window; the arithmetic waits.
    """
    import time as _time

    from prometheus_gateway.telemetry import metrics_store

    metrics_store._start_time = _time.monotonic() - 30
    await db.create_tables(db.get_engine())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        with respx.mock:
            respx.post("http://127.0.0.1:18081/v1/chat/completions").mock(
                return_value=Response(200, json=CHAT_RESPONSE)
            )
            await c.post("/v1/chat/completions", json=CHAT, headers=caller)
        body = (await c.get("/admin/api/activity", headers=admin)).json()

    assert body["window_is_comparable"] is False
    consumer = next(c for c in body["consumers"] if c["identity"] == CLIENT)
    assert consumer["window_arrived"] is None
    # The detail is untouched — it never depended on the tracker.
    assert consumer["window"]["request_count"] == 1
