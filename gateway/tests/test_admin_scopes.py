"""The admin read surface, split by resource family — PRM-274.

Implements: docs/roadmap.md — PRM-274.

One scope guarded all 33 admin read endpoints, so a credential that needed the
node list also read every principal's scopes and emails and every client's
billing. The dashboard assistant (PRM-272) made that concrete: it needs four
endpoints and was issued a token that could reach thirty-three.

`admin:read` is kept as the parent that implies all five, which is what makes
the split free to adopt — no credential was reissued and the panel did not
change. It is also the split's one failure mode: an endpoint written against
the parent still works, so the decomposition decays in silence unless something
fails. That something is `test_no_admin_endpoint_requires_the_parent_scope`.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path

from prometheus_gateway.auth.claims import Claims

_ROOT = Path(__file__).resolve().parents[2]
_GUARDED = (
    "gateway/src/prometheus_gateway/admin/router.py",
    "gateway/src/prometheus_gateway/admin/billing_router.py",
    "gateway/src/prometheus_gateway/router.py",
    "gateway/src/prometheus_gateway/main.py",
)
_FINE_SCOPES = {
    "admin:fleet:read",
    "admin:billing:read",
    "admin:principals:read",
    "admin:policy:read",
    "admin:audit:read",
}

# `_require_scope(request, "...")` and `claims.has_scope("...")` — the two
# shapes every authorisation check in the gateway takes.
_GUARD_RE = re.compile(r'(?:_require_scope\(request, |has_scope\()"(admin:[a-z:]+)"')


def _claims(scope: str) -> Claims:
    now = datetime.now(UTC)
    return Claims(
        user_id="u",
        client_id="c",
        scope=scope,
        expires_at=now,
        issued_at=now,
        issuer="test",
    )


def _guards() -> list[str]:
    found: list[str] = []
    for name in _GUARDED:
        found += _GUARD_RE.findall((_ROOT / name).read_text())
    return found


def test_the_parent_scope_implies_every_fine_scope() -> None:
    """Every credential issued before the split keeps working, including the
    dashboard's own login, which asks for `admin:read admin:write`."""
    parent = _claims("admin:read")
    for scope in sorted(_FINE_SCOPES):
        assert parent.has_scope(scope), f"admin:read stopped implying {scope}"


def test_a_fine_scope_implies_nothing_else() -> None:
    """The point of the split. The assistant's token holds `admin:fleet:read`
    and that is the whole of its reach — not billing, not the principal list."""
    fleet = _claims("admin:fleet:read inference:read model:m")
    assert fleet.has_scope("admin:fleet:read")
    for scope in sorted(_FINE_SCOPES - {"admin:fleet:read"}):
        assert not fleet.has_scope(scope), f"admin:fleet:read leaked into {scope}"
    assert not fleet.has_scope("admin:read"), "a child implied its own parent"
    assert not fleet.has_scope("admin:write")


def test_no_admin_endpoint_requires_the_parent_scope() -> None:
    """The decay guard. `admin:read` still works everywhere by design, so an
    endpoint written against it raises no error and returns no wrong answer —
    it just quietly hands out all five families again."""
    parents = [s for s in _guards() if s == "admin:read"]
    assert parents == [], f"{len(parents)} guard(s) still require the parent scope"


def test_the_guard_scan_actually_finds_the_guards() -> None:
    """Without this, deleting every authorisation check in the gateway would
    make the test above pass. The count is the measured one, asserted as a
    floor rather than an equality so adding an endpoint is not a test failure.
    """
    found = _guards()
    assert len(found) >= 32, f"the guard scan found only {len(found)}"
    assert _FINE_SCOPES <= set(found), (
        f"no endpoint is guarded by {sorted(_FINE_SCOPES - set(found))}"
    )
