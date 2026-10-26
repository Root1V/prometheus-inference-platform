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

from prometheus_gateway.admin.names import client_directory, client_names

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


async def test_the_directory_carries_whether_active_and_nothing_else(respx_mock):
    """The roster an operator asks for — "who are the clients" — with whether
    each is enabled. Not its role: a role says which credentials are admins,
    which is the inventory this exists to keep back."""
    respx_mock.get(f"{_AUTH}/clients").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "client_id": "a",
                    "client_name": "Veritium",
                    "is_active": False,
                    "role": "admin",
                    "email": "ops@example.com",
                    "allowed_scopes": ["admin:read"],
                }
            ],
        )
    )
    assert await client_directory(_request()) == [
        {"client_id": "a", "name": "Veritium", "active": False}
    ]


async def test_a_label_does_not_hide_the_clients_own_name(respx_mock):
    """PRM-279: Argus was created with the label "AI for ITOps". The directory
    carried only the label, so asked for Argus's id the assistant answered that
    no such client existed. Both names travel when they differ; one that
    differs only in case, or a client with no label, carries one."""
    respx_mock.get(f"{_AUTH}/clients").mock(
        return_value=httpx.Response(
            200,
            json=[
                {"client_id": "e", "client_name": "Argus", "label": "AI for ITOps "},
                {"client_id": "v", "client_name": "veritium", "label": "Veritium"},
                {"client_id": "b", "client_name": "Video Vigilancia", "label": None},
            ],
        )
    )
    assert await client_directory(_request()) == [
        {"client_id": "e", "name": "AI for ITOps", "client_name": "Argus", "active": True},
        {"client_id": "v", "name": "Veritium", "active": True},
        {"client_id": "b", "name": "Video Vigilancia", "active": True},
    ]
    # The pages that only decorate an id keep showing the label.
    assert (await client_names(_request()))["e"] == "AI for ITOps"
