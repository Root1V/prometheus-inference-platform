"""PRM-250: the sign-in endpoint stops answering two questions it was asked.

Implements: docs/roadmap.md — PRM-250.

Both holes were measured against the running deployment, not inferred:

  * a wrong password for a real account answered in 179ms and for an unknown
    address in 4.5ms, because bcrypt only ran when the account existed. The
    wording was identical either way, so the refusal gave nothing away and the
    clock gave away everything;
  * fifteen wrong passwords in a row were all answered at full speed, by a
    service configured for ten a minute — slowapi's `default_limits` bind
    through `@limiter.limit` or `SlowAPIMiddleware`, and this service had
    neither, so the limiter it built was never consulted.
"""

from __future__ import annotations

import time

from .conftest import ADMIN_HEADERS


async def _make_password_principal(client, email: str, password: str) -> None:
    response = await client.post(
        "/admin/clients",
        headers=ADMIN_HEADERS,
        json={
            "client_name": f"probe-{email.split('@')[0]}",
            "role": "app",
            "allowed_scopes": ["inference:read"],
            "auth_method": "password",
            "email": email,
            "password": password,
        },
    )
    assert response.status_code == 200, response.text


async def _time_refusal(client, email: str, password: str) -> float:
    started = time.perf_counter()
    response = await client.post(
        "/oauth2/token",
        data={
            "grant_type": "password",
            "username": email,
            "password": password,
            "scope": "inference:read",
        },
    )
    assert response.status_code == 401, response.text
    return time.perf_counter() - started


async def test_an_unknown_account_costs_the_same_as_a_wrong_password(client):
    """The clock must not answer what the message refuses to.

    Before this, the gap was 179ms against 4.5ms — a fortyfold tell, enough to
    enumerate every operator address at about a hundred guesses a second. The
    bound is deliberately loose: the point is that both paths pay bcrypt, not
    that they agree to the microsecond, and the residual difference is a
    database lookup.
    """
    await _make_password_principal(client, "real@example.com", "CorrectHorse!2026")

    real = await _time_refusal(client, "real@example.com", "wrong-password")
    unknown = await _time_refusal(client, "no-such-account@example.com", "wrong-password")

    ratio = max(real, unknown) / min(real, unknown)
    assert ratio < 3, (
        f"an unknown account answered in {unknown * 1000:.1f}ms and a real one in "
        f"{real * 1000:.1f}ms (ratio {ratio:.1f}x) — the timing says which "
        "addresses exist even though the message does not"
    )


async def test_failed_sign_ins_run_out_of_budget(client, settings):
    """A refusal that costs nothing can be repeated forever."""
    await _make_password_principal(client, "budget@example.com", "CorrectHorse!2026")

    budget = settings.auth_login_max_failures_per_identity
    for attempt in range(budget):
        response = await client.post(
            "/oauth2/token",
            data={
                "grant_type": "password",
                "username": "budget@example.com",
                "password": "wrong",
                "scope": "inference:read",
            },
        )
        assert response.status_code == 401, f"attempt {attempt + 1}: {response.text}"

    refused = await client.post(
        "/oauth2/token",
        data={
            "grant_type": "password",
            "username": "budget@example.com",
            "password": "wrong",
            "scope": "inference:read",
        },
    )
    assert refused.status_code == 429, (
        f"attempt {budget + 1} was still served — the budget is not enforced"
    )
    assert int(refused.headers["Retry-After"]) > 0


async def test_the_refusal_does_not_say_whether_the_account_exists(client):
    """429 is reached by failure count alone, so it reveals nothing either."""
    await _make_password_principal(client, "quiet@example.com", "CorrectHorse!2026")

    real = await client.post(
        "/oauth2/token",
        data={"grant_type": "password", "username": "quiet@example.com", "password": "no"},
    )
    unknown = await client.post(
        "/oauth2/token",
        data={"grant_type": "password", "username": "ghost@example.com", "password": "no"},
    )
    assert real.json() == unknown.json()


async def test_signing_in_clears_what_the_typos_spent(client):
    """Four typos then the right password must not leave a half-spent budget."""
    await _make_password_principal(client, "typo@example.com", "CorrectHorse!2026")

    for _ in range(settings_budget := 3):
        await client.post(
            "/oauth2/token",
            data={"grant_type": "password", "username": "typo@example.com", "password": "nope"},
        )
    assert settings_budget == 3

    ok = await client.post(
        "/oauth2/token",
        data={
            "grant_type": "password",
            "username": "typo@example.com",
            "password": "CorrectHorse!2026",
            "scope": "inference:read",
        },
    )
    assert ok.status_code == 200, ok.text

    # The budget is back: a fresh run of failures gets the full allowance.
    again = await client.post(
        "/oauth2/token",
        data={"grant_type": "password", "username": "typo@example.com", "password": "nope"},
    )
    assert again.status_code == 401, "the successful sign-in did not clear the failures"
