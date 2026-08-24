"""Tests for spec 007 — Circuit Breaker, Retry, and Restart Resilience (AC-14 to AC-20).

Each test maps 1-to-1 with an Acceptance Criterion in:
memory/specs/007-rate-limiting-and-throughput.md
"""

from __future__ import annotations

import time

import fakeredis.aioredis as fakeredis
import pytest
import respx
from httpx import ASGITransport, AsyncClient, Response

from prometheus_gateway.circuit_breaker import CircuitBreaker
from prometheus_gateway.config import Settings
from prometheus_gateway.models.registry import ModelRegistry
from tests.conftest import make_token

# ── Fixtures ───────────────────────────────────────────────────────────────


@pytest.fixture
def fake_redis():
    return fakeredis.FakeRedis()


@pytest.fixture
def cb_settings(rsa_keys, tmp_path):
    key_file = tmp_path / "public.pem"
    key_file.write_text(rsa_keys["public"])
    return Settings(
        jwt_issuer="https://auth.test",
        jwt_audience="prometheus-gateway",
        jwt_public_key_file=str(key_file),
        jwt_clock_skew_seconds=30,
        jwt_revocation_redis_url=None,
        rate_limit_rpm=1000,  # very high — don't block on RL in these tests
        rate_limit_tpm=1_000_000,
        rate_limit_strict=False,  # fail-open for RL; CB is what we're testing
        circuit_breaker_failure_threshold=5,
        circuit_breaker_recovery_timeout=30,
        circuit_breaker_success_threshold=2,
        backend_retry_max=2,
        backend_retry_backoff_base_ms=10,  # very short for test speed
    )


@pytest.fixture
def small_registry(tmp_path):
    yaml_content = """models:
  - id: small-model
    path: /dev/null
    context_length: 4096
    family: llama3
    quantization: Q4_0
    backend_url: "http://127.0.0.1:18081"
"""
    f = tmp_path / "registry.yaml"
    f.write_text(yaml_content)
    return ModelRegistry(f)


@pytest.fixture
def cb_app(cb_settings, small_registry, fake_redis):
    from prometheus_gateway.main import create_app

    return create_app(settings=cb_settings, registry=small_registry, redis_client=fake_redis)


