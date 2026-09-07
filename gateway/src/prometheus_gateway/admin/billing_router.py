"""Billing admin JSON API — RM-60.

Mounted at /admin/api/billing/* by main.py when admin_dashboard_enabled=True,
same pattern as admin/router.py. Unlike that file (proxy-only, forwards to
manager-api/auth-service), this data lives in the gateway's own DB — kept in
a separate module rather than bolting non-proxy logic onto the proxy-only
file.

GET  /admin/api/billing/clients/{client_id}/settings         admin:read
PUT  /admin/api/billing/clients/{client_id}/settings         admin:write
GET  /admin/api/billing/currency-rates                       admin:read
PUT  /admin/api/billing/currency-rates                       admin:write
GET  /admin/api/billing/clients/{client_id}/summary          admin:read
GET  /admin/api/billing/clients/{client_id}/history          admin:read
GET  /admin/api/billing/alerts                               admin:read

Implements: docs/roadmap.md — RM-60 (#5, #6, #7, #9).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from .. import billing, budget, db
from ..router import _problem
from ..telemetry import get_logger

logger = get_logger(__name__)

_ISO_MONTH_FORMAT = "%Y-%m"


def _claims(request: Request) -> Any:
    return getattr(getattr(request, "state", None), "claims", None)


def _require_scope(request: Request, scope: str) -> JSONResponse | None:
    claims = _claims(request)
    if claims is None or not claims.has_scope(scope):
        return _problem(
            request, 403, "forbidden", "Forbidden", f"This endpoint requires {scope} scope."
        )
    return None


def _settings_to_dict(row: "db.ClientBillingSettings | None", client_id: str) -> dict[str, Any]:
    if row is None:
        return {
            "client_id": client_id,
            "monthly_spend_cap_usd": None,
            "alert_thresholds_percent": None,
            "tax_rate_percent": 0.0,
            "preferred_currency": "USD",
        }
    return {
        "client_id": row.client_id,
        "monthly_spend_cap_usd": row.monthly_spend_cap_usd,
        "alert_thresholds_percent": row.alert_thresholds_percent,
        "tax_rate_percent": row.tax_rate_percent,
        "preferred_currency": row.preferred_currency,
    }


def create_billing_router() -> APIRouter:
    router = APIRouter()

    @router.get("/admin/api/billing/clients/{client_id}/settings")
    async def get_billing_settings(client_id: str, request: Request) -> Any:
        if (err := _require_scope(request, "admin:read")) is not None:
            return err
        row = await db.get_client_billing_settings(client_id)
        return _settings_to_dict(row, client_id)

    @router.put("/admin/api/billing/clients/{client_id}/settings")
    async def put_billing_settings(client_id: str, body: dict[str, Any], request: Request) -> Any:
        if (err := _require_scope(request, "admin:write")) is not None:
            return err
        cap = body.get("monthly_spend_cap_usd")
        if cap is not None:
            try:
                cap = float(cap)
            except (TypeError, ValueError):
                return _problem(
                    request,
                    400,
                    "invalid-cap",
                    "Invalid Cap",
                    "monthly_spend_cap_usd must be a number or null.",
                )
        tax_rate = float(body.get("tax_rate_percent", 0.0) or 0.0)
        currency = body.get("preferred_currency") or "USD"
        if currency not in ("USD", "PEN", "EUR"):
            return _problem(
                request,
                400,
                "invalid-currency",
                "Invalid Currency",
                "preferred_currency must be one of USD, PEN, EUR.",
            )
        row = await db.upsert_client_billing_settings(
            client_id,
            monthly_spend_cap_usd=cap,
            alert_thresholds_percent=body.get("alert_thresholds_percent") or None,
            tax_rate_percent=tax_rate,
            preferred_currency=currency,
        )
        budget.invalidate_client_billing_settings_cache(client_id)
        return _settings_to_dict(row, client_id)

    @router.get("/admin/api/billing/currency-rates")
    async def get_currency_rates(request: Request) -> Any:
        if (err := _require_scope(request, "admin:read")) is not None:
            return err
        rows = await db.list_currency_rates()
        return {r.currency_code: r.units_per_usd for r in rows}

    @router.put("/admin/api/billing/currency-rates")
    async def put_currency_rates(body: dict[str, float], request: Request) -> Any:
        if (err := _require_scope(request, "admin:write")) is not None:
            return err
        for currency_code, units_per_usd in body.items():
            if currency_code not in ("PEN", "EUR"):
                return _problem(
                    request,
                    400,
                    "invalid-currency",
                    "Invalid Currency",
                    f"{currency_code!r} is not a supported currency (PEN, EUR).",
                )
            try:
                await db.upsert_currency_rate(currency_code, float(units_per_usd))
            except (TypeError, ValueError):
                return _problem(
                    request,
                    400,
                    "invalid-rate",
                    "Invalid Rate",
                    f"units_per_usd for {currency_code!r} must be a number.",
                )
        rows = await db.list_currency_rates()
        return {r.currency_code: r.units_per_usd for r in rows}

    @router.get("/admin/api/billing/clients/{client_id}/summary")
    async def get_billing_summary(
        client_id: str, request: Request, period: str | None = None
    ) -> Any:
        if (err := _require_scope(request, "admin:read")) is not None:
            return err
        target_period = period or datetime.now(tz=timezone.utc).strftime(_ISO_MONTH_FORMAT)
        try:
            datetime.strptime(target_period, _ISO_MONTH_FORMAT)
        except ValueError:
            return _problem(
                request, 400, "invalid-period", "Invalid Period", "period must be YYYY-MM."
            )
        return await billing.build_period_summary(client_id, target_period)

    @router.get("/admin/api/billing/clients/{client_id}/history")
    async def get_billing_history(client_id: str, request: Request, periods: int = 6) -> Any:
        if (err := _require_scope(request, "admin:read")) is not None:
            return err
        periods = max(1, min(periods, 24))
        now = datetime.now(tz=timezone.utc)
        target_periods: list[str] = []
        year, month = now.year, now.month
        for _ in range(periods):
            target_periods.append(f"{year:04d}-{month:02d}")
            month -= 1
            if month == 0:
                month = 12
                year -= 1
        summaries = [await billing.build_period_summary(client_id, p) for p in target_periods]
        return {"client_id": client_id, "periods": summaries}

    @router.get("/admin/api/billing/alerts")
    async def get_billing_alerts(request: Request) -> Any:
        if (err := _require_scope(request, "admin:read")) is not None:
            return err
        redis_client = getattr(request.app.state, "shared_redis", None)
        if redis_client is None:
            return {"object": "list", "data": []}
        alerts = await billing.build_alerts_listing(redis_client)
        return {"object": "list", "data": alerts}

    return router
