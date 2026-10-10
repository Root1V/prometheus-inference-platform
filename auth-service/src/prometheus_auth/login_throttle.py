"""PRM-250: a budget for failed sign-ins.

Implements: docs/roadmap.md — PRM-250.

The service is configured with `auth_rate_limit_rpm`, a slowapi `Limiter` is
built from it, and no route ever applied it — slowapi's `default_limits` only
bind through `@limiter.limit` or `SlowAPIMiddleware`, and this service has
neither. Measured against the running deployment: fifteen wrong passwords in a
row, all answered 401 at full speed, against a service that believed it allowed
ten a minute.

Failures are counted, successes are not. A working integration refreshes its
token on a schedule and never fails; an attacker only fails. Counting attempts
instead would have to be generous enough for the busiest legitimate client,
which is far too generous to stop anything.

Two buckets, because the two attacks look different:

  * by identity — one account, many guesses;
  * by source address — many accounts, one guess each, which is what
    enumeration looks like.

The address bucket has the larger budget because the dashboard's sign-ins all
arrive from the gateway's address: the gateway proxies them server-side and
does not forward the caller's IP, so every operator of this platform shares one
bucket. Failures-only is what keeps that workable — people who know their
password do not spend the budget.

In process, not in Redis: this counter is per worker, so N workers allow N
times the budget. For a self-hosted service that is the difference between an
attacker getting ten thousand guesses and getting a few dozen, and the honest
alternative — a round trip to Redis on the path that exists to be cheap — buys
precision this does not need. The limitation is real and stated rather than
hidden.
"""

from __future__ import annotations

import time
from collections import deque

from fastapi import Request


def trusted_proxies(raw: str) -> frozenset[str]:
    """Parse the configured list once, at startup."""
    return frozenset(part.strip() for part in raw.split(",") if part.strip())


def client_address(request: Request, trusted: frozenset[str]) -> str:
    """The address whose budget this attempt spends — PRM-251.

    The socket peer, unless the peer is a proxy this service was explicitly
    told to believe, in which case the leftmost `X-Forwarded-For` entry.

    Believing the header unconditionally would be worse than not forwarding at
    all: a caller could put a different address on every request and never run
    out of budget. Believing it only from a listed peer means the header is
    only as trustworthy as that peer, and the gateway — the only one listed on
    this deployment — sets it from the socket it is actually serving and
    discards whatever the caller sent.
    """
    peer = request.client.host if request.client else "unknown"
    if peer not in trusted:
        return peer
    forwarded = request.headers.get("x-forwarded-for", "")
    # Leftmost is the original caller; a single trusted hop writes only that.
    first = forwarded.split(",")[0].strip()
    return first or peer


class LoginThrottle:
    """Sliding-window failure counter, keyed by arbitrary strings."""

    def __init__(self, *, max_failures: int, window_seconds: int) -> None:
        self._max = max_failures
        self._window = window_seconds
        self._failures: dict[str, deque[float]] = {}

    def _recent(self, key: str, now: float) -> deque[float]:
        hits = self._failures.get(key)
        if hits is None:
            hits = deque()
            self._failures[key] = hits
        cutoff = now - self._window
        while hits and hits[0] <= cutoff:
            hits.popleft()
        return hits

    def retry_after(self, key: str) -> int | None:
        """Seconds to wait, or None while the key still has budget left.

        Checked before the password is looked at, so a refused caller costs
        nothing — which is the point of refusing them.
        """
        now = time.monotonic()
        hits = self._recent(key, now)
        if len(hits) < self._max:
            return None
        return int(self._window - (now - hits[0])) + 1

    def record_failure(self, key: str) -> None:
        now = time.monotonic()
        self._recent(key, now).append(now)

    def clear(self, key: str) -> None:
        """Forget a key's failures — called when a sign-in succeeds.

        Someone who mistypes a password four times and then gets it right has
        not spent anything; the budget is for attempts that never land.
        """
        self._failures.pop(key, None)
