"""PRM-241: a day is a UTC day, everywhere.

Implements: docs/roadmap.md — PRM-241.

`db.record_usage` stamps `usage_events.day` from UTC, because that is what
billing is reconciled against. Anything that asks "what happened today" with
a *local* date disagrees with every row for as many hours as the offset — five
on the machine this was written on, which is why the Activity page emptied
itself every evening at seven and why its tests went red twenty minutes after
going green.

The trap was known: `test_default_prices.py` and `test_model_groups.py` each
carry a hand-written comment warning about it. A comment in two tests did not
stop a third caller walking into it, so this is the guard.
"""

from __future__ import annotations

import re
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src/prometheus_gateway"

# `date.today()` and `datetime.now()` without a timezone are the two ways to
# ask the machine what day it is. Both are wrong here for the same reason.
_LOCAL_CLOCK = re.compile(r"\bdate\.today\(\)|\bdatetime\.now\(\)(?!\s*\.\s*astimezone)")


def test_no_gateway_source_asks_the_local_clock_what_day_it_is():
    offenders = []
    for path in sorted(_SRC.rglob("*.py")):
        for number, line in enumerate(path.read_text().splitlines(), start=1):
            if line.lstrip().startswith("#"):
                continue
            if _LOCAL_CLOCK.search(line):
                offenders.append(f"{path.relative_to(_SRC)}:{number}: {line.strip()}")
    assert not offenders, (
        "a local date disagrees with `usage_events.day`, which is stamped in UTC, "
        "for as many hours as the machine's offset:\n  " + "\n  ".join(offenders)
    )


def test_the_usage_row_still_stamps_its_day_in_utc():
    """The fact the rule rests on. If this ever changes, the rule inverts and
    every caller above has to change with it — which is why it is asserted
    here rather than assumed."""
    source = (_SRC / "db.py").read_text()
    assert "d = day or datetime.now(tz=timezone.utc).date()" in source
