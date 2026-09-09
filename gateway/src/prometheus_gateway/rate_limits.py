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

# The exact Settings fields an operator can override from the dashboard.
# Order matters only for display.
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
