"""Readiness: did the model answer, or did a port open? PRM-150.

Implements: docs/roadmap.md — PRM-150.

## The distinction this module exists for

`scanner._probe_health` calls a health endpoint and reports `ready` on a 200.
That is **liveness**, and it has been reported as readiness. The industry keeps
them apart and has for years:

  * Kubernetes: `livenessProbe`, `readinessProbe`, `startupProbe`.
  * The Open Inference Protocol (KServe v2, Triton): `/v2/health/live`,
    `/v2/health/ready`, **and `/v2/models/{name}/ready`** — per model, distinct
    from the server.

The gap is not theoretical here. Several engines answer `/health` before the
weights are loaded, because they load lazily on the first request: the process is
alive, the port is open, and the first real caller is the one that discovers the
model cannot serve. `hf-serve` will happily start with a `--task` the checkpoint
does not support and only fail when asked to do the task.

So a start is not finished when health answers. It is finished when the model has
answered one real request.

## What this catches, and what it does not

**Catches**: a task the checkpoint cannot perform, weights that fail a lazy load,
and a request schema the engine does not accept — which this project hit for real
with `laya`, whose options go in `criteria` and whose `instructions` is required.

**Does not catch**: a model that answers correctly-shaped nonsense. `laya`'s
miscalibrated checkpoint returned a structurally perfect response while its own
library logged that the confidences were unusable. No single request distinguishes
that from a good one; reading the engine's own log would, and that is not this.

Claiming otherwise would be the same defect this codebase keeps finding — a
signal that looks like it means more than it does.

## Image generation is deliberately excluded

Every other probe below costs milliseconds. Generating an image costs seconds of
GPU even at one step, on every start, and the failure it would catch is the one
`/health` on an image server already catches. Paying that on each launch to learn
nothing new is not a trade worth making; `image` returns "skipped" and says so
rather than returning a pass it did not earn.

## Not billed

The probe serves no caller, so it is never metered. PRM-142 established the rule
in the other direction — a request the backend refused is not a request served —
and this is the same rule: work nobody asked for is not work anybody pays for.
The gateway is not involved at all; this talks to the engine directly.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import httpx

from .registry import MODALITIES, RegistryEntry

# One probe is a real request, so it gets a real timeout — a lazily-loading model
# may take a while on its first call even after health has gone green.
_PROBE_TIMEOUT_S = 60.0

# A lazy load can still be in flight when health first answers, so a single
# failure is not a verdict. Three tries over ~6s, then it is.
_ATTEMPTS = 3
_RETRY_DELAY_S = 3.0

# The shortest input that is still a real one. Deliberately not empty: some
# engines special-case an empty input and would pass a probe they should fail.
_TEXT = "ok"


@dataclass(frozen=True)
class Readiness:
    """The verdict, and why — the "why" is the point.

    `skipped` is a third outcome, not a pass: it means no probe was run, and a
    caller must not read it as evidence the model works.
    """

    ready: bool
    skipped: bool
    detail: str

    @property
    def failed(self) -> bool:
        return not self.ready and not self.skipped


def _probe_body(entry: RegistryEntry) -> tuple[str, dict[str, Any]] | None:
    """(path, body) for one real request, or None when the modality is skipped.

    Keyed on modality, with the pass-through modalities keyed on the backend too,
    because there the request shape belongs to the engine rather than to us —
    the same split the gateway's `payload_schema` publishes (PRM-144).
    """
    name = entry.model_slug or entry.model_id or entry.id
    modality = entry.modality

    if modality in ("text", "vision"):
        # `max_tokens: 1` — the cheapest generation that still proves the model
        # loaded and produced a token.
        return "/v1/chat/completions", {
            "model": name,
            "messages": [{"role": "user", "content": _TEXT}],
            "max_tokens": 1,
        }
    if modality == "embedding":
        return "/v1/embeddings", {"model": name, "input": _TEXT}
    if modality == "rerank":
        # Two documents, because a reranker with one has nothing to order and
        # some implementations short-circuit.
        #
        # PRM-179: and keyed on the backend, which is what the docstring above
        # already said about pass-through modalities and had never needed to be
        # true of `rerank` — it had exactly one engine. TEI serves `/rerank` with
        # `texts`; llama.cpp serves `/v1/rerank` with `documents`. Probing the
        # wrong one gets a 404 and reports a working engine as not ready, which is
        # exactly what the first TEI instance did.
        #
        # The path is stated here *and* in the gateway's `rerank_dialects`,
        # because a manager that imports the gateway would be the wrong
        # dependency for two strings. `test_rerank_dialects.py` imports both and
        # fails if they stop agreeing.
        if entry.backend == "tei":
            return "/rerank", {"query": _TEXT, "texts": [_TEXT, "no"]}
        return "/v1/rerank", {"model": name, "query": _TEXT, "documents": [_TEXT, "no"]}
    if modality == "classification":
        # The same path on both engines, and the same body: one text in, scored
        # classes out. Nothing to key on here.
        return "/predict", {"inputs": _TEXT}
    if modality == "zero_shot":
        # PRM-184: and this one *is* keyed on the engine, for a reason that only
        # measuring showed. TEI answers hf-serve's body with **200** — it ignores
        # `parameters` entirely rather than refusing it — so this probe passed
        # against TEI while the `candidate_labels` were thrown away. A probe that
        # proves "the engine responds" and not "the engine does this modality" is
        # the shape of a failure that looks like a success, which is the defect
        # this project keeps meeting under its own name.
        #
        # So for TEI the probe sends what a zero-shot request to TEI actually is:
        # a (premise, hypothesis) pair, whose answer depends on the hypothesis.
        if entry.backend == "tei":
            return "/predict", {"inputs": [[_TEXT, "this is a sentence"]]}
        return "/predict", {
            "inputs": _TEXT,
            "parameters": {"candidate_labels": ["yes", "no"]},
        }
    if modality == "nli":
        # The pair is the whole modality: a probe sending one text would pass
        # against a model that cannot do this at all, which is the failure
        # PRM-184 described one branch above.
        return "/predict", {"inputs": [[_TEXT, "this is a sentence"]]}
    if modality == "typed_decision":
        # laya's shape, and it is the shape this project got wrong first time:
        # options live in `criteria`, and `instructions` is required.
        return "/v1/systemone", {
            "state": {"text": _TEXT},
            "questions": {
                "probe": {
                    "type": "choice",
                    "instructions": "Is this a probe?",
                    "criteria": ["yes", "no"],
                }
            },
        }
    if modality == "image":
        return None  # see the module docstring
    return None


def check(entry: RegistryEntry, host: str, port: int) -> Readiness:
    """Send one real request to a started engine and report whether it answered.

    Never raises: a readiness probe that throws would turn "we could not tell"
    into a failed start, and those are different (RM-98).
    """
    if entry.modality not in MODALITIES:
        return Readiness(
            False,
            True,
            f"modality {entry.modality!r} is not one this manager knows, so no probe exists",
        )

    probe = _probe_body(entry)
    if probe is None:
        return Readiness(
            False,
            True,
            f"no readiness probe for modality {entry.modality!r} — see readiness.py for why",
        )

    path, body = probe
    url = f"http://{host}:{port}{path}"
    last = ""
    for attempt in range(1, _ATTEMPTS + 1):
        try:
            resp = httpx.post(url, json=body, timeout=_PROBE_TIMEOUT_S)
        except Exception as exc:  # noqa: BLE001 — any transport failure is a failure
            last = f"{type(exc).__name__}: {exc}"
        else:
            if 200 <= resp.status_code < 300:
                return Readiness(
                    True,
                    False,
                    f"answered {path} with {resp.status_code} on attempt {attempt}",
                )
            # The engine's own words, truncated — this is what an operator reads
            # to find out what is actually wrong.
            last = f"HTTP {resp.status_code} from {path}: {resp.text[:400]}"
        if attempt < _ATTEMPTS:
            time.sleep(_RETRY_DELAY_S)

    return Readiness(
        False,
        False,
        f"the engine is running and did not serve {entry.modality} after "
        f"{_ATTEMPTS} attempts — {last}",
    )
