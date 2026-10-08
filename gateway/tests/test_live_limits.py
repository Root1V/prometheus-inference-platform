"""PRM-230: which ceiling is refusing right now.

Implements: docs/roadmap.md — PRM-230.

PRM-227 made a request pass three conjoined layers and PRM-224..228 took the
dimensions to seven. A caller sees one 429 and the reason is whichever of
twenty-one counters crossed first, which the Limits page could not answer
because it showed configuration and the answer is a measurement.

The test that matters here is
`test_the_live_view_names_the_ceiling_that_actually_refused`: it exhausts a
real budget through the real middleware and asserts the view names the same
layer and dimension the 429 did. The recurring defect in this codebase is an
instrument asserting something it did not measure, and a diagnostic page is
the one place that failure is invisible — a plausible-looking table is
indistinguishable from a correct one.
"""

from __future__ import annotations

import fakeredis.aioredis as fakeredis
import pytest
import respx
from httpx import ASGITransport, AsyncClient, Response

from prometheus_gateway.rate_limiter import ALL_ENDPOINTS, PLATFORM_IDENTITY, RateLimiter
from tests.conftest import dashboard_settings, make_token

from tests.test_rate_limiting import LLAMA_RESPONSE, VALID_BODY


@pytest.fixture
def live_settings(rsa_keys, tmp_path):
    """The dashboard on, and limits small enough to exhaust in four requests.

    Spec 007's `rl_settings` has the small limits and not the dashboard, and
    this page is on the dashboard — so the two ends of the thing under test
    only meet in a fixture that has both.
    """
    key_file = tmp_path / "public.pem"
    key_file.write_text(rsa_keys["public"])
    return dashboard_settings(
        key_file,
        rate_limit_rpm=3,
        rate_limit_tpm=100,
        rate_limit_strict=True,
    )


@pytest.fixture
def live_app(live_settings, small_registry, fake_redis):
    from prometheus_gateway.main import create_app

    return create_app(settings=live_settings, registry=small_registry, redis_client=fake_redis)


@pytest.fixture
def fake_redis():
    return fakeredis.FakeRedis()


@pytest.fixture
def small_registry(tmp_path):
    from prometheus_gateway.models.registry import ModelRegistry

    f = tmp_path / "registry.yaml"
    f.write_text(
        """models:
  - id: small-model
    path: /dev/null
    context_length: 4096
    family: llama3
    quantization: Q4_0
    backend_url: "http://127.0.0.1:18081"
"""
    )
    return ModelRegistry(f)


@pytest.fixture
def caller_headers(rsa_keys):
    return {
        "Authorization": "Bearer "
        + make_token(
            rsa_keys["private"],
            scope="inference:read inference:stream model:small-model",
            sub="client-a",
            azp="client-a",
        )
    }


@pytest.fixture
def live_headers(rsa_keys):
    return {
        "Authorization": "Bearer "
        + make_token(rsa_keys["private"], scope="admin:read", sub="admin-user", azp="admin-client")
    }


# ── The reserved tokens, in two modules ──────────────────────────────────────


def test_the_reserved_identity_tokens_agree():
    """`rate_limits` repeats them rather than importing a redis-dependent module
    for two strings — so something has to check they still match."""
    from prometheus_gateway import rate_limits

    assert rate_limits._PLATFORM_IDENTITY == PLATFORM_IDENTITY
    assert rate_limits._ALL_ENDPOINTS == ALL_ENDPOINTS


# ── Layer is read off the key, not guessed ───────────────────────────────────


def test_the_layer_comes_from_the_key_shape():
    from prometheus_gateway.rate_limits import counter_layer

    assert counter_layer(PLATFORM_IDENTITY, ALL_ENDPOINTS) == "platform"
    assert counter_layer("client-a", ALL_ENDPOINTS) == "client"
    assert counter_layer("client-a", "chat_completions") == "endpoint"


