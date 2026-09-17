"""Tests for RM-60 — GET /v1/usage/export (CSV export over a date range)."""

from __future__ import annotations

import csv
import io
from datetime import date

import fakeredis.aioredis as fakeredis
import pytest
from httpx import ASGITransport, AsyncClient

from prometheus_gateway import db
from prometheus_gateway.config import Settings
from prometheus_gateway.main import create_app
from prometheus_gateway.models.registry import ModelRegistry
from tests.conftest import make_token

_DAY = date(2026, 1, 15)
_DAY2 = date(2026, 1, 16)


@pytest.fixture
def fake_redis():
    return fakeredis.FakeRedis()


@pytest.fixture
def registry(tmp_path):
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
def settings(rsa_keys, tmp_path):
    key_file = tmp_path / "public.pem"
    key_file.write_text(rsa_keys["public"])
    return Settings(
        jwt_issuer="https://auth.test",
        jwt_audience="prometheus-gateway",
        jwt_public_key_file=str(key_file),
        jwt_revocation_redis_url=None,
        rate_limit_strict=False,
    )


@pytest.fixture
def app(settings, registry, fake_redis):
    return create_app(settings=settings, registry=registry, redis_client=fake_redis)


@pytest.fixture
def admin_headers(rsa_keys):
    token = make_token(
        rsa_keys["private"], scope="admin:read", sub="admin-user", azp="admin-client"
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def non_admin_headers(rsa_keys):
    token = make_token(rsa_keys["private"], scope="inference:read", sub="user-x", azp="client-a")
    return {"Authorization": f"Bearer {token}"}


async def test_export_requires_admin_read_scope(app, non_admin_headers):
    await db.create_tables(db.get_engine())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get(
            "/v1/usage/export",
            params={"start": "2026-01-01", "end": "2026-01-31"},
            headers=non_admin_headers,
        )
    assert r.status_code == 403


async def test_export_invalid_date_returns_400(app, admin_headers):
    await db.create_tables(db.get_engine())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get(
            "/v1/usage/export",
            params={"start": "not-a-date", "end": "2026-01-31"},
            headers=admin_headers,
        )
    assert r.status_code == 400


async def test_export_inverted_range_returns_400(app, admin_headers):
    await db.create_tables(db.get_engine())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get(
            "/v1/usage/export",
            params={"start": "2026-01-31", "end": "2026-01-01"},
            headers=admin_headers,
        )
    assert r.status_code == 400


async def test_export_range_too_large_returns_400(app, admin_headers):
    await db.create_tables(db.get_engine())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get(
            "/v1/usage/export",
            params={"start": "2020-01-01", "end": "2026-01-01"},
            headers=admin_headers,
        )
    assert r.status_code == 400


async def test_export_csv_shape_and_total_row(app, admin_headers):
    await db.create_tables(db.get_engine())
    await db.record_usage("client-a", "small-model", 100, 50, day=_DAY)
    await db.record_usage("client-a", "small-model", 10, 5, day=_DAY2)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get(
            "/v1/usage/export",
            params={"start": "2026-01-15", "end": "2026-01-16"},
            headers=admin_headers,
        )

    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"]

    rows = list(csv.reader(io.StringIO(r.text)))
    header, *data_rows = rows
    assert header[0] == "generated_at"
    # By name, not by position. New columns are appended, so anything indexing
    # from the end breaks the moment one is added — which is what happened to
    # this assertion when PRM-100 appended two. Reading by name is what we tell
    # consumers to do, and the test should not do something we advise against.
    for column in ("interrupted", "termination_reason", "request_id", "cached_prompt_tokens"):
        assert column in header

    total_row = data_rows[-1]
    assert total_row[5] == "TOTAL"
    # prompt_tokens column (index 7): 100 + 10 = 110
    assert total_row[7] == "110"
    # completion_tokens column (index 8): 50 + 5 = 55
    assert total_row[8] == "55"
    # No pricing.yaml configured — total cost stays blank, never a false "0".
    assert total_row[header.index("cost_usd")] == ""
    # RM-83/RM-88: how one request ended has no total, so those columns are
    # blank on the total row rather than summing something meaningless. A
    # request id has no total either; cached tokens do, being a count.
    assert total_row[header.index("interrupted")] == ""
    assert total_row[header.index("termination_reason")] == ""
    assert total_row[header.index("request_id")] == ""
    assert total_row[header.index("cached_prompt_tokens")] == "0"
    # Ordinary recorded usage finished, and the two columns agree on that.
    assert data_rows[0][header.index("interrupted")] == "false"
    assert data_rows[0][header.index("termination_reason")] == "complete"

    # 2 data rows (one per event) + 1 total row
    assert len(data_rows) == 3


async def test_export_filters_by_client_id(app, admin_headers):
    await db.create_tables(db.get_engine())
    await db.record_usage("client-a", "small-model", 10, 5, day=_DAY)
    await db.record_usage("client-b", "small-model", 1, 1, day=_DAY)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get(
            "/v1/usage/export",
            params={"start": "2026-01-15", "end": "2026-01-15", "client_id": "client-a"},
            headers=admin_headers,
        )

    rows = list(csv.reader(io.StringIO(r.text)))
    _header, *data_rows = rows
    non_total_rows = [row for row in data_rows if row[5] != "TOTAL"]
    assert len(non_total_rows) == 1
    assert non_total_rows[0][3] == "client-a"


# ── PRM-100: a caller reads the row for its own request ─────────────────────


@pytest.fixture
def client_headers(rsa_keys):
    """An ordinary caller: inference scope, no admin. `azp` is the client id the
    row is filed under, which is what the endpoint filters by."""
    token = make_token(rsa_keys["private"], scope="inference:read", sub="a-user", azp="client-a")
    return {"Authorization": f"Bearer {token}"}


async def test_a_client_reads_its_own_usage_row(app, client_headers):
    """The point of the endpoint: we added termination_reason so a charge for a
    half-delivered answer could be explained, then put both usage endpoints
    behind admin:read — leaving it visible only to the party that does not need
    it. This reads one row, the caller's own, with no admin scope.
    """
    await db.create_tables(db.get_engine())
    await db.record_usage(
        "client-a",
        "qwen3-0.6b",
        100,
        40,
        instance_id="qwen3-1",
        request_id="req-aaa",
        cached_prompt_tokens=64,
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.get("/v1/usage/req-aaa", headers=client_headers)

    assert resp.status_code == 200
    body = resp.json()
    assert body["request_id"] == "req-aaa"
    assert body["termination_reason"] == "complete"
    assert body["instance_id"] == "qwen3-1"
    # Broken down exactly as the inference response reports it. An aggregate
    # cannot be reconciled against what the caller received once caching is
    # involved, and reconciling is all this is for.
    assert body["usage"] == {
        "prompt_tokens": 100,
        "completion_tokens": 40,
        "total_tokens": 140,
        "prompt_tokens_details": {"cached_tokens": 64},
    }


async def test_the_row_reports_the_name_the_caller_used(app, client_headers):
    """PRM-115. PRM-113 re-keyed usage on the immutable catalog id, and this
    endpoint returned that id as `model` — a value the caller never sent, never
    received, and cannot look up, because `GET /v1/models` advertises the slug.
    Axonium found it in all three SDKs at once: `RequestUsage.model` was
    exposing something with no use.

    Reconciling means holding this next to the `model` the inference response
    returned, so it has to be the same string.
    """
    await db.create_tables(db.get_engine())
    await db.record_usage(
        "client-a",
        "qwen3-0-6b-iq4-nl-local-2",  # the catalog id, which looks like a name
        10,
        5,
        request_id="req-slug",
        model_slug="qwen3-0.6b",  # what the caller asked for and got back
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.get("/v1/usage/req-slug", headers=client_headers)

    assert resp.json()["model"] == "qwen3-0.6b"


async def test_a_row_with_no_slug_at_all_still_names_something(app, client_headers):
    """`model_slug` is nullable, so the column has to be readable as empty —
    the migration backfills it, but a row that escaped that is the one case
    where returning null would make every SDK special-case this field. Written
    straight at the database because `record_usage()` will not produce one.
    """
    from sqlalchemy import text

    await db.create_tables(db.get_engine())
    await db.record_usage("client-a", "solo-un-nombre", 1, 1, request_id="req-bare")
    async with db.get_engine().begin() as conn:
        await conn.execute(
            text("UPDATE usage_events SET model_slug = NULL WHERE request_id = 'req-bare'")
        )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.get("/v1/usage/req-bare", headers=client_headers)

    assert resp.json()["model"] == "solo-un-nombre"


async def test_another_clients_request_is_404_not_403(app, client_headers):
    """404 rather than 403, on Axonium's suggestion: a 403 would confirm that
    the id exists, which is exactly what a probe wants to learn."""
    await db.create_tables(db.get_engine())
    await db.record_usage("someone-else", "qwen3-0.6b", 10, 5, request_id="req-bbb")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.get("/v1/usage/req-bbb", headers=client_headers)

    assert resp.status_code == 404


async def test_an_unknown_request_is_also_404(app, client_headers):
    await db.create_tables(db.get_engine())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.get("/v1/usage/req-never", headers=client_headers)
    assert resp.status_code == 404


def test_the_documented_column_list_matches_the_file():
    """PRM-100 / A-13: the export's shape was a commitment that lived only in
    correspondence — new columns appended, existing ones never moved — while the
    guide documented neither the columns nor the rule. Writing it down is worth
    little if it can drift from the code, so this compares the two.
    """
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    src = (root / "gateway/src/prometheus_gateway/router.py").read_text()
    start = src.index('"generated_at",')
    in_code = re.findall(r'"([a-z0-9_]+)"', src[start : src.index("]", start)])

    guide = (root / "docs/sdk-integration-guide.md").read_text()
    start = guide.index("generated_at, period_start")
    block = guide[start : guide.index("```", start)]
    documented = [c.strip() for c in block.replace("\n", " ").split(",") if c.strip()]

    assert in_code == documented, "the guide's column list has drifted from the export"


def test_every_error_the_gateway_raises_is_in_the_guide():
    """PRM-116 / A-18. Axonium found five `type` suffixes we had documented
    nowhere — four of them the entire subject of one round — and a §6.2 bullet
    denying the idempotency §3.7 describes. They were unharmed because they
    build from their own recorded catalog rather than our guide, which is the
    part that should worry us: the guide had drifted for weeks and the only
    reason anyone noticed was that a reader stopped trusting it.

    Same shape as the export-columns guard above, and for the same reason —
    writing a contract down is worth little if it can drift from the code. One
    direction only: the guide legitimately documents errors raised in
    middleware, which never appear in this file.
    """
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    src = (root / "gateway/src/prometheus_gateway/router.py").read_text()
    raised = set(re.findall(r'_problem\(\s*request,\s*\d+,\s*"([a-z0-9-]+)"', src))
    # The four idempotency refusals reach `_problem` as `outcome.kind`, so the
    # pattern above cannot see them — which is exactly the set A-18 was about.
    # Read them where they are declared instead.
    idem = (root / "gateway/src/prometheus_gateway/idempotency.py").read_text()
    # Public constants only — the private ones next to them are internal state
    # ("completed", "in_progress"), not error types a caller ever sees.
    raised |= set(re.findall(r'^[A-Z][A-Z_]* = "([a-z0-9-]+)"$', idem, re.MULTILINE))

    guide = (root / "docs/sdk-integration-guide.md").read_text()
    table = guide[
        guide.index("### 5.2 Full error catalog") : guide.index(
            "There is no `404` on the inference-family"
        )
    ]
    documented = set(re.findall(r"\|\s*`([a-z0-9-]+)`\s*\|", table))

    assert raised, "the extraction stopped matching — fix this before trusting it"
    assert not (raised - documented), (
        f"raised by the gateway, absent from the guide's error catalog: "
        f"{sorted(raised - documented)}"
    )


def test_the_export_ends_lines_the_way_its_reader_expects():
    """PRM-119 follow-up, from a screenshot: the dashboard's Model column kept
    showing the catalog id after we switched it to the public name.

    The cause was ours and it was one character. `csv.writer` emits RFC-4180
    `\\r\\n`, and the dashboard's parser split on `"\\n"` — leaving a stray
    `\\r` welded to the LAST field of every line. `model_slug` is the last
    column, so `header.indexOf("model_slug")` returned -1 on every export and
    the parser fell back to `model_id`, exactly as designed for an old file.

    It was invisible while that parser read by position, because index 13 is
    not the last column. Reading by name is still right; it just has to survive
    the line ending the writer actually produces.

    This pins both halves: the writer keeps emitting `\\r\\n` (other consumers
    vendor this file and Excel expects it), and the reader keeps tolerating it.
    """
    import csv
    import io
    import re
    from pathlib import Path

    buf = io.StringIO()
    csv.writer(buf).writerow(["a", "b"])
    assert buf.getvalue().endswith("\r\n"), (
        "csv.writer no longer emits CRLF — the note below is now wrong, not the code"
    )

    parser = (Path(__file__).resolve().parents[2] / "gateway/admin-ui/src/api/usage.ts").read_text()
    split = re.search(r"response\.data\.trim\(\)\.split\((.+?)\);", parser)
    assert split, "the export parser's line split moved — re-point this guard"
    assert "\\r?\\n" in split.group(1), (
        f"the parser splits lines on {split.group(1)} and will glue \\r onto the last "
        "column of every row, which is where new columns are appended"
    )
