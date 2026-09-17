#!/usr/bin/env -S uv run --script
"""Apply a price that was already in force to rows a bug billed as unpriced — PRM-117.

RM-70 lets an operator name a model once. Until PRM-113, usage rows were keyed
on that public name while `model_price_config` is keyed on the catalog id, so
naming a model made every later row miss its price and record `cost_usd = NULL`
— "no price configured", with nothing failing. PRM-113 fixed new rows and
`reunify_usage_history.py` re-keyed the old ones, but re-keying only repaired
the *identity*: the money stayed null.

This is not repricing. RM-60's rule is that a row is priced at the rate in
force when it was used, and never re-rated afterwards — so a row is only
touched when the price it should have had **already existed at the time**, and
a row older than its model's price is left alone and reported. That check is
the whole reason this is a separate script and not a migration.

    python scripts/reprice_unpriced_usage.py            # plan only
    python scripts/reprice_unpriced_usage.py --apply    # write

PRM-121 adds `--include-usage-older-than-its-price`, which switches off the
refusal above and rates old usage at today's price — including the per-modality
base price PRM-120 seeds. That is a deliberate exception to RM-60, safe only
while no client has been invoiced yet, because it changes what a past period
cost after the fact. It is a flag rather than the default so that using it is
recorded as a decision someone made.

Take a copy of gateway.db first. This edits rows you may have already invoiced.
"""

import argparse
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path


def _prices(conn: sqlite3.Connection) -> dict[str, dict]:
    cols = [r[1] for r in conn.execute("PRAGMA table_info(model_price_config)")]
    return {
        d["model_id"]: d
        for d in (dict(zip(cols, r, strict=True)) for r in conn.execute("SELECT * FROM model_price_config"))
    }


def _cost(kind: str, row: dict, price: dict) -> tuple[float, dict] | None:
    """The gateway's own arithmetic, kind for kind — see db.record_usage().

    Returns None where the gateway would also have recorded nothing: an image
    with no image price, or tokens with only half a token price. Half a price
    is not a discount.
    """
    if kind == "image":
        each = price["image_price"]
        if each is None:
            return None
        return each * row["image_count"], {"image_price_each": each}
    pp, cp = price["prompt_price_per_1m"], price["completion_price_per_1m"]
    if pp is None or cp is None:
        return None
    cost = (row["prompt_tokens"] * pp + row["completion_tokens"] * cp) / 1_000_000
    return cost, {"prompt_price_per_1m": pp, "completion_price_per_1m": cp}


# PRM-121: a row whose model has no price at all — a retired instance, say —
# still has a request_kind, and the kind maps to exactly one modality. Only
# consulted under --include-usage-older-than-its-price, where inventing a
# little is already the point.
_KIND_TO_MODALITY = {
    "chat": "text",
    "embedding": "embedding",
    "rerank": "rerank",
    "image": "image",
}


def _base_price_for_kind(kind: str) -> dict | None:
    """The per-modality base price PRM-120 seeds, as a price-row-shaped dict."""
    try:
        from prometheus_gateway import pricing
    except ImportError:
        return None
    base = pricing.default_price_for(_KIND_TO_MODALITY.get(kind, ""))
    if base is None:
        return None
    return {
        "prompt_price_per_1m": base.prompt_price_per_1m,
        "completion_price_per_1m": base.completion_price_per_1m,
        "image_price": base.image_price,
        "updated_at": "(base price for this modality)",
    }


def plan(db: Path, *, ignore_age: bool = False):
    """(repairable, too_old, unpriced) — the three ways a null row can end up."""
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        prices = _prices(conn)
        repairable, too_old, unpriced = [], [], defaultdict(lambda: [0, 0])
        for r in conn.execute("SELECT * FROM usage_events WHERE cost_usd IS NULL"):
            row = dict(r)
            # By either name, exactly as the gateway resolves it.
            price = prices.get(row["model_id"]) or prices.get(row["model_slug"] or "")
            if price is None and ignore_age:
                price = _base_price_for_kind(row["request_kind"])
            if price is None:
                key = row["model_id"]
                unpriced[key][0] += 1
                unpriced[key][1] += row["prompt_tokens"] + row["completion_tokens"]
                continue
            # RM-60: never apply a price to usage that predates it — unless the
            # operator has said, explicitly, that nobody has been invoiced yet.
            if not ignore_age and row["recorded_at"] <= price["updated_at"]:
                too_old.append((row, price))
                continue
            computed = _cost(row["request_kind"], row, price)
            if computed is None:
                unpriced[row["model_id"]][0] += 1
                continue
            cost, cols = computed
            repairable.append((row, cost, cols))
        return repairable, too_old, dict(unpriced)
    finally:
        conn.close()


