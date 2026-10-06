"""PRM-196 — an abandoned request lets go of the engine.

repo2deck stopped an exploration and found four slots busy for minutes,
generating answers for clients that no longer existed. The streamed path never
had this: a hung-up client raises `GeneratorExit` at a `yield`, the `finally`
closes the upstream connection, and llama.cpp sees the socket go. The
non-streamed path simply awaits a complete response, and nothing in that await
observes the caller at all.

Measured against the live stack before any of this was written — the same
request, the client cut at 4 s, counting busy slots on the engine:

    non-streaming   busy at +2s, +10s, +25s;  free at +45s
    streaming       free at +2s

Both halves of the fix were measured too, because either could have been false:
`request.is_disconnected()` does fire on a non-streamed request whose body is
already consumed (3.03 s after a cut at 3 s), and cancelling the httpx task
really does release the *engine* — four concurrent generations showed
`requests_processing=4, deferred=1`, and cancelling them left zero deferred with
a fifth request served at once.

Cancelling is not the same as not billing. RM-87 says a caller who walks away
mid-generation is the case most worth charging for; that is why `ClientGone` is
raised rather than swallowed, and why the accounting goes through `_detach`.
"""

from __future__ import annotations

import asyncio

import pytest

from prometheus_gateway.router import (
    ClientGone,
    _forward_or_abandon,
    _listen_for_disconnect,
)


class _FakeRequest:
    """An ASGI receive channel, which is what the listener actually consumes.

    Deliberately not a `is_disconnected()` stub: that method is the thing PRM-196
    found unusable here, and a double that implements it would test a mechanism
    the gateway does not use.
    """

    def __init__(self, disconnect_after: float | None = None) -> None:
        self._after = disconnect_after
        self.receives = 0

    async def receive(self) -> dict[str, str]:
        self.receives += 1
        if self._after is None:
            await asyncio.sleep(3600)  # a caller who never leaves
        await asyncio.sleep(self._after)
        return {"type": "http.disconnect"}


async def _slow(seconds: float, value: str = "done") -> str:
    await asyncio.sleep(seconds)
    return value


# ── the request completes ──────────────────────────────────────────────────


async def test_a_finished_request_returns_its_result():
    req = _FakeRequest()
    assert await _forward_or_abandon(req, _slow(0.05)) == "done"


async def test_an_exception_from_the_backend_still_surfaces():
    """The guard must not swallow a real failure into a disconnect."""

    async def boom() -> str:
        raise RuntimeError("backend exploded")

    with pytest.raises(RuntimeError, match="backend exploded"):
        await _forward_or_abandon(_FakeRequest(), boom())


async def test_the_listener_does_not_outlive_the_request():
    """A listener left awaiting `receive()` per request is a leak that only
    shows under load — and it would keep consuming messages for a connection
    the handler has finished with."""
    req = _FakeRequest()
    await _forward_or_abandon(req, _slow(0.05))
    receives_at_return = req.receives
    await asyncio.sleep(0.08)
    assert req.receives == receives_at_return


# ── the caller hangs up ────────────────────────────────────────────────────


async def test_a_hung_up_caller_raises_client_gone():
    with pytest.raises(ClientGone):
        await _forward_or_abandon(_FakeRequest(disconnect_after=0.02), _slow(5))


async def test_the_backend_call_is_actually_cancelled():
    """The point of the whole change: the engine must stop, not merely be
    ignored. Pinned by a task that records whether it ever finished."""
    finished = False

    async def work() -> str:
        nonlocal finished
        await asyncio.sleep(5)
        finished = True
        return "done"

    with pytest.raises(ClientGone):
        await _forward_or_abandon(_FakeRequest(disconnect_after=0.02), work())
    await asyncio.sleep(0.05)
    assert not finished, "the backend call survived the caller leaving"


async def test_it_gives_up_promptly_rather_than_at_the_end():
    """Letting go only when the generation finishes would be no fix at all."""
    started = asyncio.get_running_loop().time()
    with pytest.raises(ClientGone):
        await _forward_or_abandon(_FakeRequest(disconnect_after=0.02), _slow(10))
    assert asyncio.get_running_loop().time() - started < 1.0


async def test_a_caller_who_leaves_after_the_answer_still_gets_the_result():
    """The race has an order, and this is the side that must not become a
    disconnect: the backend won, so there is a billable answer."""
    req = _FakeRequest(disconnect_after=5)
    assert await _forward_or_abandon(req, _slow(0.02)) == "done"


# ── the server cancels us ──────────────────────────────────────────────────


