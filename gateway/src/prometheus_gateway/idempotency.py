"""Client-supplied idempotency keys.

Implements: docs/roadmap.md — RM-78

A retry is otherwise always a new, billable generation, so an SDK can only
retry where the platform proves nothing ran — which excludes the commonest
case, retrying after its own timeout. With a key, a replay returns the first
result instead of generating again.

This is not about gateway-internal failover, which never double-bills: usage is
recorded once per returned response, never per attempt, so an aborted attempt
writes nothing. What that wastes is compute on the abandoned instance.

Shape follows what OpenAI and Anthropic already do — an `Idempotency-Key`
header, a 24h window, replay the stored result — so an SDK written against
either works here without a second concept.
"""

from __future__ import annotations

import hashlib
import json
from math import ceil
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select

from . import db
from .telemetry import get_logger

logger = get_logger(__name__)

HEADER = "Idempotency-Key"

# How long a key answers for. 24h matches OpenAI and Anthropic, so an SDK's
# own key-retention guidance carries over unchanged.
_WINDOW = timedelta(hours=24)

# What to suggest waiting when nothing has been observed for this model yet —
# the latency counters live in process memory and reset with the gateway. Short
# on purpose: re-asking costs a 409, not a generation.
_DEFAULT_RETRY_AFTER_S = 2

# Bodies above this aren't retained: chat and embeddings are kilobytes, but an
# image response is megabytes of base64 and `n` multiplies it. The key is still
# recorded, so a replay can say the original succeeded rather than silently
# regenerating — losing the guarantee quietly would be worse than either.
_MAX_STORED_BODY = 1024 * 1024

_IN_PROGRESS = "in_progress"
_COMPLETED = "completed"

# Keys longer than the column can't be stored, and truncating would make two
# different keys collide — which is worse than refusing one.
MAX_KEY_LENGTH = 255


# RM-82: a stored stream is kept as its raw SSE text under this key. The store
# is JSON and an SSE body is not, so wrapping it keeps one storage path rather
# than a second column plus a discriminator.
_STREAM_FIELD = "__sse_stream__"


def wrap_stream(raw: str) -> dict[str, str]:
    """Package an SSE body for storage."""
    return {_STREAM_FIELD: raw}


def unwrap_stream(body: Any) -> str | None:
    """The SSE body a Replay carries, or None if it was an ordinary response."""
    if isinstance(body, dict):
        raw = body.get(_STREAM_FIELD)
        if isinstance(raw, str):
            return raw
    return None


@dataclass(frozen=True)
class Replay:
    """A stored result to return instead of generating again."""

    status_code: int
    body: Any
    #: PRM-100: the request id of the generation being replayed. A replay is
    #: given its own request id, and by design records no usage — so the id its
    #: caller holds leads to no row at all. Handing back the original's means
    #: the link exists at the moment it is known, without a lookup table and
    #: without asking the caller to correlate anything later.
    original_request_id: str | None = None


@dataclass(frozen=True)
class Refusal:
    """The key can't be honoured, and the caller must be told why rather than
    have the request quietly treated as new.

    `kind` carries the reason as a stable machine value, because the four
    reasons need opposite handling — one resolves by waiting, the rest never
    resolve by retrying — and telling them apart by matching on prose means a
    reworded message breaks a client in silence. The router maps it to the
    problem `type` and status.
    """

    kind: str
    detail: str
    #: Seconds to wait before retrying — only ever set for IN_PROGRESS, the one
    #: refusal that resolves by waiting.
    retry_after_seconds: int | None = None


# A malformed key was never a conflict with anything — it's a bad request, and
# a client branching on "conflict" would conclude it had repeated a call.
INVALID_KEY = "invalid-idempotency-key"
# Same key, different request. The caller reused it; retrying never helps.
# Covers a different endpoint too: the fingerprint spans path and payload.
KEY_REUSE = "idempotency-key-reuse"
# The first call is still running. This one *does* resolve by waiting.
IN_PROGRESS = "idempotency-in-progress"
# It succeeded, but the response was too large to keep, so there is nothing to
# replay. Retrying would generate and bill again.
NOT_RETAINED = "idempotency-response-not-retained"


@dataclass(frozen=True)
class Claim:
    """This request owns the key and should proceed."""

    client_id: str
    key: str


def fingerprint(path: str, payload: Any) -> str:
    """Identify the request a key was first used for.

    Without this, reusing a key with different parameters would be answered
    with the previous request's result — a wrong answer rather than a duplicate
    one. Sorted keys so an SDK that serialises dicts in a different order on
    its retry still matches.
    """
    material = json.dumps({"path": path, "payload": payload}, sort_keys=True, default=str)
    return hashlib.sha256(material.encode()).hexdigest()