def test_an_input_token_counter_is_an_endpoint_counter_despite_its_grouping(settings):
    """`rate_limit_tpm_input` sits in the client group in `LIMIT_LAYERS` and is
    checked per endpoint. Deriving the layer from that grouping would report a
    counter against a ceiling that never reads it."""
    from prometheus_gateway.rate_limits import LIMIT_LAYERS, counter_layer, live_limit_for

    client_group = next(fields for layer, _, fields in LIMIT_LAYERS if layer == "client")
    assert "rate_limit_tpm_input" in client_group

    settings.rate_limit_tpm_input = 1_000
    assert counter_layer("client-a", "embeddings") == "endpoint"
    assert live_limit_for(
        settings,
        layer="endpoint",
        dimension="tpm_in",
        endpoint="embeddings",
        endpoint_count=6,
    ) == (1_000, "set")


# ── Which ceiling a counter is measured by ───────────────────────────────────


def test_the_client_layer_default_is_the_sum_not_the_per_endpoint_value(settings):
    """PRM-227's default, reported as `derived` because nobody chose it — and
    reported at all, because the layer enforces it regardless."""
    from prometheus_gateway.rate_limits import live_limit_for

    settings.rate_limit_rpm = 60
    settings.rate_limit_rpm_client = None
    assert live_limit_for(
        settings, layer="client", dimension="rpm", endpoint=ALL_ENDPOINTS, endpoint_count=6
    ) == (360, "derived")


def test_a_tier_wins_over_the_derived_default(settings):
    from prometheus_gateway.db import RateLimitTier
    from prometheus_gateway.rate_limits import live_limit_for

    settings.rate_limit_rpm = 60
    settings.rate_limit_rpm_client = None
    tier = RateLimitTier(name="pilot", rpm=120)
    assert live_limit_for(
        settings,
        layer="client",
        dimension="rpm",
        endpoint=ALL_ENDPOINTS,
        endpoint_count=6,
        tier=tier,
    ) == (120, "tier")


def test_a_tier_that_is_silent_on_a_dimension_does_not_grant_it(settings):
    """A tier omitting `tpm` says nothing about tokens — it does not lift the
    ceiling. Same reading the middleware applies."""
    from prometheus_gateway.db import RateLimitTier
    from prometheus_gateway.rate_limits import live_limit_for

    settings.rate_limit_tpm = 40_000
    settings.rate_limit_tpm_client = None
    limit, source = live_limit_for(
        settings,
        layer="client",
        dimension="tpm",
        endpoint=ALL_ENDPOINTS,
        endpoint_count=6,
        tier=RateLimitTier(name="pilot", rpm=120),
    )
    assert (limit, source) == (240_000, "derived")


def test_the_endpoint_override_is_the_ceiling_where_one_is_set(settings):
    from prometheus_gateway.rate_limits import live_limit_for

    settings.rate_limit_rpm = 60
    settings.rate_limit_rpm_chat_completions = 10
    assert live_limit_for(
        settings,
        layer="endpoint",
        dimension="rpm",
        endpoint="chat_completions",
        endpoint_count=6,
    ) == (10, "set")
    assert live_limit_for(
        settings, layer="endpoint", dimension="rpm", endpoint="embeddings", endpoint_count=6
    ) == (60, "set")


def test_a_counter_nothing_checks_reports_no_ceiling(settings):
    """The router increments tpm_in/tpm_out at the all-endpoints key too, and
    nothing ever reads them there. `None` rather than a plausible number: a
    measured, unenforced dimension is a finding, and inventing a ceiling for it
    would bury the finding."""
    from prometheus_gateway.rate_limits import live_limit_for

    settings.rate_limit_tpm_input = 1_000
    assert live_limit_for(
        settings, layer="client", dimension="tpm_in", endpoint=ALL_ENDPOINTS, endpoint_count=6
    ) == (None, "none")


