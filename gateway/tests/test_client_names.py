"""Client display names, resolved by the gateway — PRM-272.

Implements: docs/roadmap.md — PRM-272.

The assistant cannot read the principal list (every principal's email and
scopes), so the gateway hands out names itself. These pin down the three
properties that decision rests on: only the name leaves, a failure never takes
a page down, and the dashboard's polling does not become a call per poll.
"""

from __future__ import annotations

from types import SimpleNamespace

import httpx

from prometheus_gateway.admin.names import client_names

_AUTH = "http://auth-admin"


def _request(**overrides) -> SimpleNamespace:
    settings = SimpleNamespace(
        auth_service_admin_url=_AUTH,
        auth_service_admin_api_key="k",
        auth_service_tls_verify=False,
    )
    for key, value in overrides.items():
        setattr(settings, key, value)
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(settings=settings)))


async def test_only_the_name_leaves(respx_mock):
    """The label first, as the Activity page shows it; the client name when
    there is no label. Nothing else from the row — not the email, not the
    scopes — reaches the map."""
    respx_mock.get(f"{_AUTH}/clients").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "client_id": "a",
                    "client_name": "Lia Assistant",
                    "label": "Dashboard Assistant",
                    "email": "lia@example.com",
                    "allowed_scopes": ["admin:fleet:read"],
                },
                {"client_id": "b", "client_name": "Veritium", "label": None},
                # A label that is only whitespace is no label.
                {"client_id": "d", "client_name": "Video Vigilancia ", "label": "  "},
                {"client_id": "c", "client_name": "", "label": ""},
            ],
        )
    )
    names = await client_names(_request())
    assert names == {"a": "Dashboard Assistant", "b": "Veritium", "d": "Video Vigilancia"}


async def test_auth_service_down_is_no_names_not_an_error(respx_mock):
    respx_mock.get(f"{_AUTH}/clients").mock(side_effect=httpx.ConnectError("refused"))
    assert await client_names(_request()) == {}


async def test_no_admin_key_is_no_names():
    assert await client_names(_request(auth_service_admin_api_key=None)) == {}


async def test_the_dashboard_polling_does_not_call_per_poll(respx_mock):
    """Activity and Overview are polled every few seconds; the map is reused
    for a minute rather than fetched on each."""
    route = respx_mock.get(f"{_AUTH}/clients").mock(
        return_value=httpx.Response(200, json=[{"client_id": "a", "client_name": "A"}])
    )
    request = _request()
    for _ in range(5):
        assert await client_names(request) == {"a": "A"}
    assert route.call_count == 1


async def test_a_failure_is_not_cached(respx_mock):
    """A blip must not leave the pages nameless for a minute."""
    route = respx_mock.get(f"{_AUTH}/clients").mock(
        side_effect=[
            httpx.ConnectError("refused"),
            httpx.Response(200, json=[{"client_id": "a", "client_name": "A"}]),
        ]
    )
    request = _request()
    assert await client_names(request) == {}
    assert await client_names(request) == {"a": "A"}
    assert route.call_count == 2
