#!/usr/bin/env python3
"""Which compose services publish a port, and on which interface — PRM-192.

Replaces the string match that used to stand in for AC-8 of
`memory/specs/004-podman-containerization.md`. That criterion says Redis is
internal-only, and it was enforced by grepping `podman-compose.yml` for the
substring `6379:6379`. A loopback-only binding is spelled `127.0.0.1:6379:6379`,
which *contains* that substring, so the check refused the safest possible
binding for the same reason it refused the most dangerous one. It tested the
spelling; this tests the property.

Parsed with PyYAML — already present in every project venv — rather than with a
compose CLI, because the suite that calls this is explicitly static: it must run
with no container daemon, and on a host where none is installed.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

import yaml

LOOPBACK = {"127.0.0.1", "::1", "localhost"}


@dataclass(frozen=True)
class Binding:
    """One published port, and the interface it is published on."""

    file: str
    service: str
    host_ip: str
    published: str
    target: str

    @property
    def is_loopback(self) -> bool:
        return self.host_ip in LOOPBACK

    def __str__(self) -> str:
        return f"{self.file}:{self.service} {self.host_ip}:{self.published}->{self.target}"


def _parse_short(spec: str) -> tuple[str, str, str]:
    """`ports:` short syntax → (host_ip, published, target).

    The forms compose accepts, and what each means for exposure:

        "6379"                    → an ephemeral host port on every interface
        "8000:8000"               → host:container, every interface
        "127.0.0.1:6379:6379"     → ip:host:container, this host only
        "[::1]:6379:6379"         → the same, IPv6

    An omitted address means **all interfaces**, which is why the default is
    `0.0.0.0` here and not something softer: a missing field is the permissive
    case, and reading it as restrictive is how a check passes a binding it should
    have caught.
    """
    spec = str(spec).split("/")[0]  # drop a /tcp or /udp suffix

    host_ip = ""
    if spec.startswith("["):  # bracketed IPv6 literal
        close = spec.index("]")
        host_ip = spec[1:close]
        spec = spec[close + 2 :] if spec[close + 1 : close + 2] == ":" else spec[close + 1 :]

    parts = spec.split(":")
    if not host_ip:
        if len(parts) == 3:
            host_ip, parts = parts[0], parts[1:]
        else:
            host_ip = "0.0.0.0"

    if len(parts) == 1:
        return host_ip, "", parts[0]
    return host_ip, parts[0], parts[1]


def bindings_in(path: Path) -> list[Binding]:
    """Every published port in one compose file. Nothing merged, nothing inferred."""
    doc = yaml.safe_load(path.read_text()) or {}
    found: list[Binding] = []
    for service, body in (doc.get("services") or {}).items():
        for entry in (body or {}).get("ports") or []:
            if isinstance(entry, dict):  # long syntax
                found.append(
                    Binding(
                        file=path.name,
                        service=service,
                        host_ip=str(entry.get("host_ip") or "0.0.0.0"),
                        published=str(entry.get("published") or ""),
                        target=str(entry.get("target") or ""),
                    )
                )
            else:
                host_ip, published, target = _parse_short(entry)
                found.append(
                    Binding(
                        file=path.name,
                        service=service,
                        host_ip=host_ip,
                        published=published,
                        target=target,
                    )
                )
    return found


def compose_files(repo_root: Path) -> list[Path]:
    """Every compose file in the repo root, so a new one cannot escape the check
    simply by being new — which is how the overlay this replaces went unchecked."""
    return sorted(p for p in repo_root.glob("*compose*.y*ml") if p.is_file())


def main(repo_root: Path) -> int:
    files = compose_files(repo_root)
    if not files:
        print("  FAIL: AC-8: no compose file found to check")
        return 1

    all_bindings = [b for f in files for b in bindings_in(f)]
    failures = 0

    # ── AC-8, as the property rather than the spelling ───────────────────────
    redis_published = [b for b in all_bindings if b.target == "6379"]
    off_box = [b for b in redis_published if not b.is_loopback]
    if off_box:
        for b in off_box:
            print(f"  FAIL: AC-8: Redis reachable off this host — {b}")
        failures += len(off_box)
    else:
        where = ", ".join(str(b) for b in redis_published) or "nowhere"
        print(f"  PASS: AC-8: Redis published only on loopback ({where})")

    # The all-container deployment keeps Redis off the host entirely. The overlay
    # is a different deployment and is allowed the loopback binding above.
    base = repo_root / "podman-compose.yml"
    in_base = [b for b in bindings_in(base) if b.service == "redis"]
    if in_base:
        for b in in_base:
            print(f"  FAIL: AC-8: podman-compose.yml must leave Redis internal — {b}")
        failures += len(in_base)
    else:
        print("  PASS: AC-8: the all-container deployment leaves Redis internal")

    # ── Everything else, reported so the posture is visible ──────────────────
    exposed = [b for b in all_bindings if not b.is_loopback]
    if exposed:
        print(f"  NOTE: {len(exposed)} binding(s) reachable from the local network:")
        for b in exposed:
            print(f"        {b}")
        print("        See PRM-192 — a decision, not a finding; not failed here.")

    return 1 if failures else 0


if __name__ == "__main__":
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[2]
    raise SystemExit(main(root))
