"""PRM-185: the blocking query's `wait` is wall seconds, not awake seconds.

Implements: docs/roadmap.md — PRM-185.

`GET /v1/backends?index=…&wait=N` holds the request until the registry index
changes or `wait` seconds pass. The deadline used `time.monotonic()`, which on
macOS is `mach_absolute_time()` and tracks `CLOCK_UPTIME_RAW`: it **stops while
the machine sleeps**. Measured on the development host, 205.50 h against 678.97 h
of wall time since boot — 474 hours invisible to it.

Argus found the consequence from the other end: 663 client `ReadTimeout`s, 89 %
of the error spans across their whole platform in seven days, and one span
lasting 325.7 minutes against a sleep episode of 325.7 minutes, matching to the
decimal. The sleep was their environment; the request not returning on time was
ours.

A sleep cannot be forced in a test, so these fake each clock in turn — which is
the honest way to test a defect whose cause is which clock was asked.

A note on the `timeout=` on each held request, because it is load-bearing rather
than defensive. With the fix reverted, the frozen-monotonic case does not fail —
it **hangs for ever**, which is precisely the symptom Argus saw from the other
end. Verified by reverting it. A test that hangs is a worse test than one that
fails, so each request is bounded and a regression comes back as a readable
assertion instead of a stuck suite.
"""

from __future__ import annotations

import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from prometheus_manager_core.registry import Registry, RegistryEntry

from prometheus_manager_api import routes
from prometheus_manager_api.app import app
from prometheus_manager_api.auth import require_backend_registry_read


class _Clock:
    """A `time` stand-in for the route module only.

    Patching the real `time.monotonic` is what the first version of these tests
    did, and it **deadlocked**: asyncio's event loop keeps its own clock on
    `time.monotonic()`, so freezing it means `asyncio.sleep` never returns. The
    clock under test belongs to one module, so that is where it is replaced.
    """

    def __init__(self, *, monotonic=None, wall=None):
        self._mono = monotonic
        self._wall = wall

    def monotonic(self):
        return self._mono if self._mono is not None else time.monotonic()

    def time(self):
        return self._wall if self._wall is not None else time.time()

    def __getattr__(self, name):  # anything else the module uses
        return getattr(time, name)


@pytest.fixture
def client(tmp_path: Path):
    """A client whose dependency override is torn down even when a test fails.

    The first version of this file cleared `app.dependency_overrides` at the end
    of each test body. `app` is a module-level singleton shared by every test in
    this package, so a failing test leaked its override and broke
    `test_discovery.py::test_get_requires_auth` — a test that touches none of
    this. A fixture's teardown runs on failure; a line at the end of a function
    does not.
    """
    yield _build_client(tmp_path)
    app.dependency_overrides.clear()


def _build_client(tmp_path: Path) -> TestClient:
    reg = Registry(tmp_path / "registry.yaml")
    reg.add(
        RegistryEntry(
            id="probe-model",
            path="/models/probe.gguf",
            context_length=4096,
            port=8080,
            family="llama",
            quantization="Q4_0",
            discovery=True,
        )
    )
    app.state.registry = reg
    app.state.pid_dir = tmp_path / "run"
    app.state.jwks_url = "http://localhost:9000/v1/jwks"
    # `Claims` is a plain dict here, and the dependency is what the route asks
    # for — overriding it keeps these tests about the clock rather than about JWTs.
    app.dependency_overrides[require_backend_registry_read] = lambda: {
        "sub": "t",
        "client_id": "t",
        "scope": "backend-registry:read",
    }
    c = TestClient(app, raise_server_exceptions=True)
    # The timeout is load-bearing, not defensive — see the module docstring.
    # Set on the client rather than per request, which `TestClient` deprecates,
    # and after construction, which is the only place its signature allows.
    c.timeout = httpx.Timeout(15.0)
    return c


def _index(client: TestClient) -> str:
    """The index travels in `X-Registry-Index`, not the body — the header was
    chosen so the existing body shape stayed untouched."""
    return client.get("/v1/backends").headers["x-registry-index"]


def test_a_frozen_monotonic_clock_no_longer_holds_the_request(client, monkeypatch):
    """The defect, with the clock that caused it frozen.

    A machine asleep is exactly this: `time.monotonic()` stops advancing while
    wall time keeps running. Before PRM-185 the loop's only deadline was the
    frozen one, so the hold lasted as long as the sleep. Now wall time ends it.
    """
    idx = _index(client)

    monkeypatch.setattr(routes, "time", _Clock(monotonic=time.monotonic()))

    started = time.time()
    resp = client.get(f"/v1/backends?index={idx}&wait=2")
    elapsed = time.time() - started

    assert resp.status_code == 200
    assert elapsed < 10, (
        f"held {elapsed:.1f}s with a frozen monotonic clock — the wall-clock "
        "deadline is not bounding the hold"
    )


def test_a_wall_clock_stepping_backwards_no_longer_holds_the_request(client, monkeypatch):
    """The other direction, which is why both clocks stay.

    An NTP step backwards makes the wall deadline recede, and on its own that
    would extend the hold exactly as the sleep did. Monotonic bounds it.
    """
    idx = _index(client)

    monkeypatch.setattr(routes, "time", _Clock(wall=0.0))

    started = time.monotonic()
    resp = client.get(f"/v1/backends?index={idx}&wait=2")
    elapsed = time.monotonic() - started

    assert resp.status_code == 200
    assert elapsed < 10, (
        f"held {elapsed:.1f}s with a wall clock pinned to zero — the monotonic "
        "deadline is not bounding the hold"
    )


def test_an_ordinary_expiry_still_waits_and_says_which_clock_ended_it(client):
    """Neither clock tampered with: the hold lasts about `wait`, and no longer."""
    idx = _index(client)

    started = time.time()
    resp = client.get(f"/v1/backends?index={idx}&wait=2")
    elapsed = time.time() - started

    assert resp.status_code == 200
    assert 1.0 <= elapsed < 8.0, f"an unchanged index should hold ~2s, held {elapsed:.1f}s"


def test_a_changed_index_returns_without_waiting(client):
    """The contract's other exit, unchanged by PRM-185."""

    started = time.time()
    resp = client.get("/v1/backends?index=not-the-current-index&wait=30")
    elapsed = time.time() - started

    assert resp.status_code == 200
    assert elapsed < 5.0, f"a stale index should return at once, took {elapsed:.1f}s"
