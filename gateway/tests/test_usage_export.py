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
    assert header[-2] == "interrupted"
    assert header[-1] == "termination_reason"

    total_row = data_rows[-1]
    assert total_row[5] == "TOTAL"
    # prompt_tokens column (index 7): 100 + 10 = 110
    assert total_row[7] == "110"
    # completion_tokens column (index 8): 50 + 5 = 55
    assert total_row[8] == "55"
    # No pricing.yaml configured — total cost stays blank, never a false "0".
    assert total_row[header.index("cost_usd")] == ""
    # RM-83/RM-88: how one request ended has no total, so both columns are
    # blank on the total row rather than summing something meaningless.
    assert total_row[-2:] == ["", ""]
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
