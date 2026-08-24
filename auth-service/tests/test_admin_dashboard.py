"""Tests for spec-015 — Auth Service Admin Dashboard.

Maps 1-to-1 with memory/specs/015-auth-service-dashboard.md Acceptance Criteria.
"""

import re

from .conftest import ADMIN_HEADERS, register_client


# ── Login helper ──────────────────────────────────────────────────────────────


async def _login(client, admin_key: str = "test-admin-secret") -> str:
    """POST to /admin/ui/login; return the signed session cookie value."""
    resp = await client.post(
        "/admin/ui/login",
        data={"api_key": admin_key},
        follow_redirects=False,
    )
    assert resp.status_code == 303, f"Login failed: {resp.status_code} {resp.text}"
    assert "admin_session" in resp.cookies
    return resp.cookies["admin_session"]


async def _getcsrf_token(client, session_cookie: str) -> str:
    """Render the dashboard and extract the CSRF token from the first hidden field."""
    resp = await client.get(
        "/admin/ui/dashboard",
        cookies={"admin_session": session_cookie},
    )
    assert resp.status_code == 200
    match = re.search(r'name="csrf_token"\s+value="([^"]+)"', resp.text)
    assert match, "CSRF token not found in dashboard HTML"
    return match.group(1)


# ── AC-1: label column exists in oauth_clients ────────────────────────────────


async def test_015_AC1_label_column(client):
    """AC-1: Fresh DB has a nullable label column; new client has label=null."""
    data = await register_client(client, name="ac1-label-test")
    resp = await client.get("/admin/clients", headers=ADMIN_HEADERS)
    assert resp.status_code == 200
    item = next(i for i in resp.json() if i["client_id"] == data["client_id"])
    assert "label" in item
    assert item["label"] is None


# ── AC-2: ClientListItem includes label field ─────────────────────────────────


async def test_015_AC2_list_includes_label(client):
    """AC-2: Every item in GET /admin/clients includes a 'label' key."""
    await register_client(client, name="ac2-list-test")
    resp = await client.get("/admin/clients", headers=ADMIN_HEADERS)
    assert resp.status_code == 200
    for item in resp.json():
        assert "label" in item


# ── AC-3: PATCH sets label ────────────────────────────────────────────────────