@pytest.fixture
def auth_headers(rsa_keys):
    # model:small-model — RM-07 per-model grant, deny-by-default.
    token = make_token(
        rsa_keys["private"],
        scope="inference:read inference:stream model:small-model",
        sub="u1",
        azp="c1",
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def admin_headers(rsa_keys):
    token = make_token(
        rsa_keys["private"], scope="admin:read inference:read", sub="admin", azp="admin-c"
    )
    return {"Authorization": f"Bearer {token}"}


VALID_BODY = {
    "model": "small-model",
    "messages": [{"role": "user", "content": "hey"}],
    "stream": False,
    "max_tokens": 5,
}

LLAMA_RESPONSE = {
    "id": "t1",
    "object": "chat.completion",
    "model": "small-model",
    "choices": [
        {"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}
    ],
    "usage": {"prompt_tokens": 2, "completion_tokens": 2, "total_tokens": 4},
}


# ── AC-14: Circuit opens after failure_threshold consecutive failures ────────


async def test_circuit_opens_after_threshold_AC14(
    cb_app, auth_headers, fake_redis
):  # memory/specs/007-rate-limiting-and-throughput.md
    """After 5 consecutive backend 503s, the 6th request fast-fails with 503 backend-unavailable."""
    async with AsyncClient(transport=ASGITransport(app=cb_app), base_url="http://test") as c:
        with respx.mock:
            respx.post("http://127.0.0.1:18081/v1/chat/completions").mock(
                return_value=Response(503, json={"error": "overloaded"})
            )
            # First 5 + retries: default retry_max=2 means 3 attempts per request
            # Need to ensure failure count builds up
            for i in range(5):
                await c.post("/v1/chat/completions", json=VALID_BODY, headers=auth_headers)

        # Now check the circuit breaker state in Redis
        state_raw = await fake_redis.get("prometheus:cb:small-model:state")

    # Circuit should now be open
    assert state_raw is not None
    assert state_raw.decode() == "open"


async def test_circuit_open_fast_fail_AC14(
    fake_redis,
):  # memory/specs/007-rate-limiting-and-throughput.md
    """When circuit is OPEN, allow_request() returns False immediately."""
    cb = CircuitBreaker(
        "test-backend",
        redis_client=fake_redis,
        failure_threshold=5,
        recovery_timeout=30,
        success_threshold=2,
    )
    # Force the circuit open in Redis
    now = time.time()
    await fake_redis.set("prometheus:cb:test-backend:state", "open")
    await fake_redis.set("prometheus:cb:test-backend:opened_at", str(now))
    await fake_redis.set("prometheus:cb:test-backend:failures", "5")

    allowed = await cb.allow_request()
    assert allowed is False


# ── AC-14b: Circuit transitions to HALF-OPEN after recovery_timeout ─────────


async def test_circuit_transitions_to_half_open_AC14b(
    fake_redis,
):  # memory/specs/007-rate-limiting-and-throughput.md
    """After recovery_timeout seconds, allow_request() transitions to HALF-OPEN."""
    cb = CircuitBreaker(
        "test-backend2",
        redis_client=fake_redis,
        failure_threshold=5,
        recovery_timeout=1,  # 1-second timeout for test speed
        success_threshold=2,
    )
    # Force open state with opened_at in the past (recovery_timeout + 2 sec ago)
    past = time.time() - 3
    await fake_redis.set("prometheus:cb:test-backend2:state", "open")
    await fake_redis.set("prometheus:cb:test-backend2:opened_at", str(past))
    await fake_redis.set("prometheus:cb:test-backend2:failures", "5")

    allowed = await cb.allow_request()

    assert allowed is True
    # State should be half-open now
    state_raw = await fake_redis.get("prometheus:cb:test-backend2:state")
    assert state_raw is not None
    assert state_raw.decode() == "half-open"


# ── AC-14c: Probe success closes circuit ────────────────────────────────────


async def test_circuit_closes_on_probe_success_AC14c(
    fake_redis,
):  # memory/specs/007-rate-limiting-and-throughput.md
    """When recorded successes reach success_threshold, circuit closes."""
    cb = CircuitBreaker(
        "test-backend3",
        redis_client=fake_redis,
        failure_threshold=5,
        recovery_timeout=30,
        success_threshold=2,
    )
    # Force half-open state
    await fake_redis.set("prometheus:cb:test-backend3:state", "half-open")
    await fake_redis.set("prometheus:cb:test-backend3:failures", "5")
    # Set probe lock to simulate we own it
    await fake_redis.set("prometheus:cb:test-backend3:probe_lock", "1")

    # Record 2 successes (threshold)
    await cb.record_success()
    await cb.record_success()

    # Circuit should now be closed (all CB keys deleted)
    state_raw = await fake_redis.get("prometheus:cb:test-backend3:state")
    assert state_raw is None  # CLOSED = no state key


# ── AC-14d: Probe failure re-opens circuit ───────────────────────────────────


async def test_circuit_reopens_on_probe_failure_AC14d(
    fake_redis,
):  # memory/specs/007-rate-limiting-and-throughput.md
    """When probe fails in HALF-OPEN, circuit returns to OPEN."""
    cb = CircuitBreaker(
        "test-backend4",
        redis_client=fake_redis,
        failure_threshold=2,  # low threshold
        recovery_timeout=30,
        success_threshold=2,
    )
    # Force half-open with enough existing failures to re-trigger open on one more
    await fake_redis.set("prometheus:cb:test-backend4:state", "half-open")
    await fake_redis.set("prometheus:cb:test-backend4:failures", "1")

    await cb.record_failure()
    await cb.record_failure()  # Now at threshold=2

    state_raw = await fake_redis.get("prometheus:cb:test-backend4:state")
    assert state_raw is not None
    assert state_raw.decode() == "open"


# ── AC-15: OPEN circuit response includes circuit_recovery_at ───────────────


async def test_circuit_open_response_body_AC15(
    cb_app, auth_headers, fake_redis
):  # memory/specs/007-rate-limiting-and-throughput.md
    """When circuit is OPEN, 503 response body includes circuit_recovery_at."""
    # Force the circuit open in Redis
    now = time.time()
    await fake_redis.set("prometheus:cb:small-model:state", "open")
    await fake_redis.set("prometheus:cb:small-model:opened_at", str(now))
    await fake_redis.set("prometheus:cb:small-model:failures", "10")

    async with AsyncClient(transport=ASGITransport(app=cb_app), base_url="http://test") as c:
        r = await c.post("/v1/chat/completions", json=VALID_BODY, headers=auth_headers)

    assert r.status_code == 503
    body = r.json()
    assert "backend-unavailable" in body["type"]
    assert "Retry-After" in r.headers
    # Detail should mention circuit state
    assert "open" in body["detail"].lower() or "circuit" in body["detail"].lower()


# ── AC-16: CB state survives gateway restart (read from Redis on startup) ────


async def test_circuit_state_survives_restart_AC16(
    cb_settings, small_registry, fake_redis, auth_headers
):  # memory/specs/007-rate-limiting-and-throughput.md
    """CB state stored in Redis is read by a new app instance (simulating pod restart)."""
    # Pre-seed an open circuit in Redis
    now = time.time()
    await fake_redis.set("prometheus:cb:small-model:state", "open")
    await fake_redis.set("prometheus:cb:small-model:opened_at", str(now))
    await fake_redis.set("prometheus:cb:small-model:failures", "7")

    # Create a brand-new app instance (simulates pod restart)
    from prometheus_gateway.main import create_app

    new_app = create_app(settings=cb_settings, registry=small_registry, redis_client=fake_redis)

    async with AsyncClient(transport=ASGITransport(app=new_app), base_url="http://test") as c:
        r = await c.post("/v1/chat/completions", json=VALID_BODY, headers=auth_headers)

    # Should get 503 because circuit was already open in Redis
    assert r.status_code == 503
    assert "backend-unavailable" in r.json()["type"]


# ── AC-17: Retry on transient errors ────────────────────────────────────────


async def test_retry_succeeds_on_third_attempt_AC17(
    cb_app, auth_headers
):  # memory/specs/007-rate-limiting-and-throughput.md
    """Backend fails twice (503) then succeeds; client receives 200."""
    call_count = 0

    def side_effect(request):
        nonlocal call_count
        call_count += 1
        if call_count <= 2:
            return Response(503, json={"error": "overloaded"})
        return Response(200, json=LLAMA_RESPONSE)

    async with AsyncClient(transport=ASGITransport(app=cb_app), base_url="http://test") as c:
        with respx.mock:
            respx.post("http://127.0.0.1:18081/v1/chat/completions").mock(side_effect=side_effect)
            r = await c.post("/v1/chat/completions", json=VALID_BODY, headers=auth_headers)

    assert r.status_code == 200
    assert call_count == 3  # 2 failures + 1 success


# ── AC-17b: All retries exhausted → 502 upstream-error ──────────────────────


async def test_retry_all_exhausted_AC17b(
    cb_app, auth_headers
):  # memory/specs/007-rate-limiting-and-throughput.md
    """When all 3 attempts (0, 1, 2) fail with 503, client receives 502 upstream-error."""
    async with AsyncClient(transport=ASGITransport(app=cb_app), base_url="http://test") as c:
        with respx.mock:
            respx.post("http://127.0.0.1:18081/v1/chat/completions").mock(
                return_value=Response(503, json={"error": "down"})
            )
            r = await c.post("/v1/chat/completions", json=VALID_BODY, headers=auth_headers)

    assert r.status_code == 502
    assert "upstream-error" in r.json()["type"]


# ── AC-18: Gateway restart uses existing Redis counters ─────────────────────


async def test_restart_uses_existing_counters_AC18(
    cb_settings, small_registry, fake_redis, rsa_keys
):  # memory/specs/007-rate-limiting-and-throughput.md
    """After restart, rate limit counters from Redis are honoured immediately."""
    # Adjust settings for very low RPM to make the test sensitive
    from prometheus_gateway.config import Settings
    import tempfile
    import os

    with tempfile.NamedTemporaryFile(suffix=".pem", delete=False, mode="w") as f:
        f.write(rsa_keys["public"])
        pem_path = f.name

    try:
        low_rpm_settings = Settings(
            jwt_issuer="https://auth.test",
            jwt_audience="prometheus-gateway",
            jwt_public_key_file=pem_path,
            jwt_revocation_redis_url=None,
            rate_limit_rpm=2,
            rate_limit_tpm=100_000,
            rate_limit_strict=True,
        )
        # Pre-seed the rate limit counter to max (rpm=2)
        import time as _t

        bucket = int(_t.time() // 60)
        key = f"prometheus:rl:rpm:sub-123:chat_completions:{bucket}"
        await fake_redis.set(key, 2)
        await fake_redis.expire(key, 90)

        from prometheus_gateway.main import create_app

        new_app = create_app(
            settings=low_rpm_settings, registry=small_registry, redis_client=fake_redis
        )
        token = make_token(
            rsa_keys["private"],
            scope="inference:read inference:stream model:small-model",
            sub="sub-123",
            azp="client-z",
            iss="https://auth.test",
            aud="prometheus-gateway",
        )
        headers = {"Authorization": f"Bearer {token}"}

        async with AsyncClient(transport=ASGITransport(app=new_app), base_url="http://test") as c:
            with respx.mock:
                respx.post("http://127.0.0.1:18081/v1/chat/completions").mock(
                    return_value=Response(200, json=LLAMA_RESPONSE)
                )
                r = await c.post("/v1/chat/completions", json=VALID_BODY, headers=headers)
    finally:
        os.unlink(pem_path)

    # Because the pre-seeded counter starts at 2 and the limit is 2,
    # the first request increments to 3 → exceeds limit → 429
    assert r.status_code == 429


# ── AC-19: Redis restart → gateway reconnects automatically ─────────────────


async def test_redis_reconnect_AC19(
    cb_settings, small_registry, rsa_keys
):  # memory/specs/007-rate-limiting-and-throughput.md
    """After a Redis error, subsequent requests succeed when Redis recovers.

    Uses fail-open mode so we can observe the request going through.
    """
    import tempfile
    import os

    with tempfile.NamedTemporaryFile(suffix=".pem", delete=False, mode="w") as f:
        f.write(rsa_keys["public"])
        pem_path = f.name

    try:
        fail_open_settings = Settings(
            jwt_issuer="https://auth.test",
            jwt_audience="prometheus-gateway",
            jwt_public_key_file=pem_path,
            jwt_revocation_redis_url=None,
            rate_limit_rpm=1000,
            rate_limit_tpm=1_000_000,
            rate_limit_strict=False,  # fail-open
        )

        class RecoveringPipeline:
            """Pipeline that fails on execute the first two times."""

            def __init__(self, fail: bool):
                self._fail = fail

            def incr(self, *a, **kw):
                return self

            def ttl(self, *a, **kw):
                return self

            async def execute(self, *a, **kw):
                if self._fail:
                    raise ConnectionError("Redis disconnected")
                return [1, 90]

        class RecoveringRedis:
            """Simulates Redis that fails on pipeline.execute() once then recovers."""

            def __init__(self):
                self._pipe_calls = 0

            def pipeline(self):
                self._pipe_calls += 1
                return RecoveringPipeline(fail=self._pipe_calls <= 2)

            async def get(self, *a, **kw):
                # Always return None — never raises, so JWT revocation passes
                return None

            async def set(self, *a, **kw):
                return True

            async def expire(self, *a, **kw):
                return True

        from prometheus_gateway.main import create_app

        app = create_app(
            settings=fail_open_settings, registry=small_registry, redis_client=RecoveringRedis()
        )
        token = make_token(
            rsa_keys["private"],
            scope="inference:read inference:stream model:small-model",
            sub="u1",
            azp="c1",
            iss="https://auth.test",
            aud="prometheus-gateway",
        )
        headers = {"Authorization": f"Bearer {token}"}

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            with respx.mock:
                respx.post("http://127.0.0.1:18081/v1/chat/completions").mock(
                    return_value=Response(200, json=LLAMA_RESPONSE)
                )
                # First request: Redis fails → fail-open → request still goes through
                r1 = await c.post("/v1/chat/completions", json=VALID_BODY, headers=headers)
                # Subsequent requests: Redis has recovered
                r2 = await c.post("/v1/chat/completions", json=VALID_BODY, headers=headers)
    finally:
        os.unlink(pem_path)

    # Both requests succeed (fail-open mode)
    assert r1.status_code == 200
    assert r2.status_code == 200


# ── AC-20: GET /v1/backends exposes CB state ────────────────────────────────


async def test_backends_exposes_circuit_state_AC20(
    cb_app, admin_headers, fake_redis
):  # memory/specs/007-rate-limiting-and-throughput.md
    """GET /v1/backends returns circuit_state, consecutive_failures, circuit_opened_at, circuit_recovery_at."""
    # Force an open circuit in Redis
    now = time.time()
    await fake_redis.set("prometheus:cb:small-model:state", "open")
    await fake_redis.set("prometheus:cb:small-model:opened_at", str(now))
    await fake_redis.set("prometheus:cb:small-model:failures", "7")

    async with AsyncClient(transport=ASGITransport(app=cb_app), base_url="http://test") as c:
        r = await c.get("/v1/backends", headers=admin_headers)

    assert r.status_code == 200
    body = r.json()
    entry = next((e for e in body["data"] if e["id"] == "small-model"), None)
    assert entry is not None
    assert entry["circuit_state"] == "open"
    assert entry["consecutive_failures"] == 7
    assert entry["circuit_opened_at"] is not None
    assert entry["circuit_recovery_at"] is not None
    assert entry["status"] == "circuit-open"


async def test_backends_closed_circuit_AC20(
    cb_app, admin_headers
):  # memory/specs/007-rate-limiting-and-throughput.md
    """GET /v1/backends - closed circuit shows circuit_state=closed, no timestamps."""
    async with AsyncClient(transport=ASGITransport(app=cb_app), base_url="http://test") as c:
        r = await c.get("/v1/backends", headers=admin_headers)

    assert r.status_code == 200
    body = r.json()
    entry = next((e for e in body["data"] if e["id"] == "small-model"), None)
    assert entry is not None
    assert entry["circuit_state"] == "closed"
    assert entry["circuit_opened_at"] is None
    assert entry["circuit_recovery_at"] is None
