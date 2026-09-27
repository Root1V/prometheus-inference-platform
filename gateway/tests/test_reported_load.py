"""Least-loaded routing reads the backend's own load — PRM-156.

The architecture review named the defect: `BackendPool._in_flight` is a plain
dict, so with several gateway replicas each sees only the requests it sent, and
least-loaded routing silently degrades to per-replica balancing with nothing to
indicate it.

The review's proposed fix — move the counter to Redis — is **not** what this does,
and the reason matters. Envoy, Linkerd and Finagle all keep least-request counts
proxy-local and mitigate the partial view with power-of-two-choices, because a
counter on the request path costs a round trip and leaks for ever when a process
dies between acquire and release. Here there is something better than either: the
engine already reports `is_processing` per slot on the `/slots` route the health
monitor already polls. That is ground truth about the backend, shared by
construction across any number of replicas, and impossible to leak because it is
not the gateway's to leak.
"""

from __future__ import annotations

import pytest

from prometheus_gateway.models.backends import BackendPool


@pytest.fixture
def pool() -> BackendPool:
    return BackendPool()


# ── The signal is the larger of the two, because they fail oppositely ─────────


def test_with_no_report_the_local_count_is_the_signal(pool: BackendPool):
    """sd.cpp has no /slots route at all — the old behaviour for exactly the
    backends that cannot do better."""
    pool.acquire("b")
    pool.acquire("b")
    assert pool.reported_busy("b") is None
    assert pool.load_ratio("b") == 2.0


def test_a_backend_reporting_more_than_this_process_sent_wins(pool: BackendPool):
    """The multi-replica case, which is the whole point: this process sent one
    request and the backend is working on four, so three came from elsewhere."""
    pool.acquire("b")
    pool.set_reported_busy("b", 4)
    assert pool.load_ratio("b") == 4.0


def test_a_process_that_just_dispatched_outruns_a_stale_report(pool: BackendPool):
    """The other direction. The report is up to one poll interval old, so a burst
    this process just sent is invisible to it — and taking the larger means the
    replica stops choosing this backend without waiting for the next poll."""
    pool.set_reported_busy("b", 0)
    for _ in range(3):
        pool.acquire("b")
    assert pool.load_ratio("b") == 3.0


def test_a_backend_that_does_not_report_is_not_treated_as_idle(pool: BackendPool):
    """`None` and `0` are different, and collapsing them would make a silent
    backend look like the emptiest one in the fleet — which is where every
    request would then go."""
    pool.set_reported_busy("quiet", None)
    pool.set_reported_busy("idle", 0)
    assert pool.reported_busy("quiet") is None
    assert pool.reported_busy("idle") == 0


def test_clearing_a_report_falls_back_to_the_local_count(pool: BackendPool):
    """A backend that stops answering /slots must not keep a stale busy figure."""
    pool.acquire("b")
    pool.set_reported_busy("b", 9)
    assert pool.load_ratio("b") == 9.0
    pool.set_reported_busy("b", None)
    assert pool.load_ratio("b") == 1.0


# ── Capacity still normalises it, which is what makes nodes comparable ───────


def test_the_reported_count_is_divided_by_capacity_too(pool: BackendPool):
    """RM-72's point, unchanged: least loaded means least loaded *relative to what
    the backend can take*, or a laptop and a workstation get the same share."""
    pool.set_slot_capacity("laptop", 1)
    pool.set_slot_capacity("workstation", 4)
    pool.set_reported_busy("laptop", 1)
    pool.set_reported_busy("workstation", 2)

    assert pool.load_ratio("laptop") == 1.0
    assert pool.load_ratio("workstation") == 0.5
    assert pool.load_ratio("workstation") < pool.load_ratio("laptop")


# ── The defect, stated as the test that would have caught it ──────────────────


def test_two_replicas_agree_on_which_backend_is_busiest(pool: BackendPool):
    """Two pools stand for two gateway processes, each having sent one request to
    a different backend. Before PRM-156 each would rank its *own* target as the
    busy one and the other as idle, so both would keep choosing the backend the
    other was loading. With the backend's own report they agree.
    """
    replica_a, replica_b = BackendPool(), BackendPool()
    replica_a.acquire("x")  # A sent one to x
    replica_b.acquire("y")  # B sent one to y

    # What the backends themselves report a moment later: x is carrying three
    # requests from across the fleet, y is carrying one.
    for replica in (replica_a, replica_b):
        replica.set_reported_busy("x", 3)
        replica.set_reported_busy("y", 1)

    ranking_a = sorted(("x", "y"), key=replica_a.load_ratio)
    ranking_b = sorted(("x", "y"), key=replica_b.load_ratio)
    assert ranking_a == ranking_b == ["y", "x"], (
        "two replicas disagreed about which backend is busier, which is the "
        "per-replica balancing PRM-156 removes"
    )


def test_release_lowers_the_local_half_only(pool: BackendPool):
    """A crashed process cannot leave a stale shared count, because the shared
    half is not ours — the leak a Redis counter would have introduced."""
    pool.acquire("b")
    pool.set_reported_busy("b", 5)
    pool.release("b")
    assert pool.in_flight("b") == 0
    assert pool.load_ratio("b") == 5.0


# ── The monitor reads it from the response it was already fetching ────────────


async def test_the_monitor_extracts_the_busy_count_from_slots():
    """Shape taken from a live llama.cpp `/slots`: four slots, two working.

    The capacity half of this response was already being read; the busy half was
    being thrown away while the gateway counted its own requests instead.
    """
    import httpx
    import respx

    from prometheus_gateway.health_monitor import BackendHealthMonitor

    base = "http://127.0.0.1:18201"
    slots = [
        {"id": 0, "is_processing": True, "id_task": 7},
        {"id": 1, "is_processing": False, "id_task": -1},
        {"id": 2, "is_processing": True, "id_task": 9},
        {"id": 3, "is_processing": False, "id_task": -1},
    ]

    monitor = BackendHealthMonitor(registry=None, interval_s=10)
    async with httpx.AsyncClient() as client:
        monitor._client = client
        with respx.mock:
            respx.get(f"{base}/slots").mock(return_value=httpx.Response(200, json=slots))
            await monitor._read_slots(base)

    assert monitor._slots[base] == 4, "capacity"
    assert monitor._busy[base] == 2, "busy — the half that was being discarded"


async def test_a_backend_with_no_slots_route_reports_neither():
    """sd.cpp. Absent from both figures rather than recorded as idle."""
    import httpx
    import respx

    from prometheus_gateway.health_monitor import BackendHealthMonitor

    base = "http://127.0.0.1:18202"
    monitor = BackendHealthMonitor(registry=None, interval_s=10)
    async with httpx.AsyncClient() as client:
        monitor._client = client
        with respx.mock:
            respx.get(f"{base}/slots").mock(return_value=httpx.Response(404))
            await monitor._read_slots(base)

    assert base not in monitor._slots
    assert base not in monitor._busy
