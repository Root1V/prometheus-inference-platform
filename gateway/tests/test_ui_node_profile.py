"""The panel shows every field a node reports — PRM-270.

Implements: docs/roadmap.md — PRM-270.

PRM-263 made a node read its own machine and send it with the heartbeat; the
coordinator stored it and `/admin/api/nodes` has returned it ever since,
untouched (`list_nodes` is a pass-through). The panel rendered none of it, and
nothing said so — the UI's `Node` type did not even declare the field, so the
data arrived in the browser and was dropped on the floor.

`DeviceProfile` is a dataclass in one language and an interface in another, and
`to_dict` is `asdict`, so the wire shape is the dataclass exactly. A field added
to the probe and not to the interface is invisible in the only place anyone
looks — the same silent drift `test_ui_scopes.py` and `test_engine_list.py`
guard, which is why this reads both sides rather than trusting the mirror.
"""

from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_PROFILE_PY = _ROOT / "runtime/manager/core/src/prometheus_manager_core/profile.py"
_NODE_TS = _ROOT / "gateway/admin-ui/src/types/node.ts"
_PANEL_TSX = _ROOT / "gateway/admin-ui/src/components/NodeProfile.tsx"


def _dataclass_fields(name: str) -> set[str]:
    """The annotated attributes of a `@dataclass`, which `asdict` puts on the wire."""
    src = _PROFILE_PY.read_text()
    block = re.search(rf"^class {name}:\n(.*?)(?=^\S|\Z)", src, re.MULTILINE | re.DOTALL)
    assert block, f"profile.py's {name} moved or changed shape"
    body = re.sub(r'"""(.*?)"""', "", block.group(1), flags=re.DOTALL)  # docstrings
    body = re.sub(r"#[^\n]*", "", body)
    fields = set(re.findall(r"^    ([a-z_][a-z0-9_]*): ", body, re.MULTILINE))
    assert fields, f"the {name} field parse found nothing"
    return fields


def _interface_fields(name: str) -> set[str]:
    src = _NODE_TS.read_text()
    block = re.search(rf"^export interface {name} \{{\n(.*?)^\}}", src, re.MULTILINE | re.DOTALL)
    assert block, f"node.ts's {name} interface moved or changed shape"
    body = re.sub(r"/\*\*.*?\*/", "", block.group(1), flags=re.DOTALL)  # doc comments
    return set(re.findall(r"^  ([a-z_][a-z0-9_]*)[?]?: ", body, re.MULTILINE))


def test_the_ui_declares_every_field_a_node_reports() -> None:
    for name in ("DeviceProfile", "Accelerator"):
        python, typescript = _dataclass_fields(name), _interface_fields(name)
        assert python - typescript == set(), (
            f"{name}: the node reports fields the panel does not declare"
        )
        assert typescript - python == set(), (
            f"{name}: the panel declares fields no node sends"
        )


def test_the_node_type_carries_the_profile_and_its_age() -> None:
    """Both, or neither is useful. The reading is cached for 5 minutes and a
    node that stopped reporting leaves its last one looking current, so the
    panel is built to refuse to show one without the other."""
    fields = _interface_fields("Node")
    assert {"profile", "profile_seen_at"} <= fields


def test_every_reported_field_is_rendered() -> None:
    """Declaring the field and never printing it is the same defect one step
    later — that is exactly what the pass-through API already did.

    Matches the bare name rather than `profile.<field>`: the panel reaches some
    fields by destructuring, and a guard that fails over an access style it did
    not anticipate is a guard people edit instead of believe (PRM-140). Comments
    are stripped first, so mentioning a field is not the same as showing it.
    """
    panel = _PANEL_TSX.read_text()
    code = re.sub(r"/\*.*?\*/|//[^\n]*|\{/\*.*?\*/\}", "", panel, flags=re.DOTALL)
    for field in _dataclass_fields("DeviceProfile"):
        assert field in code, f"the panel never shows {field}"


def test_the_panel_never_shows_a_profile_without_its_age() -> None:
    assert "profile_seen_at" in _PANEL_TSX.read_text()


def test_failed_probes_are_surfaced_not_swallowed() -> None:
    """profile.py: "an empty profile with no explanation is the shape that gets
    read as 'this machine has nothing'". A view that drops `probe_errors`
    reintroduces precisely that, and reads as a confident answer."""
    panel = _PANEL_TSX.read_text()
    assert "probe_errors.length > 0" in panel
    assert "probe_errors.map" in panel