async def begin(
    client_id: str, key: str, path: str, payload: Any, model_key: str | None = None
) -> Replay | Refusal | Claim:
    """Claim *key* for this request, or report what already holds it.

    `model_key` is the resolved model, used only to estimate how much longer an
    in-flight request has left. Optional, because the answer is a hint.
    """
    if len(key) > MAX_KEY_LENGTH:
        return Refusal(INVALID_KEY, f"{HEADER} must be at most {MAX_KEY_LENGTH} characters.")

    want = fingerprint(path, payload)
    cutoff = datetime.now(timezone.utc) - _WINDOW

    async with db.get_session_factory()() as session:
        row = await session.get(db.IdempotencyRecord, (client_id, key))

        if row is not None and _expired(row, cutoff):
            # Past the window this key means nothing; let it be claimed afresh
            # rather than answering with a result the client no longer expects.
            await session.delete(row)
            await session.commit()
            row = None

        if row is not None:
            if row.fingerprint != want:
                return Refusal(
                    KEY_REUSE,
                    f"{HEADER} {key!r} was already used for a different request. "
                    "A key identifies one request; reuse it only to retry that same one.",
                )
            if row.state == _IN_PROGRESS:
                wait = await _estimated_wait(row, model_key)
                return Refusal(
                    IN_PROGRESS,
                    f"A request with {HEADER} {key!r} is still running. Retrying while the "
                    "first is in flight would generate twice — wait for it to finish.",
                    retry_after_seconds=wait,
                )
            if row.response_body is None:
                return Refusal(
                    NOT_RETAINED,
                    f"The request with {HEADER} {key!r} already succeeded, but its response "
                    "was too large to retain, so it can't be replayed. Do not retry it.",
                )
            return Replay(
                status_code=row.status_code or 200,
                body=json.loads(row.response_body),
                original_request_id=row.request_id,
            )

        session.add(
            db.IdempotencyRecord(client_id=client_id, key=key, fingerprint=want, state=_IN_PROGRESS)
        )
        try:
            await session.commit()
        except Exception:
            # Another request inserted the same key between the read and the
            # write. That one owns it; this is the concurrent-duplicate case.
            await session.rollback()
            return Refusal(
                IN_PROGRESS,
                f"A request with {HEADER} {key!r} is still running. Retrying while the "
                "first is in flight would generate twice — wait for it to finish.",
            )
    return Claim(client_id=client_id, key=key)


async def complete(
    claim: Claim, status_code: int, body: Any, request_id: str | None = None
) -> None:
    """Store the result so a replay returns it instead of generating again."""
    try:
        serialised: str | None = json.dumps(body)
        if serialised is not None and len(serialised) > _MAX_STORED_BODY:
            serialised = None
    except (TypeError, ValueError):
        serialised = None

    async with db.get_session_factory()() as session:
        row = await session.get(db.IdempotencyRecord, (claim.client_id, claim.key))
        if row is None:
            return
        row.state = _COMPLETED
        row.status_code = status_code
        row.response_body = serialised
        if request_id:
            row.request_id = request_id
        await session.commit()


async def release(claim: Claim) -> None:
    """Give the key back after a request that produced no result.

    A failed request must not hold its key for the whole window: the client's
    retry is exactly what should be allowed through, and there is nothing
    stored to replay to it.
    """
    async with db.get_session_factory()() as session:
        row = await session.get(db.IdempotencyRecord, (claim.client_id, claim.key))
        if row is not None and row.state == _IN_PROGRESS:
            await session.delete(row)
            await session.commit()


async def purge_expired() -> int:
    """Drop records past the window. Returns how many went.

    Filtered in Python rather than SQL because SQLite hands datetimes back
    without a timezone while Postgres keeps one, so a single comparison can't
    be written that works on both. The table stays small — a row only exists
    for a request that opted into a key — so reading it is cheap.
    """
    cutoff = datetime.now(timezone.utc) - _WINDOW
    async with db.get_session_factory()() as session:
        rows = (await session.execute(select(db.IdempotencyRecord))).scalars().all()
        stale = [r for r in rows if _expired(r, cutoff)]
        for row in stale:
            await session.delete(row)
        if stale:
            await session.commit()
    return len(stale)


async def _estimated_wait(row: db.IdempotencyRecord, model_key: str | None) -> int:
    """How much longer the in-flight request probably has — RM-81.

    Derived from what this model actually does: its observed p95 latency minus
    how long this request has already been running. Deliberately not the
    backend timeout, which is an upper bound rather than an estimate and would
    have a client wait ten minutes for something that usually takes two seconds.
    """
    from .telemetry import metrics_store

    p95_ms = await metrics_store.model_latency_p95_ms(model_key) if model_key else None
    if not p95_ms:
        return _DEFAULT_RETRY_AFTER_S
    started = row.created_at
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    elapsed = (datetime.now(timezone.utc) - started).total_seconds()
    return max(1, ceil(p95_ms / 1000 - elapsed))


def _expired(row: db.IdempotencyRecord, cutoff: datetime) -> bool:
    created = row.created_at
    if created.tzinfo is None:
        # SQLite hands back naive datetimes; compare in UTC either way.
        created = created.replace(tzinfo=timezone.utc)
    return created < cutoff
