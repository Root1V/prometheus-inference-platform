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


def current_limits(settings: Any) -> dict[str, int | None]:
    return {field: getattr(settings, field) for field in RATE_LIMIT_FIELDS}
