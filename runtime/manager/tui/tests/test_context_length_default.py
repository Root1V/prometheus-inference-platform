"""PRM-194 — what `pmgr register` offers, and what it says when you overrule it."""

from __future__ import annotations

import struct

import pytest

from prometheus_manager_tui.cli import _context_cell, _resolve_context_length


def _gguf(arch: str, ctx: int) -> bytes:
    def kv(key: str, vt: int, payload: bytes) -> bytes:
        k = key.encode()
        return struct.pack("<Q", len(k)) + k + struct.pack("<I", vt) + payload

    a = arch.encode()
    return (
        b"GGUF"
        + struct.pack("<I", 3)
        + struct.pack("<Q", 0)
        + struct.pack("<Q", 2)
        + kv("general.architecture", 8, struct.pack("<Q", len(a)) + a)
        + kv(f"{arch}.context_length", 4, struct.pack("<I", ctx))
    )


@pytest.fixture
def model(tmp_path):
    p = tmp_path / "m.gguf"
    p.write_bytes(_gguf("llama", 40960))
    return str(p)


def test_the_default_offered_is_the_models_own(model, monkeypatch):
    """The fix itself: the prompt used to offer a flat 4096 regardless of the
    model, which is how nine of ten rows became a number nobody chose."""
    seen = {}

    def fake_prompt(text, default=None, type=None):
        seen["default"] = default
        return default

    monkeypatch.setattr("prometheus_manager_tui.cli.click.prompt", fake_prompt)
    assert _resolve_context_length(None, model) == 40960
    assert seen["default"] == 40960


def test_an_unreadable_model_falls_back_to_the_old_default(monkeypatch):
    """A model may be registered before it is downloaded. That must stay
    possible — it just no longer pretends the number came from anywhere."""
    monkeypatch.setattr(
        "prometheus_manager_tui.cli.click.prompt", lambda t, default=None, type=None: default
    )
    assert _resolve_context_length(None, "/does/not/exist.gguf") == 4096
    assert _resolve_context_length(None, "") == 4096


def test_an_explicit_value_is_obeyed(model):
    """Exceeding the trained length is a real capability and sometimes a
    deliberate choice. This warns; it does not refuse."""
    assert _resolve_context_length(131072, model) == 131072


def test_exceeding_the_trained_length_is_said_out_loud(model, capsys):
    """The failure that prompted this is silent: llama.cpp RoPE-scales rather
    than refusing, so the only signal available is one we print."""
    _resolve_context_length(131072, model)
    out = capsys.readouterr().out
    assert "40,960" in out and "131,072" in out
    assert "RoPE" in out


def test_a_value_within_the_trained_length_says_nothing(model, capsys):
    _resolve_context_length(8192, model)
    assert capsys.readouterr().out == ""


# ── the listing ────────────────────────────────────────────────────────────


def test_the_cell_shows_only_a_number_when_they_agree():
    assert _context_cell(40960, 40960) == "40,960"
    assert _context_cell(4096, None) == "4,096"


def test_the_cell_pairs_them_when_they_differ():
    assert "262,144" in _context_cell(4096, 262144)
    assert "4,096" in _context_cell(4096, 262144)


def test_an_over_committed_context_is_marked():
    """The only one of the three states that is wrong, so it is the only one
    that gets a colour."""
    assert "[red]" in _context_cell(131072, 40960)
    assert "[red]" not in _context_cell(4096, 262144)