def test_an_unset_platform_ceiling_refuses_nothing(settings):
    from prometheus_gateway.rate_limits import live_limit_for

    settings.rate_limit_rpm_platform = None
    assert live_limit_for(
        settings, layer="platform", dimension="rpm", endpoint=ALL_ENDPOINTS, endpoint_count=6
    ) == (None, "none")


# ── The scan ─────────────────────────────────────────────────────────────────


async def test_live_counters_finds_every_dimension_that_was_written():
    """Scanned, because the set of identities is not knowable from the gateway:
    user_id and client_id are counted separately and principals live in another
    service."""
    limiter = RateLimiter(fakeredis.FakeRedis())
    await limiter.check_and_increment_rpm("client-a", "chat_completions", 60)
    await limiter.increment_tpm("client-a", "chat_completions", 30, 12)
    await limiter.increment_tpd("client-a", "chat_completions", 42)
    await limiter.check_and_increment_rpd("client-a", "chat_completions", 1_000)
    await limiter.increment_ipm("client-a", "images", 2)
    await limiter.check_and_increment_rpm(PLATFORM_IDENTITY, ALL_ENDPOINTS, 600)

    rows = {
        (r["dimension"], r["identity"], r["endpoint"]): r for r in await limiter.live_counters()
    }

    assert rows[("rpm", "client-a", "chat_completions")]["used"] == 1
    assert rows[("tpm", "client-a", "chat_completions")]["used"] == 42
    assert rows[("tpm_in", "client-a", "chat_completions")]["used"] == 30
    assert rows[("tpm_out", "client-a", "chat_completions")]["used"] == 12
    assert rows[("tpd", "client-a", "chat_completions")]["used"] == 42
    assert rows[("rpd", "client-a", "chat_completions")]["used"] == 1
    assert rows[("ipm", "client-a", "images")]["used"] == 2
    assert rows[("rpm", PLATFORM_IDENTITY, ALL_ENDPOINTS)]["used"] == 1
    assert rows[("tpd", "client-a", "chat_completions")]["window"] == "day"
    assert rows[("tpm", "client-a", "chat_completions")]["window"] == "minute"


async def test_live_counters_is_empty_on_an_untouched_store():
    """Not a failure state and not an error — a gateway nobody is calling."""
    assert await RateLimiter(fakeredis.FakeRedis()).live_counters() == []


# ── The endpoint ─────────────────────────────────────────────────────────────


async def test_the_live_view_names_the_ceiling_that_actually_refused(
    live_app, caller_headers, live_headers
):
    """The one test worth having: exhaust a real budget and check the diagnostic
    agrees with the 429 about which layer and dimension stopped it.

    `live_settings` sets rpm=3, so the endpoint layer bites at the fourth request
    while the client layer (3 × 6 = 18) still has room — which is the whole
    reason the two are separate, and the distinction the page has to get right.
    """
    async with AsyncClient(transport=ASGITransport(app=live_app), base_url="http://test") as c:
        with respx.mock:
            respx.post("http://127.0.0.1:18081/v1/chat/completions").mock(
                return_value=Response(200, json=LLAMA_RESPONSE)
            )
            for _ in range(3):
                assert (
                    await c.post("/v1/chat/completions", json=VALID_BODY, headers=caller_headers)
                ).status_code == 200
            refused = await c.post("/v1/chat/completions", json=VALID_BODY, headers=caller_headers)
        assert refused.status_code == 429
        assert refused.json()["scope"] == "chat_completions"

        live = await c.get("/admin/api/limits/live", headers=live_headers)

    assert live.status_code == 200
    body = live.json()
    assert body["available"] is True
    top = body["rows"][0]
    assert (top["layer"], top["dimension"], top["endpoint"]) == (
        "endpoint",
        "rpm",
        "chat_completions",
    )
    assert top["used"] == 4 and top["limit"] == 3
    assert top["percent"] >= 100

    # Keyed by identity as well, because the dashboard's own request is in here
    # too — `admin-client` on the `admin` endpoint. That is not noise to filter:
    # the admin bucket has its own ceiling and `MIN_ADMIN_RPM` exists because
    # exhausting it locks the operator out of the page that would undo it.
    rows = {(r["layer"], r["dimension"], r["identity"], r["endpoint"]): r for r in body["rows"]}
    assert ("endpoint", "rpm", "admin-client", "admin") in rows

    # The consumer layer counted the three that got through, against a ceiling
    # six times wider — the fourth never reached it, having been refused one
    # layer earlier. Both numbers matter: 3 of 18 is why `scope` said
    # `chat_completions` and not `client`.
    client_row = rows[("client", "rpm", "client-a", ALL_ENDPOINTS)]
    assert client_row["used"] == 3 and client_row["limit"] == 18
    assert client_row["limit_source"] == "derived"
    # And the unenforced pair is present, last, saying so.
    unmetered = [r for r in body["rows"] if r["limit"] is None]
    assert unmetered, "tpm_in/tpm_out at the all-endpoints key have no ceiling"
    assert body["rows"][-1]["limit"] is None
    assert all(r["percent"] is None for r in unmetered)


