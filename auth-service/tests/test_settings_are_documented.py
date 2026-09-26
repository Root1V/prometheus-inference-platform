"""Every required setting must be documented — PRM-147.

An outage cost two hours and its whole cause was configuration. Part of what
made it expensive was that the documentation was wrong in ways that read as
authoritative: `auth-service/.env.example` said the issuer had to match a value
in the repo-root `.env`, which this service never reads, and the README named
`AUTH_DATABASE_URL` (no such setting), listed the issuer as optional when the
service will not start without it, and omitted `SHARE_TOKEN_ENCRYPTION_KEY`
entirely — which is required, and validated for length.

Reconstructing the file from those two documents was therefore impossible. These
tests are the guard: a setting the service refuses to start without has to appear
in both places, and a name that stops existing has to stop being documented.
"""

from __future__ import annotations

import pathlib
import re

from prometheus_auth.config import Settings

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_README = _ROOT / "README.md"
_EXAMPLE = _ROOT / "auth-service" / ".env.example"


def _env_names() -> dict[str, bool]:
    """Every setting as its env-var name, mapped to whether it is required."""
    return {name.upper(): field.is_required() for name, field in Settings.model_fields.items()}


def _required() -> set[str]:
    names = {n for n, req in _env_names().items() if req}
    # Not required by the type, but the model validator refuses to start without
    # it — which is the same thing to anyone writing the file.
    names.add("SHARE_TOKEN_ENCRYPTION_KEY")
    return names


def test_the_example_file_carries_every_required_setting() -> None:
    text = _EXAMPLE.read_text()
    missing = sorted(n for n in _required() if n not in text)
    assert not missing, (
        f"auth-service/.env.example does not mention required settings: {missing}. "
        "Someone copying it gets a service that will not start."
    )


def test_the_readme_carries_every_required_setting() -> None:
    text = _README.read_text()
    missing = sorted(n for n in _required() if n not in text)
    assert not missing, f"README.md does not document required settings: {missing}"


def test_the_readme_documents_no_setting_that_does_not_exist() -> None:
    """The other direction, and the one that had three offenders.

    A documented name that the service does not read is worse than an omission:
    it is followed, it has no effect, and nothing reports it.
    """
    known = set(_env_names())
    # Names that belong to other components or to compose, and are documented
    # here on purpose.
    elsewhere = {
        "AUTH_TLS_CERT_HOST_PATH",
        "AUTH_TLS_KEY_HOST_PATH",
        "AUTH_DB_HOST_PATH",
        "AUTH_PORT",  # read by start.sh, not by Settings
    }
    documented = set(re.findall(r"`(AUTH_[A-Z0-9_]+|SHARE_TOKEN_[A-Z0-9_]+)`", _README.read_text()))
    # `AUTH_SERVICE_*` is the gateway's half of the pair — its base URLs for
    # calling this service, documented in the gateway's own table. Same prefix,
    # different reader.
    documented = {n for n in documented if not n.startswith("AUTH_SERVICE_")}
    phantom = sorted(n for n in documented - known - elsewhere if not n.endswith("_SECONDS"))
    assert not phantom, (
        f"README.md documents settings this service does not read: {phantom}. "
        "A name that is followed and has no effect is worse than a missing one."
    )


def test_the_example_does_not_claim_the_root_env_matters() -> None:
    """The specific false statement that cost the time.

    auth-service reads `auth-service/.env` and its own environment. A comment
    telling someone to keep the repo-root `.env` in sync sends them to verify
    against a file this service never opens — and the value there can differ
    from what is actually minted while looking correct.
    """
    text = _EXAMPLE.read_text()
    assert "all three must be identical" not in text, (
        "the example again tells the reader to match a value in the repo-root .env"
    )
