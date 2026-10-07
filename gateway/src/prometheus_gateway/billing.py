"""Pure billing calculations — period bounds, currency conversion, tax, and
period-summary assembly. Kept separate from the FastAPI routing layer, same
as pricing.py/db.py being delegated to by router.py.

Implements: docs/roadmap.md — RM-60 (#4, #5, #8, #9).
"""

from __future__ import annotations

import calendar
from datetime import date
from typing import Any

from . import budget, db
from .telemetry import get_logger

logger = get_logger(__name__)


def month_bounds(period: str) -> tuple[date, date]:
    """ "2026-08" -> (2026-08-01, 2026-08-31)."""
    year_str, month_str = period.split("-")
    year, month = int(year_str), int(month_str)
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, 1), date(year, month, last_day)


def convert_currency(
    amount_usd: float | None, currency_code: str, rates: dict[str, float]
) -> float | None:
    """`rates` maps currency_code -> units_per_usd (e.g. {"PEN": 3.75}). USD is
    the identity conversion and never needs a rates entry.

    PRM-119: an unknown amount converts to an unknown amount. Multiplying it
    by a rate would invent a figure in the client's own currency, which is the
    one place a fabricated 0.00 does the most damage.
    """
    if amount_usd is None:
        return None
    if currency_code == "USD":
        return amount_usd
    units_per_usd = rates.get(currency_code)
    if units_per_usd is None:
        return amount_usd
    return amount_usd * units_per_usd


def apply_tax(
    subtotal_usd: float | None, tax_rate_percent: float
) -> tuple[float | None, float | None]:
    """Returns (tax_amount_usd, total_usd) for a tax-exclusive line item.

    PRM-119: a subtotal of None means "nothing here could be priced", which is
    not zero. Taxing it would turn an unknown into a confident 0.00 — the
    silent $0 every other layer of this system refuses to produce.
    """
    if subtotal_usd is None:
        return None, None
    tax_amount = subtotal_usd * (tax_rate_percent / 100)
    return tax_amount, subtotal_usd + tax_amount


async def build_platform_overview(period: str) -> dict[str, Any]:
    """The whole platform for one month, ranked — PRM-221.

    Billing opened on a client selector, which meant it could not answer its
    own first question: what did the platform bill this month? Answering it by
    hand was a request per client, and that is exactly the loop this endpoint
    replaces.

    **Subtotal is the headline, tax is a separate line, and that is deliberate.**
    `tax_rate_percent` is configured per client, so a single blended "total
    billed" would add figures computed at different rates and present the sum
    as one number. The total is still returned — it is what is owed — but the
    two are never collapsed.

    Reuses `query_client_cost_range` and `query_model_cost_range` rather than
    adding grouped queries that already exist.
    """
    start_day, end_day = month_bounds(period)
    by_client = await db.query_client_cost_range(start_day, end_day)
    by_model = await db.query_model_cost_range(start_day, end_day)
    tax_rates = {
        row.client_id: row.tax_rate_percent for row in await db.list_client_billing_settings()
    }

    clients: list[dict[str, Any]] = []
    subtotal = 0.0
    tax_total = 0.0
    any_priced = False
    for row in by_client:
        client_subtotal = row["cost_usd"]
        rate = tax_rates.get(row["client_id"], 0.0)
        tax_amount, total = apply_tax(client_subtotal, rate)
        if client_subtotal is not None:
            any_priced = True
            subtotal += client_subtotal
            tax_total += tax_amount or 0.0
        clients.append(
            {
                "client_id": row["client_id"],
                "subtotal_usd": client_subtotal,
                "tax_rate_percent": rate,
                "tax_amount_usd": tax_amount,
                "total_usd": total,
                "total_tokens": row["total_tokens"],
                "request_count": row["request_count"],
                "unpriced_requests": row["unpriced_requests"],
            }
        )
    clients.sort(key=lambda c: (c["subtotal_usd"] or 0.0, c["total_tokens"]), reverse=True)

    models = sorted(by_model, key=lambda m: ((m["cost_usd"] or 0.0), m["tokens"]), reverse=True)

    return {
        "period": period,
        "period_start": start_day.isoformat(),
        "period_end": end_day.isoformat(),
        # None, never 0.0, when nothing in the month could be priced — the same
        # rule `apply_tax` keeps one function above.
        "subtotal_usd": subtotal if any_priced else None,
        "tax_amount_usd": tax_total if any_priced else None,
        "total_usd": subtotal + tax_total if any_priced else None,
        "client_count": len(clients),
        "total_tokens": sum(c["total_tokens"] for c in clients),
        "request_count": sum(c["request_count"] for c in clients),
        "unpriced_requests": sum(c["unpriced_requests"] for c in clients),
        "by_client": clients,
        "by_model": models,
    }