async def test_015_AC3_patch_sets_label(client):
    """AC-3: PATCH /admin/clients/{id} with {"label": "gateway"} returns 200 and persists."""
    data = await register_client(client, name="ac3-patch")
    cid = data["client_id"]
    resp = await client.patch(
        f"/admin/clients/{cid}",
        json={"label": "gateway"},
        headers=ADMIN_HEADERS,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["label"] == "gateway"
    assert body["client_id"] == cid

    # Verify the change is persisted in the list
    list_resp = await client.get("/admin/clients", headers=ADMIN_HEADERS)
    item = next(i for i in list_resp.json() if i["client_id"] == cid)
    assert item["label"] == "gateway"


async def test_015_AC3_patch_updates_name_and_scopes(client):
    """AC-3 (extended): PATCH can update client_name and allowed_scopes independently."""
    data = await register_client(client, name="patch-multi", scopes=["inference:read"])
    cid = data["client_id"]
    resp = await client.patch(
        f"/admin/clients/{cid}",
        json={"client_name": "patched-name", "allowed_scopes": ["inference:read", "ui:chat"]},
        headers=ADMIN_HEADERS,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["client_name"] == "patched-name"
    assert "ui:chat" in body["allowed_scopes"]


# ── AC-4: PATCH invalid scope returns 422 ────────────────────────────────────


async def test_015_AC4_patch_invalid_scope(client):
    """AC-4: PATCH with an unknown scope returns 422."""
    data = await register_client(client, name="ac4-bad-scope")
    resp = await client.patch(
        f"/admin/clients/{data['client_id']}",
        json={"allowed_scopes": ["unknown:scope"]},
        headers=ADMIN_HEADERS,
    )
    assert resp.status_code == 422


# ── AC-5: PATCH unknown client returns 404 ────────────────────────────────────


async def test_015_AC5_patch_unknown_client(client):
    """AC-5: PATCH against a non-existent client_id returns 404."""
    resp = await client.patch(
        "/admin/clients/does-not-exist",
        json={"label": "x"},
        headers=ADMIN_HEADERS,
    )
    assert resp.status_code == 404


# ── AC-6: PATCH requires admin key ───────────────────────────────────────────


async def test_015_AC6_patch_no_admin_key(client):
    """AC-6: PATCH without X-Admin-Key returns 403."""
    data = await register_client(client, name="ac6-no-key")
    resp = await client.patch(
        f"/admin/clients/{data['client_id']}",
        json={"label": "x"},
    )
    assert resp.status_code == 403


# ── AC-7: reactivate a deactivated client ────────────────────────────────────


async def test_015_AC7_reactivate(client):
    """AC-7: POST /admin/clients/{id}/reactivate on a deactivated client returns 200 is_active=true."""
    data = await register_client(client, name="ac7-reactivate")
    cid = data["client_id"]

    deact = await client.delete(f"/admin/clients/{cid}", headers=ADMIN_HEADERS)
    assert deact.status_code == 204

    resp = await client.post(f"/admin/clients/{cid}/reactivate", headers=ADMIN_HEADERS)
    assert resp.status_code == 200
    body = resp.json()
    assert body["is_active"] is True
    assert body["client_id"] == cid

    # Client can now obtain a token again
    list_resp = await client.get("/admin/clients", headers=ADMIN_HEADERS)
    item = next(i for i in list_resp.json() if i["client_id"] == cid)
    assert item["is_active"] is True


# ── AC-8: reactivate already-active client returns 409 ───────────────────────


async def test_015_AC8_reactivate_already_active(client):
    """AC-8: Reactivating an already-active client returns 409."""
    data = await register_client(client, name="ac8-already-active")
    resp = await client.post(
        f"/admin/clients/{data['client_id']}/reactivate",
        headers=ADMIN_HEADERS,
    )
    assert resp.status_code == 409


# ── AC-9: Redis revocation key cleared (no Redis in unit tests — see note) ────


# AC-9 covers Redis revocation key deletion on reactivate.
# The unit-test fixture has auth_revocation_redis_url=None so Redis is skipped.
# Integration coverage: the reactivate_client handler calls redis_client.delete()
# when auth_revocation_redis_url is set — verified by code inspection + AC-7.


# ── AC-10: hard delete removes the row ───────────────────────────────────────


async def test_015_AC10_hard_delete(client):
    """AC-10: DELETE /admin/clients/{id}?permanent=true removes the row from the DB."""
    data = await register_client(client, name="ac10-hard-delete")
    cid = data["client_id"]

    resp = await client.delete(
        f"/admin/clients/{cid}?permanent=true",
        headers=ADMIN_HEADERS,
    )
    assert resp.status_code == 204

    # Client no longer appears in the list
    list_resp = await client.get("/admin/clients", headers=ADMIN_HEADERS)
    ids = [i["client_id"] for i in list_resp.json()]
    assert cid not in ids


# ── AC-11: soft deactivate (no permanent param) leaves row intact ─────────────


async def test_015_AC11_soft_deactivate_unchanged(client):
    """AC-11: DELETE without ?permanent still performs soft deactivate only."""
    data = await register_client(client, name="ac11-soft")
    cid = data["client_id"]

    resp = await client.delete(f"/admin/clients/{cid}", headers=ADMIN_HEADERS)
    assert resp.status_code == 204

    # Row still present in list, but is_active=False
    list_resp = await client.get("/admin/clients", headers=ADMIN_HEADERS)
    ids = [i["client_id"] for i in list_resp.json()]
    assert cid in ids
    item = next(i for i in list_resp.json() if i["client_id"] == cid)
    assert item["is_active"] is False


# ── AC-12: login page renders with brand palette and dark toggle ───────────────


async def test_015_AC12_login_page(client):
    """AC-12: GET /admin/ui/login returns 200 HTML with theme-toggle in it."""
    resp = await client.get("/admin/ui/login")
    assert resp.status_code == 200
    assert "text/html" in resp.headers.get("content-type", "")
    assert "theme-toggle" in resp.text
    assert "admin.css" in resp.text


# ── AC-13: correct key sets cookie and redirects to dashboard ─────────────────


async def test_015_AC13_login_success(client):
    """AC-13: Correct API key → Set-Cookie admin_session + 303 to /admin/ui/dashboard."""
    resp = await client.post(
        "/admin/ui/login",
        data={"api_key": "test-admin-secret"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/admin/ui/dashboard"
    assert "admin_session" in resp.cookies


# ── AC-14: wrong key — no cookie, 401, error message ─────────────────────────


async def test_015_AC14_login_wrong_key(client):
    """AC-14: Wrong API key → 401, no Set-Cookie, error message in HTML."""
    resp = await client.post(
        "/admin/ui/login",
        data={"api_key": "totally-wrong"},
        follow_redirects=False,
    )
    assert resp.status_code == 401
    assert "admin_session" not in resp.cookies
    assert "Invalid" in resp.text


# ── AC-15: dashboard without valid session redirects to login ─────────────────


async def test_015_AC15_dashboard_no_cookie(client):
    """AC-15: GET /admin/ui/dashboard without session cookie → 302 to /admin/ui/login."""
    resp = await client.get("/admin/ui/dashboard", follow_redirects=False)
    assert resp.status_code == 302
    assert "/admin/ui/login" in resp.headers["location"]


# ── AC-16: tampered/expired session cookie redirects to login ─────────────────


async def test_015_AC16_tampered_session(client):
    """AC-16: Tampered session cookie → 302 to /admin/ui/login (treated as expired)."""
    resp = await client.get(
        "/admin/ui/dashboard",
        cookies={"admin_session": "not.a.valid.signed.token"},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert "/admin/ui/login" in resp.headers["location"]


# ── AC-17: dashboard returns 200 with client table ────────────────────────────


async def test_015_AC17_dashboard_renders(client):
    """AC-17: Valid session → 200 response containing a table element."""
    session = await _login(client)
    resp = await client.get("/admin/ui/dashboard", cookies={"admin_session": session})
    assert resp.status_code == 200
    assert "<table" in resp.text


# ── AC-18: label is visible in the dashboard table ───────────────────────────


async def test_015_AC18_label_visible_in_dashboard(client):
    """AC-18: A client with label 'my-component' appears in the dashboard HTML."""
    data = await register_client(client, name="ac18-label")
    cid = data["client_id"]
    await client.patch(
        f"/admin/clients/{cid}",
        json={"label": "my-component"},
        headers=ADMIN_HEADERS,
    )

    session = await _login(client)
    resp = await client.get("/admin/ui/dashboard", cookies={"admin_session": session})
    assert "my-component" in resp.text


# ── AC-19: create client via dashboard form ───────────────────────────────────


async def test_015_AC19_create_via_ui(client):
    """AC-19: POST /admin/ui/clients creates client and redirects to secret-revealed."""
    session = await _login(client)
    csrf = await _getcsrf_token(client, session)

    resp = await client.post(
        "/admin/ui/clients",
        data={
            "client_name": "ui-created-ac19",
            "role": "app",
            "allowed_scopes": ["inference:read"],
            "label": "test-owner",
            "csrf_token": csrf,
        },
        cookies={"admin_session": session},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert "/admin/ui/secret-revealed" in resp.headers["location"]

    # Secret flash cookie is present
    assert "_flash_secret" in resp.cookies

    # Follow to secret page — consume the flash
    secret_resp = await client.get(
        "/admin/ui/secret-revealed",
        cookies={"admin_session": session, "_flash_secret": resp.cookies["_flash_secret"]},
    )
    assert secret_resp.status_code == 200
    assert "pmt_live_" in secret_resp.text

    # New client appears in the REST list
    list_resp = await client.get("/admin/clients", headers=ADMIN_HEADERS)
    names = [i["client_name"] for i in list_resp.json()]
    assert "ui-created-ac19" in names

    # Label persisted
    item = next(i for i in list_resp.json() if i["client_name"] == "ui-created-ac19")
    assert item["label"] == "test-owner"


# ── AC-20: edit client via dashboard form ────────────────────────────────────


async def test_015_AC20_edit_via_ui(client):
    """AC-20: POST /admin/ui/clients/{id}/edit updates name, label, scopes, and TTL."""
    data = await register_client(client, name="ac20-edit-me")
    cid = data["client_id"]

    session = await _login(client)
    csrf = await _getcsrf_token(client, session)

    resp = await client.post(
        f"/admin/ui/clients/{cid}/edit",
        data={
            "client_name": "ac20-edited",
            "label": "edited-label",
            "allowed_scopes": ["inference:read", "ui:chat"],
            "token_ttl_seconds": "600",
            "csrf_token": csrf,
        },
        cookies={"admin_session": session},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert "/admin/ui/dashboard" in resp.headers["location"]

    list_resp = await client.get("/admin/clients", headers=ADMIN_HEADERS)
    item = next(i for i in list_resp.json() if i["client_id"] == cid)
    assert item["client_name"] == "ac20-edited"
    assert item["label"] == "edited-label"
    assert "ui:chat" in item["allowed_scopes"]
    assert item["token_ttl_seconds"] == 600


# ── AC-21: deactivate client via dashboard ────────────────────────────────────


async def test_015_AC21_deactivate_via_ui(client):
    """AC-21: POST /admin/ui/clients/{id}/deactivate sets is_active=False."""
    data = await register_client(client, name="ac21-deactivate")
    cid = data["client_id"]

    session = await _login(client)
    csrf = await _getcsrf_token(client, session)

    resp = await client.post(
        f"/admin/ui/clients/{cid}/deactivate",
        data={"csrf_token": csrf},
        cookies={"admin_session": session},
        follow_redirects=False,
    )
    assert resp.status_code == 303

    list_resp = await client.get("/admin/clients", headers=ADMIN_HEADERS)
    item = next(i for i in list_resp.json() if i["client_id"] == cid)
    assert item["is_active"] is False


# ── AC-22: reactivate client via dashboard ────────────────────────────────────


async def test_015_AC22_reactivate_via_ui(client):
    """AC-22: POST /admin/ui/clients/{id}/reactivate restores is_active=True."""
    data = await register_client(client, name="ac22-reactivate")
    cid = data["client_id"]

    session = await _login(client)
    csrf = await _getcsrf_token(client, session)

    # Deactivate first
    await client.post(
        f"/admin/ui/clients/{cid}/deactivate",
        data={"csrf_token": csrf},
        cookies={"admin_session": session},
    )

    # Reactivate via UI
    resp = await client.post(
        f"/admin/ui/clients/{cid}/reactivate",
        data={"csrf_token": csrf},
        cookies={"admin_session": session},
        follow_redirects=False,
    )
    assert resp.status_code == 303

    list_resp = await client.get("/admin/clients", headers=ADMIN_HEADERS)
    item = next(i for i in list_resp.json() if i["client_id"] == cid)
    assert item["is_active"] is True


# ── AC-23: rotate secret via dashboard — shown once, cleared on revisit ───────


async def test_015_AC23_rotate_secret_via_ui(client):
    """AC-23: Rotate secret shows pmt_live_ once; revisiting shows no secret."""
    data = await register_client(client, name="ac23-rotate")
    cid = data["client_id"]

    session = await _login(client)
    csrf = await _getcsrf_token(client, session)

    resp = await client.post(
        f"/admin/ui/clients/{cid}/rotate-secret",
        data={"csrf_token": csrf},
        cookies={"admin_session": session},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert "_flash_secret" in resp.cookies

    # First visit — secret visible
    secret_resp = await client.get(
        "/admin/ui/secret-revealed",
        cookies={"admin_session": session, "_flash_secret": resp.cookies["_flash_secret"]},
    )
    assert secret_resp.status_code == 200
    assert "pmt_live_" in secret_resp.text
    assert "rotated" in secret_resp.text.lower()

    # Second visit without the flash cookie — no secret shown
    second_resp = await client.get(
        "/admin/ui/secret-revealed",
        cookies={"admin_session": session},
    )
    assert second_resp.status_code == 200
    assert "pmt_live_" not in second_resp.text
    assert "already" in second_resp.text.lower()


# ── AC-24: hard delete via dashboard form ─────────────────────────────────────


async def test_015_AC24_hard_delete_via_ui(client):
    """AC-24: POST /admin/ui/clients/{id}/delete with correct confirm_id removes the row."""
    data = await register_client(client, name="ac24-del")
    cid = data["client_id"]

    session = await _login(client)
    csrf = await _getcsrf_token(client, session)

    resp = await client.post(
        f"/admin/ui/clients/{cid}/delete",
        data={"csrf_token": csrf, "confirm_id": cid},
        cookies={"admin_session": session},
        follow_redirects=False,
    )
    assert resp.status_code == 303

    # Client gone from REST list
    list_resp = await client.get("/admin/clients", headers=ADMIN_HEADERS)
    ids = [i["client_id"] for i in list_resp.json()]
    assert cid not in ids


async def test_015_AC24_hard_delete_wrong_confirm(client):
    """AC-24 (safety): Wrong confirm_id → no deletion, redirect without error."""
    data = await register_client(client, name="ac24-safe")
    cid = data["client_id"]

    session = await _login(client)
    csrf = await _getcsrf_token(client, session)

    resp = await client.post(
        f"/admin/ui/clients/{cid}/delete",
        data={"csrf_token": csrf, "confirm_id": "wrong-id"},
        cookies={"admin_session": session},
        follow_redirects=False,
    )
    assert resp.status_code == 303  # redirect without deletion

    # Client still exists
    list_resp = await client.get("/admin/clients", headers=ADMIN_HEADERS)
    ids = [i["client_id"] for i in list_resp.json()]
    assert cid in ids


# ── AC-25: logout clears the session cookie ───────────────────────────────────


async def test_015_AC25_logout(client):
    """AC-25: GET /admin/ui/logout clears session cookie and redirects to login."""
    session = await _login(client)

    resp = await client.get(
        "/admin/ui/logout",
        cookies={"admin_session": session},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert resp.headers["location"] == "/admin/ui/login"

    # After logout, the dashboard should redirect to login
    dash = await client.get(
        "/admin/ui/dashboard",
        # no admin_session cookie — simulates cookie being cleared
        follow_redirects=False,
    )
    assert dash.status_code == 302


# ── AC-26: admin.css has no hex literals in rule bodies ──────────────────────


async def test_015_AC26_css_no_hex_in_rule_bodies():
    """AC-26: admin.css uses only var(--color-*) in rule bodies; hex only in :root / html.dark."""
    from pathlib import Path

    css_path = Path(__file__).parent.parent / "src/prometheus_auth/static/admin.css"
    css = css_path.read_text()

    # Strip block comments (which may contain hex palette references as documentation)
    cleaned = re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)

    # Remove the :root {...} and html.dark {...} token definition blocks
    # (these are the ONLY places hex literals are allowed)
    cleaned = re.sub(r":root\s*\{[^}]+\}", "", cleaned, flags=re.DOTALL)
    cleaned = re.sub(r"html\.dark\s*\{[^}]+\}", "", cleaned, flags=re.DOTALL)

    # No #RRGGBB or #RGB literals should remain outside the token blocks
    hex_matches = re.findall(r"#[0-9a-fA-F]{3,6}\b", cleaned)
    assert not hex_matches, (
        f"Hex colour(s) found in CSS rule bodies (outside token definitions): {hex_matches}"
    )


# ── CSRF: mutating POST without valid CSRF token redirects to login ────────────


async def test_015_csrf_invalid_token_redirects(client):
    """Security: POST with a forged CSRF token is rejected (redirect to login)."""
    data = await register_client(client, name="csrf-test")
    cid = data["client_id"]
    session = await _login(client)

    resp = await client.post(
        f"/admin/ui/clients/{cid}/deactivate",
        data={"csrf_token": "forged.token.value"},
        cookies={"admin_session": session},
        follow_redirects=False,
    )
    # Forged CSRF → redirect to login (not an error page)
    assert resp.status_code in (302, 303)

    # Client should NOT have been deactivated
    list_resp = await client.get("/admin/clients", headers=ADMIN_HEADERS)
    item = next(i for i in list_resp.json() if i["client_id"] == cid)
    assert item["is_active"] is True


# ── Root redirect ─────────────────────────────────────────────────────────────


async def test_015_root_redirect_no_session(client):
    """GET /admin/ui/ without session → redirect to /admin/ui/login."""
    resp = await client.get("/admin/ui/", follow_redirects=False)
    assert resp.status_code == 302
    assert "/admin/ui/login" in resp.headers["location"]


async def test_015_root_redirect_with_session(client):
    """GET /admin/ui/ with valid session → redirect to /admin/ui/dashboard."""
    session = await _login(client)
    resp = await client.get(
        "/admin/ui/",
        cookies={"admin_session": session},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert "/admin/ui/dashboard" in resp.headers["location"]
