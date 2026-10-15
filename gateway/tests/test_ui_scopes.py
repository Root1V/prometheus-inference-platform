"""The panel can express every scope auth-service accepts — PRM-267.

Implements: docs/roadmap.md — PRM-267.

`ScopePicker.tsx` said it mirrored `VALID_SCOPES` and nothing checked that it
did. `fleet:heartbeat` was added to auth-service by PRM-152 and never to the
list the panel renders, so the one scope a node credential needs could not be
granted from the only place credentials are created — the standing rule is that
no API creates them, which made the gap a hard stop rather than an
inconvenience: the iMac could not join the fleet at all.

The two pattern shapes (`model:<id>`, `node:<id>`) are not in that set and
cannot be enumerated — they get a picker each, and the tests below assert the
pickers exist rather than trusting a comment to say so.
"""

from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_PICKER = _ROOT / "gateway/admin-ui/src/components/ScopePicker.tsx"
_AUTH_SCHEMAS = _ROOT / "auth-service/src/prometheus_auth/schemas.py"


def _valid_scopes() -> set[str]:
    src = _AUTH_SCHEMAS.read_text()
    block = re.search(
        r"^VALID_SCOPES: frozenset\[str\] = frozenset\(\s*\{(.*?)^\s*\}\s*\)",
        src,
        re.MULTILINE | re.DOTALL,
    )
    assert block, "auth-service's VALID_SCOPES moved or changed shape"
    body = re.sub(r"#[^\n]*", "", block.group(1))  # comments are not members
    scopes = set(re.findall(r'"([a-z0-9:_-]+)"', body))
    assert len(scopes) > 5, "the VALID_SCOPES parse found almost nothing"
    return scopes


def _ui_fixed_scopes() -> set[str]:
    src = _PICKER.read_text()
    block = re.search(r"^const FIXED_SCOPES = \[(.*?)^\];", src, re.MULTILINE | re.DOTALL)
    assert block, "ScopePicker's FIXED_SCOPES array moved or changed shape"
    body = re.sub(r"//[^\n]*", "", block.group(1))
    return set(re.findall(r'"([a-z0-9:_-]+)"', body))


def test_the_panel_offers_exactly_the_scopes_auth_service_accepts() -> None:
    ui, auth = _ui_fixed_scopes(), _valid_scopes()
    assert ui - auth == set(), "the panel offers a scope auth-service would reject"
    assert auth - ui == set(), "auth-service accepts a scope the panel cannot grant"


def test_the_scope_a_node_credential_needs_is_grantable() -> None:
    """The defect, named directly. The set comparison above would pass if both
    sides lost `fleet:heartbeat`, and the fleet would be just as unjoinable."""
    assert "fleet:heartbeat" in _ui_fixed_scopes()


def test_the_panel_can_express_both_pattern_scopes() -> None:
    """`model:<id>` and `node:<id>` are matched by regex in auth-service, so
    they cannot be mirrored as a list — a picker builds them instead. This
    asserts both prefixes are actually constructed into a scope string."""
    src = _PICKER.read_text()
    for prefix, pattern in (
        ("model:", r"_MODEL_SCOPE_RE"),
        ("node:", r"_NODE_SCOPE_RE"),
    ):
        assert re.search(pattern, _AUTH_SCHEMAS.read_text()), (
            f"auth-service no longer pattern-matches {prefix}<id>"
        )
        assert f'= "{prefix}"' in src, f"the panel has no {prefix}<id> prefix constant"
    # And each prefix is interpolated into a scope the checkbox toggles, rather
    # than only declared. A constant nothing uses is how the last gap looked.
    assert src.count("${MODEL_SCOPE_PREFIX}") >= 2
    assert src.count("${NODE_SCOPE_PREFIX}") >= 2
