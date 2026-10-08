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

# The exact Settings fields an operator can override **from the dashboard**,
# which is a different fact from the map above and is why it is not derived
# from it: these are persisted, so each one is a column on
# `db.RateLimitConfig`, and a name here that the table does not have breaks
# startup — `main.py` reads this list off a row with `getattr`.
#
# PRM-182 nearly shipped that bug: deriving this from `ENDPOINT_LIMIT_FIELDS`
# looked like removing a duplication and was actually adding `predict` to a list
# whose other consumer is a database.
#
# PRM-232 is the migration that made the list safe to grow, so it now holds all
# seventeen — the six RM-56 started with, `predict`'s pair that PRM-182 left for
# this change, and the nine PRM-224..228 added and could not persist. The order
# is `LIMIT_LAYERS`' order, broadest first, because the page reads it that way.
#
# `test_rate_limits.py` asserts every name here is a real column, so the next
# attempt to grow it without a migration fails in a test instead of at startup.
RATE_LIMIT_FIELDS: tuple[str, ...] = (
    "rate_limit_rpm_platform",
    "rate_limit_tpm_platform",
    "rate_limit_rpm_client",
    "rate_limit_tpm_client",
    "rate_limit_tpm_input",
    "rate_limit_tpm_output",
    "rate_limit_rpd",
    "rate_limit_tpd",
    "rate_limit_ipm",
    "rate_limit_rpm",
    "rate_limit_tpm",
    "rate_limit_rpm_chat_completions",
    "rate_limit_tpm_chat_completions",
    "rate_limit_rpm_predict",
    "rate_limit_tpm_predict",
    "rate_limit_rpm_admin",
    "rate_limit_tpm_admin",
)

# The two with no blank state. Everything else is optional and means something
# specific when unset — no ceiling, the derived default, or the global value —
# which is why "required" is a two-element set and not "all of them".
REQUIRED_RATE_LIMIT_FIELDS: frozenset[str] = frozenset({"rate_limit_rpm", "rate_limit_tpm"})

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


# ── PRM-230: which ceiling is refusing right now ─────────────────────────────

# Reserved identity/endpoint tokens, repeated from `rate_limiter` rather than
# imported: importing it here would make this module depend on redis for two
# string constants, and `test_rate_limits.py` asserts the two agree.
_PLATFORM_IDENTITY = "*platform*"
_ALL_ENDPOINTS = "*"


def endpoint_limits(settings: Any, endpoint_slug: str) -> tuple[int, int]:
    """(rpm, tpm) in force for one endpoint — the per-endpoint override, or the
    global when there isn't one.

    Implements: memory/specs/007-rate-limiting-and-throughput.md — AC-13

    Lives here because PRM-230 needs the same answer the middleware acts on,
    and a second copy of it is how this file's other maps came to need the
    warnings above them.
    """
    rpm = settings.rate_limit_rpm
    tpm = settings.rate_limit_tpm
    fields = ENDPOINT_LIMIT_FIELDS.get(endpoint_slug)
    if fields is not None:
        rpm_field, tpm_field = fields
        if (override := getattr(settings, rpm_field, None)) is not None:
            rpm = override
        if (override := getattr(settings, tpm_field, None)) is not None:
            tpm = override
    return rpm, tpm


def counter_layer(identity: str, endpoint: str) -> str:
    """Which layer a live counter belongs to — read off the key, not guessed.

    The key shape *is* the layer: the platform's own identity, the
    all-endpoints endpoint, or a real consumer on a real route. Deriving it
    from `LIMIT_LAYERS` instead would report where a setting is *grouped*,
    and those disagree — `rate_limit_tpm_input` sits in the client group and
    is checked per endpoint.
    """
    if identity == _PLATFORM_IDENTITY:
        return "platform"
    if endpoint == _ALL_ENDPOINTS:
        return "client"
    return "endpoint"


# Which setting bounds a counter, per layer. `None` means nothing checks this
# counter — a real state, and one this table exists to make visible: the
# router increments tpm_in/tpm_out at the all-endpoints key as well, while the
# middleware only ever reads them per endpoint. Those two rows are measured
# and unenforced, and a page that quietly dropped them would hide it.
# PRM-231: counter dimension → the column on `rate_limit_tiers` that bounds it
# client-wide. The names differ (`tpm_in` is the counter, `tpm_input` the
# setting) and inferring one from the other is how a mapping becomes a guess.
_CLIENT_TIER_FIELDS: dict[str, str] = {
    "tpm_in": "tpm_input",
    "tpm_out": "tpm_output",
    "rpd": "rpd",
    "tpd": "tpd",
    "ipm": "ipm",
}

_LIVE_LIMIT_FIELDS: dict[tuple[str, str], str | None] = {
    ("platform", "rpm"): "rate_limit_rpm_platform",
    ("platform", "tpm"): "rate_limit_tpm_platform",
    ("client", "rpm"): "rate_limit_rpm_client",
    ("client", "tpm"): "rate_limit_tpm_client",
    ("endpoint", "tpm_in"): "rate_limit_tpm_input",
    ("endpoint", "tpm_out"): "rate_limit_tpm_output",
    ("endpoint", "rpd"): "rate_limit_rpd",
    ("endpoint", "tpd"): "rate_limit_tpd",
    ("endpoint", "ipm"): "rate_limit_ipm",
}


def live_limit_for(
    settings: Any,
    *,
    layer: str,
    dimension: str,
    endpoint: str,
    endpoint_count: int,
    tier: Any = None,
) -> tuple[int | None, str]:
    """The ceiling a live counter is measured against, and where it came from.

    `source` is `tier` / `set` / `derived` / `none`, in the order the
    middleware resolves them. `none` is not "unlimited by policy" — it is "no
    check reads this counter", which is why it is worth printing.
    """
    if layer == "endpoint" and dimension in ("rpm", "tpm"):
        rpm, tpm = endpoint_limits(settings, endpoint)
        return (rpm if dimension == "rpm" else tpm), "set"

    if layer == "client" and dimension in ("rpm", "tpm"):
        if tier is not None and getattr(tier, dimension, None) is not None:
            return int(getattr(tier, dimension)), "tier"
        chosen = getattr(settings, f"rate_limit_{dimension}_client", None)
        if chosen is not None:
            return int(chosen), "set"
        # PRM-227's default: the sum of the per-endpoint allowances.
        return int(getattr(settings, f"rate_limit_{dimension}")) * endpoint_count, "derived"

    # PRM-231: the other five dimensions have a client-wide ceiling only when a
    # tier supplies one — the platform's own `.env` values are per endpoint and
    # are reported on those rows. Before this they were tier columns nothing
    # read, and these counters were the "counted, not checked" rows that said
    # so.
    if layer == "client" and (tier_field := _CLIENT_TIER_FIELDS.get(dimension)):
        tier_value = getattr(tier, tier_field, None) if tier is not None else None
        return (int(tier_value), "tier") if tier_value is not None else (None, "none")

    field = _LIVE_LIMIT_FIELDS.get((layer, dimension))
    if field is None:
        return None, "none"
    value = getattr(settings, field, None)
    return (int(value), "set") if value is not None else (None, "none")
