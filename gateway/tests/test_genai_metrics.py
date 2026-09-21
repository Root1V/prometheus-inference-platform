"""The four GenAI metrics the gateway emits — docs/roadmap.md PRM-131.

Every assertion runs in a **subprocess**. Not fastidiousness: OpenTelemetry's
meter provider can be set once per process, and instruments bind to the first
real provider they see. The rest of this suite builds the app, which calls
`configure_metrics()`, so a provider carrying an in-memory reader could never
be installed afterwards — the assertions would run against a provider that
exports nowhere and pass on an empty collection. A fresh interpreter also lets
each case reproduce production's import order exactly, which is the thing one
of these tests is about.
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap

_PRELUDE = """
import json, asyncio, sys

# Production order: the router module is imported while the app is still being
# built, and the meter provider is installed afterwards, inside create_app().
import prometheus_gateway.router as R
assert not [m for m in sys.modules if m.startswith("argus")], (
    "argus_semconv was imported at router import time — its meter would be "
    "created before the provider exists and lose its scope attributes"
)

from opentelemetry import metrics as om
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
reader = InMemoryMetricReader()
om.set_meter_provider(MeterProvider(metric_readers=[reader]))

def collect():
    out = {"scopes": [], "points": []}
    data = reader.get_metrics_data()
    for rm in (data.resource_metrics if data else []):
        for sm in rm.scope_metrics:
            out["scopes"].append(
                {"name": sm.scope.name,
                 "version": sm.scope.version,
                 "attributes": dict(sm.scope.attributes or {})}
            )
            for m in sm.metrics:
                for p in m.data.data_points:
                    out["points"].append({
                        "name": m.name,
                        "unit": m.unit,
                        "attributes": dict(p.attributes),
                        "value": getattr(p, "value", None) or getattr(p, "sum", None),
                    })
    return out