async def test_the_live_view_says_so_when_there_is_no_store(
    live_settings, small_registry, live_headers
):
    """An empty table reads as quiet traffic, and no store is the opposite of
    quiet — nothing is being counted or refused at all."""
    from prometheus_gateway.main import create_app

    live_settings.rate_limit_redis_url = None
    live_settings.rate_limit_strict = False
    app = create_app(settings=live_settings, registry=small_registry, redis_client=None)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        body = (await c.get("/admin/api/limits/live", headers=live_headers)).json()

    assert body["available"] is False
    assert body["rows"] == []
    assert "nothing is being counted" in body["reason"]


async def test_strict_mode_with_no_store_refuses_the_page_itself(
    live_settings, small_registry, live_headers
):
    """Why the reason above does not mention `rate_limit_strict`.

    First draft of this endpoint had a branch saying "every request is refused"
    for the strict case. It can never run: with strict and no limiter the
    middleware 503s *every* path, this one included, so there is no diagnostic
    to read — the operator gets the 503 and that is the diagnosis. A branch
    that cannot execute is a claim nothing checks, which is the shape of defect
    this file exists to catch.
    """
    from prometheus_gateway.main import create_app

    live_settings.rate_limit_redis_url = None
    live_settings.rate_limit_strict = True
    app = create_app(settings=live_settings, registry=small_registry, redis_client=None)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        response = await c.get("/admin/api/limits/live", headers=live_headers)

    assert response.status_code == 503
    assert response.json()["type"].endswith("rate-limiting-unavailable")


async def test_the_live_view_needs_admin_read(live_app, caller_headers):
    async with AsyncClient(transport=ASGITransport(app=live_app), base_url="http://test") as c:
        assert (await c.get("/admin/api/limits/live", headers=caller_headers)).status_code == 403


# ── PRM-233: counted before a ceiling exists ─────────────────────────────────


async def test_the_platform_is_counted_even_with_no_platform_ceiling(
    live_app, caller_headers, live_headers
):
    """ "How much is this platform doing right now" was answerable only after
    someone had already chosen a ceiling.

    The platform's TPM counter has always been written on every request; its
    RPM counter only existed once `RATE_LIMIT_RPM_PLATFORM` was set. So the
    dashboard could show what the platform was spending in tokens and not in
    requests, and the operator deciding what the number should be was the one
    person who could not see it.
    """
    from prometheus_gateway.rate_limiter import PLATFORM_IDENTITY

    async with AsyncClient(transport=ASGITransport(app=live_app), base_url="http://test") as c:
        with respx.mock:
            respx.post("http://127.0.0.1:18081/v1/chat/completions").mock(
                return_value=Response(200, json=LLAMA_RESPONSE)
            )
            assert (
                await c.post("/v1/chat/completions", json=VALID_BODY, headers=caller_headers)
            ).status_code == 200
        body = (await c.get("/admin/api/limits/live", headers=live_headers)).json()

    assert live_app.state.settings.rate_limit_rpm_platform is None
    rows = {(r["layer"], r["dimension"]): r for r in body["rows"]}
    platform_rpm = rows[("platform", "rpm")]
    # Two requests reached the middleware: the inference call and this read of
    # /admin/api/limits/live itself.
    assert platform_rpm["identity"] == PLATFORM_IDENTITY
    assert platform_rpm["used"] >= 1
    # Counted, and refusing nothing — which is the state the row has to be
    # able to express without inventing a ceiling for it.
    assert platform_rpm["limit"] is None
    assert platform_rpm["limit_source"] == "none"
    assert platform_rpm["percent"] is None


