"""The UI's engine list is the manager's, in another language — PRM-133.

`manager-core` decides what this platform can launch (`registry.BACKENDS`), and
the admin UI has to render the same set. Two lists in two languages cannot be
one list, so the next best thing is a test that fails the moment they disagree.

It matters more than the usual duplication: PRM-133 exists because the UI
offered engines a node cannot run. A UI list that has drifted from the
manager's offers engines *nothing* can run, which is the same defect one level
further out.
"""

from __future__ import annotations

import re
from pathlib import Path

_UI = Path(__file__).resolve().parents[1] / "admin-ui/src"
_MANAGER_REGISTRY = (
    Path(__file__).resolve().parents[2]
    / "runtime/manager/core/src/prometheus_manager_core/registry.py"
)


def _manager_backends() -> list[str]:
    src = _MANAGER_REGISTRY.read_text()
    match = re.search(r"^BACKENDS = \(([^)]*)\)", src, re.MULTILINE)
    assert match, "manager-core's BACKENDS tuple moved or changed shape"
    return re.findall(r'"([a-z0-9_]+)"', match.group(1))


def _ui_engines() -> list[str]:
    src = (_UI / "lib/engines.ts").read_text()
    match = re.search(r"export const ENGINES: Backend\[\] = \[([^\]]*)\]", src)
    assert match, "lib/engines.ts's ENGINES array moved or changed shape"
    return re.findall(r'"([a-z0-9_]+)"', match.group(1))


def test_the_ui_offers_exactly_what_the_manager_can_launch() -> None:
    assert _ui_engines() == _manager_backends()


def test_every_engine_has_a_display_label() -> None:
    """A missing label renders `undefined` in a dropdown, not the raw id."""
    src = (_UI / "lib/engines.ts").read_text()
    match = re.search(r"export const ENGINE_LABELS[^{]*\{([^}]*)\}", src)
    assert match, "ENGINE_LABELS moved or changed shape"
    labelled = set(re.findall(r"^\s*([a-z0-9_]+):", match.group(1), re.MULTILINE))
    assert labelled == set(_ui_engines())


def test_the_backend_type_union_matches_the_list() -> None:
    """`Backend` is what typechecks; `ENGINES` is what renders. A union wider
    than the array lets a component hold an engine the picker never offers."""
    src = (_UI / "types/instance.ts").read_text()
    match = re.search(r"export type Backend = ([^;]*);", src)
    assert match, "the Backend union moved or changed shape"
    assert set(re.findall(r'"([a-z0-9_]+)"', match.group(1))) == set(_ui_engines())


def test_no_component_keeps_its_own_copy_of_the_engine_list() -> None:
    """Both modals used to declare `const BACKENDS = [...]` locally.

    That was harmless while each rendered the whole list unconditionally. It
    stops being harmless now that the list has to be filtered against a node's
    declaration, because a list that exists twice gets filtered once — which is
    the shape that cost PRM-118, PRM-127 and PRM-130.
    """
    offenders: list[str] = []
    for path in sorted(_UI.rglob("*.tsx")):
        for lineno, line in enumerate(path.read_text().splitlines(), 1):
            if re.search(r"(const|let)\s+\w*(BACKENDS|ENGINES)\w*\s*(:|=)", line):
                offenders.append(f"{path.relative_to(_UI)}:{lineno}")
    assert not offenders, f"engine list declared outside lib/engines.ts: {offenders}"


def test_no_backend_leaks_its_internal_id_as_a_provider_name() -> None:
    """`gen_ai.provider.name` is the product's name, not ours.

    `llama_cpp` is a directory name; `llama.cpp` is what the thing is called,
    and it is what a dashboard grouped by provider expects to find. An
    underscore in this value is our id leaking through — and correcting it
    later splits the series it has been accumulating.
    """
    import re
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    from prometheus_gateway.router import _provider_of  # noqa: PLC0415

    manager_backends = re.findall(
        r'"([a-z0-9_]+)"',
        re.search(r"^BACKENDS = \(([^)]*)\)", _MANAGER_REGISTRY.read_text(), re.MULTILINE).group(1),
    )
    offenders = {b: _provider_of(b) for b in manager_backends if "_" in _provider_of(b)}
    assert not offenders, f"backends whose provider name is still our id: {offenders}"


def test_the_ui_offers_exactly_the_modalities_the_registry_accepts() -> None:
    """Same trap as the engine list, one field over.

    PRM-136 added `classification` to MODALITIES. A UI that does not offer it
    cannot register the models the gateway now has a route for; a UI that
    offers one the registry rejects fails at save with a validation error.
    """
    manager_modalities = set(
        re.findall(
            r'"([a-z_]+)"',
            re.search(
                r"^MODALITIES = \(([^)]*)\)", _MANAGER_REGISTRY.read_text(), re.MULTILINE
            ).group(1),
        )
    )
    ui_union = set(
        re.findall(
            r'"([a-z_]+)"',
            re.search(
                r"export type Modality = ([^;]*);", (_UI / "types/instance.ts").read_text()
            ).group(1),
        )
    )
    ui_list = set(
        re.findall(
            r'"([a-z_]+)"',
            re.search(
                r"const MODALITIES: Modality\[\] = \[([^\]]*)\]",
                (_UI / "components/EditModelModal.tsx").read_text(),
            ).group(1),
        )
    )
    assert ui_union == manager_modalities
    assert ui_list == manager_modalities


def test_the_playground_picker_can_show_every_modality() -> None:
    """It used to hard-code three groups, so `rerank` had been invisible in the
    Playground since PRM-106 and nobody noticed — a model missing from a
    dropdown reads as a model nobody started, not as a bug.

    The picker now falls back to the raw modality rather than dropping the
    entry, so this asserts the labels exist for the modalities we have: a
    missing one shows as `zero_shot` instead of `Decision (zero-shot)`, which
    is ugly but still selectable.
    """
    src = (_UI / "components/PlaygroundModelPicker.tsx").read_text()
    labelled = set(
        re.findall(
            r"^\s*([a-z_]+):",
            re.search(r"GROUP_LABELS[^{]*\{([^}]*)\}", src).group(1),
            re.MULTILINE,
        )
    )
    manager_modalities = set(
        re.findall(
            r'"([a-z_]+)"',
            re.search(
                r"^MODALITIES = \(([^)]*)\)", _MANAGER_REGISTRY.read_text(), re.MULTILINE
            ).group(1),
        )
    )
    assert manager_modalities - labelled == set(), (
        f"modalities with no group label in the Playground picker: "
        f"{sorted(manager_modalities - labelled)}"
    )