"""


def _run(body: str) -> dict:
    script = _PRELUDE + textwrap.dedent(body) + "\nprint(json.dumps(collect()))\n"
    proc = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=120
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


def _names(result: dict) -> set[str]:
    return {p["name"] for p in result["points"]}


def _point(result: dict, name: str, **match: str) -> dict:
    for p in result["points"]:
        if p["name"] == name and all(p["attributes"].get(k) == v for k, v in match.items()):
            return p
    raise AssertionError(f"no {name} point matching {match} in {result['points']}")


# ── The scope, which is the reason for the lazy import ───────────────────────


def test_the_scope_is_argus_own_and_keeps_its_attributes() -> None:
    """`argus.semconv.version` survives.

    A-29 moved the model's version into a scope attribute so an alpha and a
    stable stop reporting the same thing. OpenTelemetry's `_ProxyMeterProvider`
    accepts `attributes` on `get_meter()` and silently discards them, so a
    library imported before the provider exists loses that attribute for the
    life of the process — the fix would be undone by nothing more than an
    import at the top of a file.
    """
    result = _run("""
        R._emit_genai_metrics(
            request_kind="chat", model="qwen3-8b-q6", engine="llama_cpp",
            prompt_tokens=10, completion_tokens=5, duration_s=1.0,
            ttft_s=None, cost_usd=None, backend_id="b1",
        )
    """)
    assert result["scopes"] == [
        {
            "name": "argus-semconv",
            "version": "1.0.0a5",
            "attributes": {"argus.semconv.version": "1.0.0"},
        }
    ]


# ── The four metrics ─────────────────────────────────────────────────────────


def test_a_streamed_chat_emits_all_four() -> None:
    result = _run("""
        R._emit_genai_metrics(
            request_kind="chat", model="qwen3-8b-q6", engine="llama_cpp",
            prompt_tokens=120, completion_tokens=30, duration_s=1.5,
            ttft_s=0.115, cost_usd=0.00042, backend_id="qwen3-8b-q6-1",
        )
    """)
    assert _names(result) == {
        "gen_ai.client.operation.duration",
        "gen_ai.client.token.usage",
        "gen_ai.server.time_to_first_token",
        "argus.cost.usd",
    }

    duration = _point(result, "gen_ai.client.operation.duration")
    assert duration["unit"] == "s"
    assert duration["attributes"] == {
        "gen_ai.operation.name": "chat",
        "gen_ai.provider.name": "llama.cpp",
        "gen_ai.request.model": "qwen3-8b-q6",
    }

    tokens_in = _point(result, "gen_ai.client.token.usage", **{"gen_ai.token.type": "input"})
    tokens_out = _point(result, "gen_ai.client.token.usage", **{"gen_ai.token.type": "output"})
    assert tokens_in["unit"] == "{token}"
    assert (tokens_in["value"], tokens_out["value"]) == (120, 30)

    ttft = _point(result, "gen_ai.server.time_to_first_token")
    assert ttft["unit"] == "s"
    # A-29's point 5: TTFT carries the operation like the other three, so it can
    # be sliced with chat, embeddings and rerank sharing a deployment.
    assert ttft["attributes"]["gen_ai.operation.name"] == "chat"
    assert ttft["attributes"]["argus.inference.backend_id"] == "qwen3-8b-q6-1"

    cost = _point(result, "argus.cost.usd")
    assert cost["unit"] == "{usd}"
    assert cost["value"] == 0.00042


def test_the_engine_becomes_the_provider_name() -> None:
    """The same mapping the spans use — `_provider_of`, called from both."""
    result = _run("""
        R._emit_genai_metrics(
            request_kind="image", model="sd-turbo", engine="sd_cpp",
            prompt_tokens=0, completion_tokens=0, duration_s=2.0,
            ttft_s=None, cost_usd=0.01, backend_id="sd-1",
        )
    """)
    duration = _point(result, "gen_ai.client.operation.duration")
    assert duration["attributes"]["gen_ai.provider.name"] == "stable-diffusion.cpp"
    assert duration["attributes"]["gen_ai.operation.name"] == "image_generation"
    # An image request has no tokens at all — no zero-valued token series.
    assert "gen_ai.client.token.usage" not in _names(result)


def test_every_request_kind_has_an_operation_name() -> None:
    """The map is the translation between our vocabulary and OpenTelemetry's.

    A kind missing from it falls through as itself, which is plausible enough
    to survive review and wrong on the dashboard — so the map is asserted
    against the kinds that actually reach it rather than trusted.
    """
    result = _run("""
        for kind in ("chat", "embedding", "rerank", "image"):
            R._emit_genai_metrics(
                request_kind=kind, model="m", engine="llama_cpp",
                prompt_tokens=1, completion_tokens=0, duration_s=0.1,
                ttft_s=None, cost_usd=None, backend_id=None,
            )
    """)
    operations = {
        p["attributes"]["gen_ai.operation.name"]
        for p in result["points"]
        if p["name"] == "gen_ai.client.operation.duration"
    }
    assert operations == {"chat", "embeddings", "rerank", "image_generation"}


# ── Absence stays absence ────────────────────────────────────────────────────


def test_an_unpriced_model_records_no_cost_at_all() -> None:
    """Not a zero. PRM-119's rule, now on a counter.

    A counter incremented by 0.0 creates the series, so "this model has no
    price" and "this model is free" become the same line on a chart.
    """
    result = _run("""
        R._emit_genai_metrics(
            request_kind="chat", model="unpriced", engine="llama_cpp",
            prompt_tokens=5, completion_tokens=5, duration_s=0.5,
            ttft_s=None, cost_usd=None, backend_id="b1",
        )
    """)
    assert "argus.cost.usd" not in _names(result)


def test_a_non_streamed_request_records_no_ttft() -> None:
    """There is no first-token moment to report when nothing was streamed.

    Recording the full duration here would fill the gap A-24 measured with a
    number that looks like a TTFT and is not one.
    """
    result = _run("""
        R._emit_genai_metrics(
            request_kind="chat", model="qwen3-0.6b", engine="llama_cpp",
            prompt_tokens=5, completion_tokens=5, duration_s=0.5,
            ttft_s=None, cost_usd=0.0001, backend_id="b1",
        )
    """)
    assert "gen_ai.server.time_to_first_token" not in _names(result)


def test_a_caller_without_a_duration_records_no_duration() -> None:
    result = _run("""
        R._emit_genai_metrics(
            request_kind="rerank", model="qwen3-reranker", engine="llama_cpp",
            prompt_tokens=40, completion_tokens=0, duration_s=None,
            ttft_s=None, cost_usd=None, backend_id="b1",
        )
    """)
    assert _names(result) == {"gen_ai.client.token.usage"}


# ── Every call site, not most of them ────────────────────────────────────────


def test_every_record_usage_call_passes_a_duration_and_an_engine() -> None:
    """PRM-131 put the emission in one place. Its inputs still arrive from five.

    `_record_usage()` bills a request and emits its metrics. `duration_s` and
    `engine` are optional so a caller that genuinely lacks them still bills —
    which means a handler that simply forgets them keeps working, bills
    correctly, and quietly drops that route out of the latency and provider
    dimensions. Nothing fails; a chart is just missing a model. This is the
    same shape as the two duplicated header lists of PRM-130, so it gets the
    same treatment: assert on all the call sites rather than on one.
    """
    import ast
    import pathlib

    source = pathlib.Path(__file__).resolve().parents[1] / "src/prometheus_gateway/router.py"
    tree = ast.parse(source.read_text())

    offenders: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Name) and func.id == "_record_usage"):
            continue
        passed = {kw.arg for kw in node.keywords}
        missing = {"duration_s", "engine"} - passed
        if missing:
            offenders.append(f"router.py:{node.lineno} missing {sorted(missing)}")

    assert not offenders, f"_record_usage call sites that drop their metric inputs: {offenders}"


def test_the_ast_guard_can_actually_see_the_call_sites() -> None:
    """The guard above passes trivially if it matches nothing.

    A test written to find offenders and finding no calls at all reports
    success — which is how an inverted or mis-targeted assertion survives.
    """
    import ast
    import pathlib

    source = pathlib.Path(__file__).resolve().parents[1] / "src/prometheus_gateway/router.py"
    tree = ast.parse(source.read_text())
    calls = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "_record_usage"
    ]
    # chat non-streaming, chat streamed, embeddings, rerank, images, and
    # PRM-136's pass-through. The count is deliberately exact: a new route that
    # bills is a new place for the metric inputs to be forgotten, and this line
    # is what makes adding one a decision rather than an omission.
    assert len(calls) == 6, f"expected 6 _record_usage call sites, found {len(calls)}"