async def test_an_unset_platform_ceiling_still_refuses_nothing(live_app, caller_headers):
    """Counting is not enforcing. The counter is unconditional now, so the
    claim that the platform layer is opt-in needs its own test rather than
    resting on the absence of a Redis key."""
    async with AsyncClient(transport=ASGITransport(app=live_app), base_url="http://test") as c:
        with respx.mock:
            respx.post("http://127.0.0.1:18081/v1/chat/completions").mock(
                return_value=Response(200, json=LLAMA_RESPONSE)
            )
            # `live_settings` allows 3/min per endpoint, so three pass and the
            # fourth is refused by the endpoint layer — never the platform.
            for _ in range(3):
                await c.post("/v1/chat/completions", json=VALID_BODY, headers=caller_headers)
            refused = await c.post("/v1/chat/completions", json=VALID_BODY, headers=caller_headers)

    assert refused.status_code == 429
    assert refused.json()["scope"] != "platform"


async def test_the_login_throttle_is_reported(live_app, live_headers):
    """The rate limit the Limits page never showed: every ceiling it listed is
    keyed on a credential, and this is the one that applies to someone who does
    not have one yet."""
    from prometheus_gateway import db

    await db.create_tables(db.get_engine())
    async with AsyncClient(transport=ASGITransport(app=live_app), base_url="http://test") as c:
        body = (await c.get("/admin/api/limits", headers=live_headers)).json()

    throttle = body["login_throttle"]
    assert throttle["rpm"] == live_app.state.settings.ui_login_rate_limit_rpm
    assert throttle["active"] is live_app.state.settings.ui_enabled


# ── PRM-234: the layers are numbered, once ───────────────────────────────────


def test_the_layer_numbers_follow_the_order_a_request_meets_them():
    """Broadest first, which is both the enforcement order and the order the
    middleware's comments have called them since PRM-227 — "Layer 1: the
    platform", "Layer 3 alone bounds a route".

    Derived from `LIMIT_LAYERS` rather than written out again, so this test is
    really about the tuple's order: reordering it renumbers the page, and the
    page is where someone reads which ceiling is the broad one.
    """
    from prometheus_gateway.rate_limits import LAYER_NUMBERS

    assert LAYER_NUMBERS == {"platform": 1, "client": 2, "endpoint": 3}


async def test_both_payloads_number_the_layers_the_same_way(live_app, live_headers):
    """The configuration cards and the live counters are two views of three
    layers. Numbering them separately would let one page say Layer 2 while the
    other said Layer 3 about the same ceiling."""
    from prometheus_gateway import db
    from prometheus_gateway.rate_limits import LAYER_NUMBERS

    await db.create_tables(db.get_engine())

    async with AsyncClient(transport=ASGITransport(app=live_app), base_url="http://test") as c:
        limits = (await c.get("/admin/api/limits", headers=live_headers)).json()
        live = (await c.get("/admin/api/limits/live", headers=live_headers)).json()

    assert {layer["layer"]: layer["n"] for layer in limits["layers"]} == LAYER_NUMBERS
    for row in live["rows"]:
        assert row["layer_n"] == LAYER_NUMBERS[row["layer"]]
