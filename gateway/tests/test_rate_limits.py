"""PRM-182: the `predict` bucket becomes configurable, and the list that nearly broke.

Implements: docs/roadmap.md — PRM-182.

`predict` has had its own rate-limit bucket since PRM-136 — `X-RateLimit-Scope`
says `predict` — and no way to set it, so it fell to the generic 60. Centinela
measured the consequence in C-01 §4: four searches exhausted it.

The interesting test here is not the feature. It is
`test_every_dashboard_field_is_a_real_column`, which pins a bug this change
nearly shipped: the slug-to-field relationship lived in two places, collapsing
them looked like removing a duplication, and the other consumer of the list reads
a database table that has no `predict` columns.
"""

from __future__ import annotations

# ── PRM-182: `predict` gets its own configurable bucket ──────────────────────


def test_predict_falls_back_to_the_global_limit_when_unset(settings):
    """Default behaviour is unchanged — which is the point of leaving it None."""
    from prometheus_gateway.rate_limit_middleware import RateLimitMiddleware

    mw = RateLimitMiddleware.__new__(RateLimitMiddleware)
    mw.settings = settings
    assert mw._resolve_limits("predict") == (settings.rate_limit_rpm, settings.rate_limit_tpm)


def test_predict_can_be_raised_without_raising_chat(settings):
    """Centinela's C-01 §4: four searches exhausted the generic 60, and raising it
    before this meant raising chat and embeddings with it."""
    from prometheus_gateway.rate_limit_middleware import RateLimitMiddleware

    settings.rate_limit_rpm_predict = 600
    mw = RateLimitMiddleware.__new__(RateLimitMiddleware)
    mw.settings = settings

    assert mw._resolve_limits("predict")[0] == 600
    assert mw._resolve_limits("chat_completions")[0] == settings.rate_limit_rpm
    assert mw._resolve_limits("default")[0] == settings.rate_limit_rpm


def test_every_slug_in_the_map_names_real_settings_fields(settings):
    """The map is the only place the slug-to-field relationship lives now."""
    from prometheus_gateway.rate_limits import ENDPOINT_LIMIT_FIELDS

    for slug, (rpm_field, tpm_field) in ENDPOINT_LIMIT_FIELDS.items():
        assert hasattr(settings, rpm_field), f"{slug}: {rpm_field}"
        assert hasattr(settings, tpm_field), f"{slug}: {tpm_field}"


def test_every_dashboard_field_is_a_real_column():
    """The bug PRM-182 nearly shipped, made impossible.

    `main.py` reads `RATE_LIMIT_FIELDS` off a `RateLimitConfig` row with
    `getattr`, so a name in that list which the table does not have is an
    `AttributeError` at startup — only on a deployment that has ever saved
    limits from the dashboard, which is the worst place for it to appear.
    Deriving the list from the resolution map would have done exactly that.
    """
    from prometheus_gateway.db import RateLimitConfig
    from prometheus_gateway.rate_limits import RATE_LIMIT_FIELDS

    columns = {c.name for c in RateLimitConfig.__table__.columns}
    missing = [f for f in RATE_LIMIT_FIELDS if f not in columns]
    assert not missing, f"not columns on rate_limit_config: {missing}"


def test_the_predict_bucket_is_not_dashboard_editable_yet(settings):
    """Stated as a test so the asymmetry is deliberate rather than forgotten.

    `predict` resolves from Settings (.env) but is not persisted, because that
    needs the migration and UI that PRM-181 carries anyway.
    """
    from prometheus_gateway.rate_limits import ENDPOINT_LIMIT_FIELDS, RATE_LIMIT_FIELDS

    predict_fields = ENDPOINT_LIMIT_FIELDS["predict"]
    assert all(f not in RATE_LIMIT_FIELDS for f in predict_fields)
