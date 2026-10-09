"""PRM-247: every searchable feature lands somewhere that exists.

Implements: docs/roadmap.md — PRM-247.

The dashboard's search can reach a setting two scrolls down a page of
seventeen inputs, so a result carries `?focus=<key>` and the destination
rings the element marked `data-focus="<key>"`. An entry whose key nobody
marked still navigates — it just lands on the page and highlights nothing,
which looks like the search being wrong about where the thing is.

Guarded from Python for the reason `test_engine_list.py` is: the two halves
are in different files, one of them has no test runner, and nothing else
checks that they agree.
"""

from __future__ import annotations

import re
from pathlib import Path

_UI = Path(__file__).resolve().parents[1] / "admin-ui/src"


def _features() -> list[tuple[str, str]]:
    """(label, to) for each entry in the catalogue."""
    source = (_UI / "lib/features.ts").read_text()
    return re.findall(
        r'label:\s*"([^"]+)",\s*\n\s*where:[^\n]*\n\s*keywords:[^\n]*\n\s*to:\s*([^\n]+),', source
    )


def test_every_focus_key_is_marked_on_some_page():
    marked = set()
    for path in _UI.rglob("*.tsx"):
        marked.update(re.findall(r'data-focus="([^"]+)"', path.read_text()))

    source = (_UI / "lib/features.ts").read_text()
    wanted = set(re.findall(r'focusLink\("[^"]+",\s*"([^"]+)"\)', source))

    missing = sorted(wanted - marked)
    assert not missing, (
        "the search offers these and no page marks them, so the result lands "
        f"and highlights nothing: {missing}"
    )


def test_every_feature_points_at_a_route_the_app_serves():
    routes = set(re.findall(r'path="(/[^"]*)"', (_UI / "App.tsx").read_text()))
    source = (_UI / "lib/features.ts").read_text()
    targets = re.findall(r'to:\s*(?:focusLink\(")?(/[a-z-]*)', source)

    unknown = sorted({t for t in targets if t not in routes})
    assert not unknown, f"the search points at routes that do not exist: {unknown}"


def test_the_catalogue_is_not_empty_and_carries_keywords():
    """Keywords are the reason it is a catalogue and not a menu: the point is
    reaching a setting by the word the reader has, not the one we chose."""
    source = (_UI / "lib/features.ts").read_text()
    entries = re.findall(r"keywords:\s*\"([^\"]+)\"", source)
    assert len(entries) >= 10
    assert all(len(k.split()) >= 3 for k in entries), (
        "an entry with one or two keywords is only findable by its own name"
    )