async def test_an_outright_cancellation_also_releases_the_backend():
    """A shutdown or a timeout cancels the handler. Leaving the engine running
    would be the same leak by a different door."""
    finished = False

    async def work() -> str:
        nonlocal finished
        await asyncio.sleep(5)
        finished = True
        return "done"

    outer = asyncio.ensure_future(_forward_or_abandon(_FakeRequest(), work()))
    await asyncio.sleep(0.05)
    outer.cancel()
    with pytest.raises(asyncio.CancelledError):
        await outer
    await asyncio.sleep(0.05)
    assert not finished


# ── the wiring, so a revert is noticed ─────────────────────────────────────


def test_the_image_path_is_guarded():
    """PRM-198. Diffusion is the longest blocking call this gateway makes, so
    it is where an abandoned request wastes most — and the one route whose
    `except Exception` catch-all would have swallowed `ClientGone` into an
    upstream error, opening the circuit breaker over a caller hanging up."""
    import inspect

    from prometheus_gateway import router

    src = inspect.getsource(router)
    i = src.index('"/v1/images/generations",\n                    body.to_backend_payload(),')
    window = src[max(0, i - 700) : i]
    assert "_forward_or_abandon" in window, (
        "the image forward is no longer raced against the caller"
    )


def test_the_image_guard_precedes_the_catch_all():
    """Order is the correctness here: `except Exception` sits below and would
    report an abandoned request as a backend failure."""
    import inspect

    from prometheus_gateway import router

    src = inspect.getsource(router)
    gone = src.index("except ClientGone:", src.index("images_generations.forwarding"))
    catch_all = src.index("except Exception as exc:", src.index("images_generations.forwarding"))
    assert gone < catch_all


def test_an_abandoned_image_releases_its_budget_reservation():
    """RM-60 debits the monthly cap before forwarding. Returning without
    settling leaves the caller charged for an image they never received until
    the period rolls over."""
    import inspect

    from prometheus_gateway import router

    src = inspect.getsource(router)
    i = src.index("images_generations.client_disconnected")
    block = src[i : i + 2000]
    assert "settle(" in block
    assert "0.0," in block, "an image is all-or-nothing; nothing was delivered"


def test_the_chat_path_is_guarded():
    """If someone unwraps this call the tests above keep passing, because they
    exercise the helper and not the route. This reads the source so that the
    revert is what fails."""
    import inspect

    from prometheus_gateway import router

    src = inspect.getsource(router)
    i = src.index('"/v1/chat/completions",\n                                payload,')
    window = src[max(0, i - 600) : i]
    assert "_forward_or_abandon" in window, (
        "the non-streaming chat forward is no longer raced against the caller"
    )


def test_client_gone_is_not_an_http_exception():
    """It must not be mistaken for something the caller should be told about —
    there is nobody left to tell."""
    from fastapi import HTTPException

    assert not issubclass(ClientGone, HTTPException)


# ── the primitive, which is the part that was wrong twice ──────────────────


async def test_the_listener_consumes_messages_until_the_disconnect():
    """It discards whatever arrives while waiting, which is why the body must
    already be parsed. An earlier attempt put this in an outermost middleware
    and it swallowed the body — requests stopped reaching the backend at all."""
    seen: list[dict[str, str]] = []

    class _Noisy:
        def __init__(self) -> None:
            self.queue = [
                {"type": "http.request"},
                {"type": "http.request"},
                {"type": "http.disconnect"},
            ]

        async def receive(self) -> dict[str, str]:
            m = self.queue.pop(0)
            seen.append(m)
            return m

    await _listen_for_disconnect(_Noisy())
    assert [m["type"] for m in seen] == [
        "http.request",
        "http.request",
        "http.disconnect",
    ]


async def test_is_disconnected_is_not_what_this_uses():
    """The regression guard for the finding itself. `is_disconnected()` is
    blind behind `BaseHTTPMiddleware`, which this app has two of; a future
    simplification back to it would reintroduce the bug and pass every other
    test here, because a double would answer it happily."""
    import inspect

    from prometheus_gateway import router

    src = inspect.getsource(router._listen_for_disconnect)
    assert "is_disconnected" not in src.split('"""')[-1], (
        "the listener must await receive(), not poll is_disconnected()"
    )


def test_the_route_returns_a_status_not_none():
    """vLLM's #42794: returning None from a cancelled handler makes FastAPI
    serialise a 200 with a `null` body — a silent success for a request that
    failed. 499 is nginx's convention for a caller who left first."""
    import inspect

    from prometheus_gateway import router

    src = inspect.getsource(router)
    i = src.index("except ClientGone:")
    block = src[i : i + 4000]
    assert "Response(status_code=499)" in block
    assert "return None" not in block
