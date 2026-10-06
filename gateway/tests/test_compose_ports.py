"""PRM-192 — the port check asks the real question.

AC-8 of `memory/specs/004-podman-containerization.md` says Redis is
internal-only. It was enforced by grepping `podman-compose.yml` for the
substring `6379:6379`, and a loopback-only binding is spelled
`127.0.0.1:6379:6379` — which contains that substring. So the check refused the
safest binding available for exactly the same reason it refused the most
dangerous one, and PRM-191 had to route around it with an overlay file that
nothing then checked.

The first test below is the regression: the binding the old check rejected has
to be accepted, and the one it was actually written to stop has to still fail.

The parsing tests are not decoration. `ports:` has four spellings and an omitted
host address means **every interface** — so a parser that treats a missing field
as restrictive passes exactly the bindings this exists to catch.
"""

from __future__ import annotations

import textwrap

import pytest

from tests.check_compose_ports import Binding, bindings_in, compose_files, main


def _write(tmp_path, name, body):
    p = tmp_path / name
    p.write_text(textwrap.dedent(body))
    return p


# ── the regression that motivated this ─────────────────────────────────────


def test_a_loopback_redis_binding_is_accepted(tmp_path, capsys):
    """The exact binding the substring grep refused."""
    _write(
        tmp_path,
        "podman-compose.yml",
        """
        services:
          redis:
            image: redis:7-alpine
    """,
    )
    _write(
        tmp_path,
        "compose.baremetal.yml",
        """
        services:
          redis:
            ports:
              - "127.0.0.1:6379:6379"
    """,
    )
    assert main(tmp_path) == 0
    assert "PASS: AC-8" in capsys.readouterr().out


def test_a_redis_binding_on_every_interface_is_refused(tmp_path, capsys):
    """What AC-8 was written to stop, and still does."""
    _write(
        tmp_path,
        "podman-compose.yml",
        """
        services:
          redis:
            image: redis:7-alpine
    """,
    )
    _write(
        tmp_path,
        "compose.baremetal.yml",
        """
        services:
          redis:
            ports:
              - "6379:6379"
    """,
    )
    assert main(tmp_path) == 1
    assert "reachable off this host" in capsys.readouterr().out


def test_the_all_container_deployment_still_may_not_publish_redis(tmp_path, capsys):
    """Even on loopback. That deployment runs on servers where every service is
    a container on one internal network — a host binding there buys nothing and
    widens the surface, which is AC-8's original and correct intent."""
    _write(
        tmp_path,
        "podman-compose.yml",
        """
        services:
          redis:
            image: redis:7-alpine
            ports:
              - "127.0.0.1:6379:6379"
    """,
    )
    assert main(tmp_path) == 1
    assert "must leave Redis internal" in capsys.readouterr().out


def test_a_new_compose_file_cannot_escape_the_check(tmp_path, capsys):
    """How the overlay went unchecked: the old test named one file. This one
    globs, so a file added tomorrow is covered the day it lands."""
    _write(
        tmp_path,
        "podman-compose.yml",
        """
        services:
          redis:
            image: redis:7-alpine
    """,
    )
    _write(
        tmp_path,
        "compose.someone-added-this.yml",
        """
        services:
          redis:
            ports:
              - "0.0.0.0:6379:6379"
    """,
    )
    assert main(tmp_path) == 1
    assert "someone-added-this" in capsys.readouterr().out


def test_no_compose_file_at_all_is_a_failure_not_a_pass(tmp_path, capsys):
    """A check that silently passes when it found nothing to check is worse than
    no check: it reports safety it never established."""
    assert main(tmp_path) == 1
    assert "no compose file found" in capsys.readouterr().out


# ── the four spellings of `ports:` ─────────────────────────────────────────


@pytest.mark.parametrize(
    ("spec", "host_ip", "published", "target"),
    [
        ("8000:8000", "0.0.0.0", "8000", "8000"),
        ("127.0.0.1:6379:6379", "127.0.0.1", "6379", "6379"),
        ("6379", "0.0.0.0", "", "6379"),
        ("127.0.0.1:6379:6379/tcp", "127.0.0.1", "6379", "6379"),
        ("5000:5000/udp", "0.0.0.0", "5000", "5000"),
        ("[::1]:6379:6379", "::1", "6379", "6379"),
    ],
)
def test_short_syntax_is_read_correctly(tmp_path, spec, host_ip, published, target):
    f = _write(
        tmp_path,
        "compose.yml",
        f"""
        services:
          thing:
            ports:
              - "{spec}"
    """,
    )
    (b,) = bindings_in(f)
    assert (b.host_ip, b.published, b.target) == (host_ip, published, target)


def test_an_omitted_address_means_every_interface_not_loopback(tmp_path):
    """The direction of this default is the whole safety of the check. Treating
    a missing field as restrictive would pass every binding it exists to catch."""
    f = _write(
        tmp_path,
        "compose.yml",
        """
        services:
          thing:
            ports:
              - "8000:8000"
    """,
    )
    assert not bindings_in(f)[0].is_loopback


def test_long_syntax_is_read_too(tmp_path):
    """Compose's other spelling. A checker that only understood the short form
    would be blind to a file written in the long one."""
    f = _write(
        tmp_path,
        "compose.yml",
        """
        services:
          thing:
            ports:
              - target: 6379
                published: "6379"
                host_ip: 127.0.0.1
                protocol: tcp
    """,
    )
    (b,) = bindings_in(f)
    assert b.is_loopback and b.target == "6379"


def test_long_syntax_without_host_ip_is_every_interface(tmp_path):
    f = _write(
        tmp_path,
        "compose.yml",
        """
        services:
          thing:
            ports:
              - target: 6379
                published: "6379"
    """,
    )
    assert not bindings_in(f)[0].is_loopback


def test_a_service_with_no_ports_contributes_nothing(tmp_path):
    f = _write(
        tmp_path,
        "compose.yml",
        """
        services:
          redis:
            image: redis:7-alpine
            expose:
              - "6379"
    """,
    )
    assert bindings_in(f) == []


# ── what it reports without failing ────────────────────────────────────────


def test_other_lan_bindings_are_named_but_do_not_fail(tmp_path, capsys):
    """The gateway and manager publish on 0.0.0.0 today. Whether that should
    change is a security decision for a person, so this makes it visible on
    every push instead of either hiding it or failing the build over it."""
    _write(
        tmp_path,
        "podman-compose.yml",
        """
        services:
          redis:
            image: redis:7-alpine
          gateway:
            ports:
              - "8000:8000"
    """,
    )
    assert main(tmp_path) == 0
    out = capsys.readouterr().out
    assert "NOTE:" in out and "0.0.0.0:8000->8000" in out


def test_the_repos_own_compose_files_pass(tmp_path):
    """Run against the real files, so this suite fails the day someone widens
    one of them — which is the point of wiring it into the hook."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    assert compose_files(root), "the repo must have compose files to check"
    assert main(root) == 0


def test_binding_renders_readably():
    b = Binding(file="x.yml", service="redis", host_ip="127.0.0.1", published="6379", target="6379")
    assert str(b) == "x.yml:redis 127.0.0.1:6379->6379"