async def build_period_summary(client_id: str, period: str) -> dict[str, Any]:
    """Assemble one client's billing summary for one UTC calendar-month period."""
    start_day, end_day = month_bounds(period)
    settings_row = await db.get_client_billing_settings(client_id)
    tax_rate_percent = settings_row.tax_rate_percent if settings_row else 0.0
    preferred_currency = settings_row.preferred_currency if settings_row else "USD"
    cap_usd = settings_row.monthly_spend_cap_usd if settings_row else None

    daily_rows = await db.query_daily_cost_range(start_day, end_day, client_id)
    model_rows = await db.query_model_cost_range(start_day, end_day, client_id)
    # PRM-119: `or 0.0` used to live here, which is how a period of entirely
    # unpriced usage came to report "USD 0.00" — six requests, every row
    # showing "—", and a total stating the client owed nothing. The rows were
    # honest and the total was not. None means unknown; 0.0 now only ever
    # means a real zero.
    priced = [row["cost_usd"] for row in daily_rows if row["cost_usd"] is not None]
    subtotal_usd = sum(priced) if priced else None
    total_tokens = sum(row["tokens"] for row in daily_rows)
    request_count = sum(row["request_count"] for row in daily_rows)
    # A partly-priced period is the more dangerous case: it looks complete.
    unpriced_requests = sum(row["unpriced_requests"] for row in daily_rows)

    tax_amount_usd, total_usd = apply_tax(subtotal_usd, tax_rate_percent)

    rate_rows = await db.list_currency_rates()
    rates = {r.currency_code: r.units_per_usd for r in rate_rows}

    return {
        "client_id": client_id,
        "period": period,
        "period_start": start_day.isoformat(),
        "period_end": end_day.isoformat(),
        "subtotal_usd": subtotal_usd,
        "tax_rate_percent": tax_rate_percent,
        "tax_amount_usd": tax_amount_usd,
        "total_usd": total_usd,
        "preferred_currency": preferred_currency,
        "total_in_preferred_currency": convert_currency(total_usd, preferred_currency, rates),
        "exchange_rate_used": rates.get(preferred_currency, 1.0)
        if preferred_currency != "USD"
        else 1.0,
        "total_tokens": total_tokens,
        "request_count": request_count,
        # PRM-119: how much of `request_count` the subtotal does not account
        # for. Non-zero means the total is a lower bound, not a bill.
        "unpriced_requests": unpriced_requests,
        "monthly_spend_cap_usd": cap_usd,
        "daily": [
            {
                "day": row["day"].isoformat(),
                "cost_usd": row["cost_usd"],
                "tokens": row["tokens"],
                "request_count": row["request_count"],
                "unpriced_requests": row["unpriced_requests"],
            }
            for row in daily_rows
        ],
        "by_model": [
            {
                "model_id": row["model_id"],
                "cost_usd": row["cost_usd"],
                "tokens": row["tokens"],
                "request_count": row["request_count"],
                # PRM-119: per model, because this is where an operator can act
                # on it — a model with traffic and no price is one row here.
                "unpriced_requests": row["unpriced_requests"],
            }
            for row in model_rows
        ],
    }


async def build_alerts_listing(redis_client: Any) -> list[dict[str, Any]]:
    """Clients with at least one currently-crossed alert threshold this period —
    feeds the in-app budget banner. Notify-only and polled every ~15s by the
    dashboard, so a Redis hiccup here must degrade to "no alerts", never a
    500 that breaks the page — mirrors the hard-cap enforcement path's own
    fail-open-on-secondary-failure conventions (router.py's usage/alert
    dispatch, budget.get_client_billing_settings_cached).
    """
    tracker = budget.BudgetTracker(redis_client)
    settings_rows = await db.list_client_billing_settings_with_cap()
    alerts: list[dict[str, Any]] = []
    for row in settings_rows:
        try:
            notified = await tracker.get_notified_thresholds(row.client_id)
            if not notified:
                continue
            spend = await tracker.get_spend(row.client_id)
        except Exception as exc:
            logger.warning("billing.alerts_read_error", client_id=row.client_id, error=str(exc))
            continue
        alerts.append(
            {
                "client_id": row.client_id,
                "monthly_spend_cap_usd": row.monthly_spend_cap_usd,
                "spend_usd": spend,
                "crossed_thresholds_percent": notified,
            }
        )
    return alerts
