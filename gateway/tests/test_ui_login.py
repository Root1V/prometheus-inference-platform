"""PRM-249: the sign-in screen, and the promises it has to keep.

Implements: docs/roadmap.md — PRM-249.

Three of these guard decisions that are invisible in the running page until
they are already wrong, and one guards a decision the operator made about who
this screen is for.

Guarded from Python for the reason `test_ui_search_targets.py` is: the
dashboard has no test runner of its own, and nothing else checks that these
hold.
"""

from __future__ import annotations

import re
from pathlib import Path

_UI = Path(__file__).resolve().parents[1] / "admin-ui/src"


def _login() -> str:
    return (_UI / "routes/Login.tsx").read_text()


def test_the_dashboard_offers_no_client_credentials_sign_in():
    """A client id belongs to a software integration, not to a person.

    The page used to carry a second tab that exchanged a client id and secret
    for an admin token, which put a machine identity through a screen built for
    a human and asked an operator to decide which of two kinds of credential
    they were holding.
    """
    source = _login()
    for field in ("clientId", "clientSecret", "client_secret"):
        assert field not in source, (
            f"Login.tsx still references `{field}` — the dashboard signs people "
            "in by email only; integrations take their token from the "
            "auth-service's token endpoint."
        )


def test_the_sign_in_form_can_be_filled_by_a_password_manager():
    """The standard autocomplete tokens, which the form carried none of.

    Without them a password manager has to guess from field names and mostly
    declines, so every sign-in to an admin dashboard was typed by hand.
    """
    source = _login()
    for token in ('autoComplete="username"', 'autoComplete="current-password"'):
        assert token in source, f"Login.tsx is missing {token}"


def test_the_token_deadline_is_stored_and_not_just_the_token():
    """`expires_in` arrives with every token and was read by nothing.

    Storing it is what lets the app end a session on time instead of
    discovering the end as a failed request.
    """
    source = (_UI / "api/auth.ts").read_text()
    assert "expiresInSeconds" in source, (
        "storeToken no longer takes the lifetime — without it the three-hour "
        "deadline is invisible again and expiry goes back to arriving as a 401."
    )
    assert re.search(r"expires_in", source), "auth.ts ignores `expires_in`"


def test_a_refused_sign_in_is_not_explained_in_terms_of_client_credentials():
    """The auth-service answers both grants with one message, written for the
    machine one: a person who mistyped their password read "Invalid
    Credentials — Invalid client credentials."
    """
    source = (_UI / "api/auth.ts").read_text()
    message = re.search(
        r"if \(error\.response\?\.status === 401\) \{\s*return\s*\n?\s*\"([^\"]+)\"", source
    )
    assert message is not None, "describeLoginError no longer special-cases 401"
    assert "client" not in message.group(1).lower(), (
        f"the 401 message still talks about clients: {message.group(1)!r}"
    )


def test_an_expired_session_says_so_and_keeps_the_route():
    """An expiry and a sign-out land on the same screen and mean opposite
    things; the page used to say nothing about either, so an expiry read as the
    dashboard breaking.
    """
    source = _login()
    assert "sessionEnded" in source, "Login.tsx does not read why the session ended"
    assert "returnTo" in source, (
        "Login.tsx ignores the route the reader was on — signing in drops them "
        "at the overview instead of where they were interrupted."
    )