def apply_changes(db: Path, repairable: list) -> tuple[int, int]:
    conn = sqlite3.connect(db)
    try:
        daily: dict[tuple, dict] = defaultdict(lambda: defaultdict(float))
        for row, cost, cols in repairable:
            sets = ", ".join(f"{c} = ?" for c in (*cols, "cost_usd"))
            conn.execute(
                f"UPDATE usage_events SET {sets} WHERE id = ?",  # noqa: S608
                (*cols.values(), cost, row["id"]),
            )
            key = (row["day"], row["client_id"], row["model_id"])
            daily[key]["cost_usd"] += cost
            if row["request_kind"] == "image":
                daily[key]["image_cost_usd"] += cost
            else:
                pp, cp = cols["prompt_price_per_1m"], cols["completion_price_per_1m"]
                daily[key]["prompt_cost_usd"] += row["prompt_tokens"] * pp / 1_000_000
                daily[key]["completion_cost_usd"] += row["completion_tokens"] * cp / 1_000_000

        # Added to the rollup, never recomputed from the events: 41 of this
        # deployment's 72 daily rows predate usage_events entirely, so a
        # rebuild would silently zero the history that only lives here.
        touched = 0
        for (day, client, model), deltas in daily.items():
            sets = ", ".join(f"{c} = COALESCE({c}, 0) + ?" for c in deltas)
            touched += conn.execute(
                f"UPDATE usage_daily SET {sets} WHERE day = ? AND client_id = ? AND model_id = ?",  # noqa: S608
                (*deltas.values(), day, client, model),
            ).rowcount
        conn.commit()
        return len(repairable), touched
    finally:
        conn.close()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--gateway-db", default="gateway.db", type=Path)
    ap.add_argument("--apply", action="store_true", help="write the changes")
    ap.add_argument(
        "--include-usage-older-than-its-price",
        action="store_true",
        dest="ignore_age",
        help=(
            "rate old usage at today's price, including PRM-120's per-modality base "
            "price. Changes what a past period cost — only correct while no client "
            "has been invoiced."
        ),
    )
    args = ap.parse_args()

    if not args.gateway_db.exists():
        print(f"not found: {args.gateway_db}", file=sys.stderr)
        return 2

    repairable, too_old, unpriced = plan(args.gateway_db, ignore_age=args.ignore_age)
    if args.ignore_age:
        print(
            "MODO PRM-121: se tarifa uso anterior a su propio precio.\n"
            "  Esto cambia lo que costo un periodo ya cerrado. Solo es correcto\n"
            "  mientras no se haya facturado a ningun cliente.\n"
        )

    if unpriced:
        print("Sin precio configurado — se quedan como están, que es lo correcto:")
        print(f"  {'modelo':38} {'filas':>6} {'tokens':>9}")
        for model, (n, tokens) in sorted(unpriced.items(), key=lambda kv: -kv[1][0]):
            print(f"  {model[:38]:38} {n:>6} {tokens:>9}")
        print()

    if too_old:
        print("Anteriores a su propio precio — NO se tocan (RM-60):")
        for row, price in too_old:
            print(
                f"  {row['model_id'][:34]:34} fila {row['recorded_at'][:19]} "
                f"< precio {price['updated_at'][:19]}"
            )
        print()

    if not repairable:
        print("Ninguna fila reparable.")
        return 0

    by_model: dict[str, list[int | float]] = defaultdict(lambda: [0, 0.0])
    for row, cost, _ in repairable:
        by_model[row["model_id"]][0] += 1
        by_model[row["model_id"]][1] += cost
    print(
        "A tarifar con el precio de HOY (puede no ser el vigente entonces):"
        if args.ignore_age
        else "Reparables — el precio ya regía cuando se facturaron:"
    )
    print(f"  {'modelo':38} {'filas':>6} {'USD':>14}")
    for model, (n, cost) in sorted(by_model.items()):
        print(f"  {model[:38]:38} {n:>6} {cost:>14.8f}")
    print(f"\n  {'TOTAL':38} {len(repairable):>6} {sum(c for _, c, _ in repairable):>14.8f}")

    if not args.apply:
        print("\nPlan solamente. Re-ejecuta con --apply para escribir, tras copiar gateway.db.")
        return 0

    events, daily = apply_changes(args.gateway_db, repairable)
    print(f"\nHecho. {events} evento(s) y {daily} fila(s) diaria(s) actualizadas.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
