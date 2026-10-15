"""PRM-242: the fingerprint covers what the client sent.

Implements: docs/roadmap.md — PRM-242.
Reported as `VRT-PRM-004` by Veritium, CC Axonium and synaptum, measured the
morning PRM-235 was deployed.

`_begin_idempotent` was handed `body.model_dump()` — the gateway's model with
its defaults filled in — so the fingerprint covered fields the client had
never sent. PRM-235 added `user` and `safety_identifier` to
`ChatCompletionRequest`, every chat request's fingerprint moved, and every key
stored before that deploy answered `409 idempotency-key-reuse` for the rest of
its 24-hour window: the gateway telling a caller it had misused its key when
the caller had done exactly the right thing, on the retry-after-a-crash path
that is the whole reason the key exists.

The acceptance criterion is Veritium's, negative control included.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

import os

import pytest

from prometheus_gateway import db, idempotency

_PATH = "/v1/chat/completions"


@pytest.fixture
async def store():
    """The records table, on this test's own database.

    `_isolated_gateway_db` sets `GATEWAY_DB_URL`; nothing here builds an app,
    so the engine has to be opened by hand.
    """
    db.init_db_engine(os.environ["GATEWAY_DB_URL"])
    await db.create_tables(db.get_engine())


class _RequestV1(BaseModel):
    """A request model before an optional field is added to it."""

    model_config = ConfigDict(extra="allow")

    model: str
    messages: list[dict]
    stream: bool = False


class _RequestV2(_RequestV1):
    """The same model one deploy later. This is what PRM-235 did."""

    user: str | None = None
    safety_identifier: str | None = None


_SENT = {"model": "small-model", "messages": [{"role": "user", "content": "hi"}]}


def test_an_optional_field_added_to_the_model_does_not_move_the_fingerprint():
    """The criterion: a key stored before the deploy still replays after it."""
    before = idempotency.fingerprint(_PATH, _RequestV1(**_SENT).model_dump(exclude_unset=True))
    after = idempotency.fingerprint(_PATH, _RequestV2(**_SENT).model_dump(exclude_unset=True))
    assert before == after


def test_the_negative_control_the_report_asked_for():
    """With the fingerprint over the full dump — what shipped — the same two
    requests disagree. Kept so the fix is demonstrated against the defect
    rather than asserted on its own."""
    before = idempotency.fingerprint(_PATH, _RequestV1(**_SENT).model_dump())
    after = idempotency.fingerprint(_PATH, _RequestV2(**_SENT).model_dump())
    assert before != after


def test_a_field_the_client_actually_sends_still_moves_the_fingerprint():
    """The other half, or the fix would be a hole: the fingerprint exists so a
    *different* request cannot be answered with the previous one's result."""
    with_user = idempotency.fingerprint(
        _PATH, _RequestV2(**_SENT, user="alice").model_dump(exclude_unset=True)
    )
    without = idempotency.fingerprint(_PATH, _RequestV2(**_SENT).model_dump(exclude_unset=True))
    assert with_user != without
    # And explicitly sending a value that equals the default is still a
    # different request as sent — the client said something about it.
    explicit_default = idempotency.fingerprint(
        _PATH, _RequestV2(**_SENT, stream=False).model_dump(exclude_unset=True)
    )
    assert explicit_default != without


def test_the_handlers_hand_over_the_model_not_a_dump():
    """Where the defect actually lived. The dump moved into
    `_begin_idempotent` so that a fifth endpoint cannot reintroduce it by
    copying one of the four that had it."""
    import inspect

    from prometheus_gateway import router

    source = inspect.getsource(router)
    assert "body.model_dump(exclude_unset=True)" in source
    assert ", body.model_dump(), resolution.model_key" not in source, (
        "a call site is fingerprinting the gateway's defaults again"
    )


# ── PRM-262: the transitional acceptance is gone ─────────────────────────────


async def test_the_transitional_acceptance_is_no_longer_a_parameter(store):
    """It was written as deletable after 2026-10-10 and this asserts the date
    was honoured rather than the comment left to rot.

    `also_accept` let a key stored before PRM-242 still replay, because that
    change moved what the fingerprint covers. Nothing it protected can still
    exist: the window is 24 hours and the newest pre-fix key on this deployment
    was seven days old when it went.
    """
    import inspect

    assert "also_accept" not in inspect.signature(idempotency.begin).parameters


async def test_a_genuinely_different_request_still_collides(store):
    """The half of the removed pair worth keeping. Two requests under one key
    must not replay each other, which is the fingerprint's whole job and the
    thing a compatibility shim is most likely to quietly widen."""
    from prometheus_gateway.models.schemas import ChatCompletionRequest

    first = ChatCompletionRequest(**_SENT)
    claim = await idempotency.begin("c2", "k", _PATH, first.model_dump(exclude_unset=True))
    assert isinstance(claim, idempotency.Claim)
    await idempotency.complete(claim, 200, {"ok": True}, "req-1")

    other = ChatCompletionRequest(
        model="small-model", messages=[{"role": "user", "content": "something else"}]
    )
    outcome = await idempotency.begin("c2", "k", _PATH, other.model_dump(exclude_unset=True))
    assert isinstance(outcome, idempotency.Refusal)
    assert outcome.kind == idempotency.KEY_REUSE
