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
GET  /admin/api/billing/pricing                               admin:read
PUT  /admin/api/billing/pricing/{model_id}                    admin:write
DELETE /admin/api/billing/pricing/{model_id}                  admin:write

Implements: docs/roadmap.md — RM-60 (#5, #6, #7, #9).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from .. import billing, budget, db, pricing
from ..config import Settings
from ..router import _problem
from .client import ManagerApiClient
from .nodes_client import fetch_nodes
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
            "tier": None,
        }
    return {
        "client_id": row.client_id,
        "monthly_spend_cap_usd": row.monthly_spend_cap_usd,
        "alert_thresholds_percent": row.alert_thresholds_percent,
        "tier": row.tier,
        "tax_rate_percent": row.tax_rate_percent,
        "preferred_currency": row.preferred_currency,
    }


def create_billing_router(manager_client: "ManagerApiClient | None" = None) -> APIRouter:
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
        # PRM-228: the tier, validated against the catalogue rather than taken
        # on trust. A typo would otherwise park a client on a tier that does
        # not exist, which resolves to the platform defaults and looks exactly
        # like a tier that does.
        tier = body.get("tier") or None
        if tier is not None and await db.get_rate_limit_tier(tier) is None:
            return _problem(
                request,
                400,
                "unknown-tier",
                "Unknown Tier",
                f"No tier {tier!r}. See GET /admin/api/limits/tiers for the ones that exist.",
            )
        row = await db.upsert_client_billing_settings(
            client_id,
            monthly_spend_cap_usd=cap,
            alert_thresholds_percent=body.get("alert_thresholds_percent") or None,
            tax_rate_percent=tax_rate,
            preferred_currency=currency,
            tier=tier,
        )
        budget.invalidate_client_billing_settings_cache(client_id)
        budget.invalidate_rate_limit_tier_cache()
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

    _TIER_DIMENSIONS = ("rpm", "tpm", "tpm_input", "tpm_output", "rpd", "tpd", "ipm")

    def _tier_json(row: Any) -> dict[str, Any]:
        out: dict[str, Any] = {"name": row.name, "description": row.description}
        out.update({d: getattr(row, d) for d in _TIER_DIMENSIONS})
        return out

    @router.get("/admin/api/limits/tiers")
    async def list_tiers(request: Request) -> Any:
        """The tier catalogue — PRM-228."""
        if (err := _require_scope(request, "admin:read")) is not None:
            return err
        return {"tiers": [_tier_json(t) for t in await db.list_rate_limit_tiers()]}

    @router.put("/admin/api/limits/tiers/{name}")
    async def put_tier(name: str, body: dict[str, Any], request: Request) -> Any:
        """Create or update one tier.

        A dimension present and null clears it back to the platform default; a
        dimension absent is left as it was. The two are different edits and
        collapsing them would make "leave this alone" unexpressible.
        """
        if (err := _require_scope(request, "admin:write")) is not None:
            return err
        if not name or len(name) > 32:
            return _problem(
                request, 400, "invalid-tier", "Invalid Tier", "name must be 1-32 characters."
            )
        values: dict[str, Any] = {}
        if "description" in body:
            values["description"] = body["description"]
        for dimension in _TIER_DIMENSIONS:
            if dimension not in body:
                continue
            raw = body[dimension]
            if raw is None:
                values[dimension] = None
                continue
            try:
                parsed = int(raw)
            except (TypeError, ValueError):
                return _problem(
                    request,
                    400,
                    "invalid-tier",
                    "Invalid Tier",
                    f"{dimension} must be a whole number or null.",
                )
            if parsed <= 0:
                return _problem(
                    request,
                    400,
                    "invalid-tier",
                    "Invalid Tier",
                    f"{dimension} must be greater than zero \u2014 use null to fall back to "
                    f"the platform default, since zero would mean a ceiling nobody can pass.",
                )
            values[dimension] = parsed
        row = await db.upsert_rate_limit_tier(name, **values)
        budget.invalidate_rate_limit_tier_cache(name)
        return _tier_json(row)

    @router.delete("/admin/api/limits/tiers/{name}")
    async def delete_tier(name: str, request: Request) -> Any:
        """Refused while clients are still on it.

        Deleting a tier out from under a client would silently move them to the
        platform defaults, which is a limit change nobody asked for and nobody
        would see.
        """
        if (err := _require_scope(request, "admin:write")) is not None:
            return err
        if await db.get_rate_limit_tier(name) is None:
            return _problem(request, 404, "unknown-tier", "Unknown Tier", f"No tier {name!r}.")
        if assigned := await db.count_clients_on_tier(name):
            return _problem(
                request,
                409,
                "tier-in-use",
                "Tier In Use",
                f"{assigned} client(s) are on {name!r}. Move them first — deleting it would "
                f"change their limits without anyone seeing it happen.",
            )
        await db.delete_rate_limit_tier(name)
        budget.invalidate_rate_limit_tier_cache(name)
        return {"deleted": name}

    @router.get("/admin/api/billing/overview")
    async def get_billing_overview(request: Request, period: str | None = None) -> Any:
        """The whole platform for one month — PRM-221.

        Every other endpoint on this router is keyed by client, so the page
        built on them could only answer "what does this one client owe" and
        the reader had to already know which client they meant. Asking "how is
        the month going" meant one request per client, in a loop, by hand.
        """
        if (err := _require_scope(request, "admin:read")) is not None:
            return err
        target_period = period or datetime.now(tz=timezone.utc).strftime(_ISO_MONTH_FORMAT)
        try:
            datetime.strptime(target_period, _ISO_MONTH_FORMAT)
        except ValueError:
            return _problem(
                request, 400, "invalid-period", "Invalid Period", "period must be YYYY-MM."
            )
        return await billing.build_platform_overview(target_period)

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

    @router.get("/admin/api/billing/pricing")
    async def get_pricing(request: Request) -> Any:
        """Model prices, admin-configured — replaces hand-editing pricing.yaml.

        Returns every DB-persisted override plus any model that only has a
        pricing.yaml-sourced price (source="db" vs "file") so the UI can show
        what's actually in effect, not just what was explicitly set here.
        """
        if (err := _require_scope(request, "admin:read")) is not None:
            return err
        db_rows = {row.model_id: row for row in await db.list_model_price_configs()}
        table = pricing.get_pricing_table()
        result: dict[str, Any] = {}
        for model_id, price in table.list_prices().items():
            result[model_id] = {
                "prompt_price_per_1m": price.prompt_price_per_1m,
                "completion_price_per_1m": price.completion_price_per_1m,
                "image_price": price.image_price,
                # PRM-120: "default" is a third answer, not a flavour of "db".
                # A seeded base price and a price somebody chose are both rows
                # in the same table, and only this tells them apart — which is
                # what an operator needs to know before invoicing on it.
                "source": (
                    ("default" if db_rows[model_id].is_default else "db")
                    if model_id in db_rows
                    else "file"
                ),
            }
        return {
            "object": "list",
            "data": result,
            # PRM-125: the reset button fills the inputs from this and writes
            # nothing — saving is what Save is for. Sent with the listing rather
            # than as its own endpoint because the table needs both together and
            # a second round trip would let them disagree.
            "defaults_by_modality": {
                modality: {
                    "prompt_price_per_1m": base.prompt_price_per_1m,
                    "completion_price_per_1m": base.completion_price_per_1m,
                    "image_price": base.image_price,
                }
                for modality, base in pricing.default_prices().items()
            },
        }

    @router.put("/admin/api/billing/pricing/{model_id}")
    async def put_pricing(model_id: str, body: dict[str, Any], request: Request) -> Any:
        if (err := _require_scope(request, "admin:write")) is not None:
            return err

        def _parse_price(key: str) -> float | None:
            value = body.get(key)
            if value is None:
                return None
            try:
                return float(value)
            except (TypeError, ValueError):
                raise ValueError(key) from None

        try:
            prompt_price = _parse_price("prompt_price_per_1m")
            completion_price = _parse_price("completion_price_per_1m")
            image_price = _parse_price("image_price")
        except ValueError as exc:
            return _problem(
                request,
                400,
                "invalid-price",
                "Invalid Price",
                f"{exc.args[0]} must be a number or null.",
            )
        if (prompt_price is None) != (completion_price is None):
            return _problem(
                request,
                400,
                "incomplete-token-price",
                "Incomplete Token Price",
                "prompt_price_per_1m and completion_price_per_1m must both be set, or both left "
                "blank — a model can still be priced per-image only.",
            )

        await db.upsert_model_price_config(
            model_id,
            prompt_price_per_1m=prompt_price,
            completion_price_per_1m=completion_price,
            image_price=image_price,
        )
        # Apply immediately — the very next request for this model is priced
        # at the new rate, no restart required (mirrors budget.py's
        # cache-invalidation-on-write pattern for billing settings).
        pricing.get_pricing_table().set_price(
            model_id,
            prompt_price_per_1m=prompt_price,
            completion_price_per_1m=completion_price,
            image_price=image_price,
        )
        return {
            "model_id": model_id,
            "prompt_price_per_1m": prompt_price,
            "completion_price_per_1m": completion_price,
            "image_price": image_price,
            "source": "db",
        }

    async def _modality_of(request: Request, model_id: str) -> str | None:
        """This model's modality, for restoring its base price — PRM-124.

        The gateway's own registry answers instantly but only knows models with
        a running instance, and most of the pricing table is models that have
        never been started (PRM-123). So fall back to the catalog, which is one
        round trip on a button click nobody presses in a loop.
        """
        registry = getattr(request.app.state, "registry", None)
        if registry is not None:
            for entry in getattr(registry, "_models", {}).values():
                if model_id in (entry.model_id, entry.id):
                    return str(entry.modality)

        settings: Settings = request.app.state.settings
        # PRM-134: the node list now comes from the coordinator with the manager
        # token, so the client this needed anyway is also what authenticates the
        # lookup. Guarded first: without it there is nothing to ask either way.
        if manager_client is None:
            return None
        try:
            nodes = await fetch_nodes(
                settings.resolved_fleet_url,
                await manager_client._headers(),
            )
            for _name, url in nodes:
                resp = await manager_client.get(url, "/v1/models")
                resp.raise_for_status()
                for entry in resp.json().get("models", []):
                    if entry.get("id") == model_id:
                        return entry.get("modality") or None
        except Exception as exc:  # noqa: BLE001
            logger.warning("billing.modality_lookup_failed", model_id=model_id, error=str(exc))
        return None

    @router.delete("/admin/api/billing/pricing/{model_id}")
    async def delete_pricing(model_id: str, request: Request) -> Any:
        """Restores this model's base price — PRM-124.

        This used to mean "remove the override", leaving the model unpriced
        unless pricing.yaml happened to carry it. That was right until PRM-120
        made "every model has a price" an invariant: since then, reset emptied
        the row and the dashboard showed a dash, which is the one state the
        table is no longer supposed to have.

        Reset now means what the button looks like it means — back to the
        per-modality base price, not back to nothing. Only if the modality
        cannot be determined at all does it leave the model unpriced, because
        inventing one would price it wrong on purpose.
        """
        if (err := _require_scope(request, "admin:write")) is not None:
            return err
        await db.delete_model_price_config(model_id)
        pricing.get_pricing_table().remove_price(model_id)

        modality = await _modality_of(request, model_id)
        base = pricing.default_price_for(modality) if modality else None
        if base is None:
            logger.info("billing.reset_left_unpriced", model_id=model_id, modality=modality)
            return Response(status_code=204)

        await db.upsert_model_price_config(
            model_id,
            prompt_price_per_1m=base.prompt_price_per_1m,
            completion_price_per_1m=base.completion_price_per_1m,
            image_price=base.image_price,
            is_default=True,
        )
        pricing.get_pricing_table().set_price(
            model_id,
            prompt_price_per_1m=base.prompt_price_per_1m,
            completion_price_per_1m=base.completion_price_per_1m,
            image_price=base.image_price,
        )
        return Response(status_code=204)

    return router
