"""PRM-240: the dashboard stopped polling for things that do not change.

Implements: docs/roadmap.md — PRM-240.

`useUsers` was written for the Users page, where a stale list is a real
problem — you create a client and want to see it. Six other pages imported it
to turn a `client_id` into a name and inherited its five-second refetch.
Measured from the browser on the Activity page: 165 calls to
`/admin/api/users` in 15.4 minutes to paint three names that had not changed.

Guarded from Python for the reason `test_engine_list.py` is: the invariant
spans two languages, and the half that can enforce it is the half with a test
runner.
"""

from __future__ import annotations

import re
from pathlib import Path

_UI = Path(__file__).resolve().parents[1] / "admin-ui/src"

# The hooks whose data changes when somebody changes it, not on its own — so
# polling is something a caller opts into rather than inherits.
_OPT_IN_HOOKS = ("useUsers", "useModelCatalog")

# Who may ask for it, and why. Explicit so that adding a seventh polling page
# is an edit to this list with a reason beside it, rather than a line in a
# component nobody reviews again.
_LIVE_CALLERS = {
    "useUsers": {
        "routes/Users.tsx",  # the page that creates and edits them
    },
    "useModelCatalog": {
        "routes/Models.tsx",  # entries appear and change state while you watch
        "routes/Dashboard.tsx",  # the operator's live view of the fleet
    },
}


def _sources() -> dict[str, str]:
    return {
        str(path.relative_to(_UI)): path.read_text()
        for path in _UI.rglob("*.tsx")
        if path.is_file()
    }


def test_the_shared_hooks_do_not_poll_unless_asked():
    """`refetchInterval` must be behind the flag, not a constant."""
    for hook, module in (("useUsers", "api/users.ts"), ("useModelCatalog", "api/models.ts")):
        source = (_UI / module).read_text()
        body = source[source.index(f"export function {hook}(") :]
        body = body[: body.index("\n}\n")]
        assert "options: { live?: boolean }" in body, f"{hook} lost its opt-in"
        assert re.search(r"refetchInterval:\s*options\.live\s*\?", body), (
            f"{hook} polls unconditionally again — six pages import it for names"
        )


def test_only_the_pages_that_own_the_data_ask_it_to_poll():
    """Everyone else gets the same cached list without a timer behind it."""
    for hook in _OPT_IN_HOOKS:
        live_in = {
            name
            for name, source in _sources().items()
            if re.search(rf"{hook}\(\s*\{{\s*live:\s*true", source)
        }
        assert live_in == _LIVE_CALLERS[hook], (
            f"{hook}: polling callers changed. Expected {sorted(_LIVE_CALLERS[hook])}, "
            f"found {sorted(live_in)} — add it to _LIVE_CALLERS with the reason, or "
            f"drop the flag."
        )


def test_the_flag_does_not_leak_into_the_cache_key():
    """One key either way, so the seven pages reading names share one fetch.

    The way this breaks is `queryKey: ["users", live]`, which looks like a
    tidy refinement and quietly splits the cache in two: the polling page and
    the name-reading pages stop sharing, and the saving is spent on a second
    copy of the same list.
    """
    for hook, module, key in (
        ("useUsers", "api/users.ts", "USERS_KEY"),
        ("useModelCatalog", "api/models.ts", "CATALOG_KEY"),
    ):
        source = (_UI / module).read_text()
        body = source[source.index(f"export function {hook}(") :]
        body = body[: body.index("\n}\n")]
        assert f"queryKey: {key}," in body, f"{hook} no longer uses the shared key"
        assert "live" not in body.split("queryKey:")[1].split("\n")[0], (
            f"{hook} put the flag in its cache key — that splits the cache"
        )
