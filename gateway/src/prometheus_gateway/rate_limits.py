"""RM-56 — live-editable rate limits.

The rate-limit middleware reads `self.settings.<field>` fresh on every
request, and that Settings object is the same instance exposed as
`app.state.settings`. So applying an admin-configured limit is just writing
the field: the very next request sees it, with no restart and no change to
the middleware itself.

Deliberately stateless — the env snapshot lives on `app.state`, not in a
module-level singleton, so apps built side by side in tests can't leak
limits into each other.
"""

from __future__ import annotations

from typing import Any

# PRM-182: which Settings fields override the global limit, per endpoint slug.
# The slugs are `rate_limit_middleware._endpoint_slug`'s.
#
# **One map, because this was two.** The slug-to-field relationship lived in an
# `if/elif` chain in the middleware *and* in the tuple below, and adding
# `predict` would have meant adding it to both — a fact in two places with
# nothing checking they agree, which is the defect this codebase keeps meeting.
# A new endpoint is now one entry, and a test walks the slugs the middleware can
# produce against this map.
ENDPOINT_LIMIT_FIELDS: dict[str, tuple[str, str]] = {
    "chat_completions": ("rate_limit_rpm_chat_completions", "rate_limit_tpm_chat_completions"),
    "admin": ("rate_limit_rpm_admin", "rate_limit_tpm_admin"),
    "predict": ("rate_limit_rpm_predict", "rate_limit_tpm_predict"),
}

# The exact Settings fields an operator can override **from the dashboard**, which
# is a different fact from the map above and is why it is not derived from it:
# these are persisted, so each one is a column on `db.RateLimitConfig`, and a name
# here that the table does not have breaks startup — `main.py` reads this list off
# a row with `getattr`.
#
# PRM-182 nearly shipped that bug: deriving this from `ENDPOINT_LIMIT_FIELDS`
# looked like removing a duplication and was actually adding `predict` to a list
# whose other consumer is a database. So `predict` is **.env-only for now**
# (`RATE_LIMIT_RPM_PREDICT` plus a restart), which is enough to raise it, and
# making it dashboard-editable is the same migration-and-UI work as PRM-181's
# per-client store — they belong in one change, not two.
#
# `test_rate_limits.py` now asserts every name here is a real column, so the next
# attempt fails in a test instead of at startup.
RATE_LIMIT_FIELDS: tuple[str, ...] = (
    "rate_limit_rpm",
    "rate_limit_tpm",
    "rate_limit_rpm_chat_completions",
    "rate_limit_tpm_chat_completions",
    "rate_limit_rpm_admin",
    "rate_limit_tpm_admin",
)

# Guards the operator's own way back in: the dashboard polls several
# endpoints every few seconds, so an admin bucket below this would 429 the
# very page needed to undo the mistake — leaving .env + a restart as the
# only escape, which is exactly what RM-56 exists to avoid.
MIN_ADMIN_RPM = 60


def snapshot_env_defaults(settings: Any) -> dict[str, int | None]:
    """Capture what .env said, before any override can overwrite it."""
    return {field: getattr(settings, field) for field in RATE_LIMIT_FIELDS}


def apply_limits(settings: Any, values: dict[str, int | None]) -> None:
    """Write limits onto the live Settings object. Only known fields are
    touched; anything missing from `values` is left as-is."""
    for field in RATE_LIMIT_FIELDS:
        if field in values:
            setattr(settings, field, values[field])


# PRM-229: every limit there is, grouped by the layer it belongs to.
#
# A separate fact from `RATE_LIMIT_FIELDS`, and for the reason PRM-182 wrote
# down: that list is what the dashboard may *edit*, so each name is a column on
# `db.RateLimitConfig` and a name it lacks breaks startup. This one is what the
# dashboard may *show*, which is everything — including the eleven that
# PRM-224 through PRM-228 added and that are `.env`-only until the migration
# that makes them editable.
#
# The grouping is the point. Before this the page showed two dimensions of one
# layer and called the per-endpoint value "per client", which is what a reader
# would then believe a client could consume.
LIMIT_LAYERS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (
        "platform",
        "Everyone at once. Protects the hardware; nobody's entitlement.",
        ("rate_limit_rpm_platform", "rate_limit_tpm_platform"),
    ),
    (
        "client",
        "One consumer, across every endpoint. What a client may use.",
        (
            "rate_limit_rpm_client",
            "rate_limit_tpm_client",
            "rate_limit_tpm_input",
            "rate_limit_tpm_output",
            "rate_limit_rpd",
            "rate_limit_tpd",
            "rate_limit_ipm",
        ),
    ),
    (
        "endpoint",
        "One consumer on one route. Tighter where a route is expensive.",
        (
            "rate_limit_rpm",
            "rate_limit_tpm",
            "rate_limit_rpm_chat_completions",
            "rate_limit_tpm_chat_completions",
            "rate_limit_rpm_predict",
            "rate_limit_tpm_predict",
            "rate_limit_rpm_admin",
            "rate_limit_tpm_admin",
        ),
    ),
)


# PRM-227 left the consumer layer always enforced with a value derived from
# the per-endpoint allowances, so `rate_limit_rpm_client` being None does not
# mean "no ceiling" — it means "this one, computed". Reporting the raw setting
# would have the page say a layer refuses nothing while it refuses at 360.
_DERIVED_CLIENT_DEFAULTS = {
    "rate_limit_rpm_client": "rate_limit_rpm",
    "rate_limit_tpm_client": "rate_limit_tpm",
}


def limits_by_layer(settings: Any, *, endpoint_count: int) -> list[dict[str, Any]]:
    """Every limit, by layer, with the value actually in force.

    `source` is the part worth having: `set` is a number someone chose,
    `derived` is one PRM-227 computes and enforces all the same, and `unset`
    is a dimension that genuinely refuses nothing. Collapsing the last two
    would make an enforced ceiling look like an absent one.
    """
    editable = set(RATE_LIMIT_FIELDS)
    layers: list[dict[str, Any]] = []
    for layer, what, fields in LIMIT_LAYERS:
        entries = []
        for field in fields:
            raw = getattr(settings, field, None)
            source = "set" if raw is not None else "unset"
            value = raw
            if raw is None and (base := _DERIVED_CLIENT_DEFAULTS.get(field)):
                value = getattr(settings, base, 0) * endpoint_count
                source = "derived"
            entries.append(
                {
                    "field": field,
                    "value": value,
                    "source": source,
                    "editable": field in editable,
                }
            )
        layers.append({"layer": layer, "what": what, "fields": entries})
    return layers


def current_limits(settings: Any) -> dict[str, int | None]:
    return {field: getattr(settings, field) for field in RATE_LIMIT_FIELDS}
