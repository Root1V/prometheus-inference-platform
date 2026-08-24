# See memory/specs/015-auth-service-dashboard.md — Admin web UI routes
# See memory/specs/016-credential-share-link.md — Share token generation and revocation
# Implements: AC-12 through AC-26 (spec-015), AC-1..AC-7, AC-19..AC-26 (spec-016)
import secrets
import uuid as _uuid_mod
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import bcrypt
import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import ClientRole, CredentialShareToken, OAuthClient, get_session_factory
from ..schemas import VALID_SCOPES, invalid_scopes
from ..share_crypto import encrypt_secret
from opentelemetry.trace import SpanKind, StatusCode

from ..telemetry import get_logger, get_tracer

logger = get_logger(__name__)
_tracer = get_tracer("auth-service.admin-ui")

router = APIRouter(prefix="/admin/ui", tags=["admin-ui"])

_TEMPLATE_DIR = Path(__file__).parent.parent / "templates"
_templates = Jinja2Templates(directory=str(_TEMPLATE_DIR))

_SESSION_COOKIE = "admin_session"
_SESSION_MAX_AGE = 3600  # seconds
_FLASH_COOKIE = "_flash_secret"
_FLASH_MAX_AGE = 300  # seconds

# ── Scope catalogue ───────────────────────────────────────────────────────────
# Each entry: (scope_id, description).  Sorted alphabetically for display.
SCOPE_DESCRIPTIONS: dict[str, str] = {
    "admin:models": "Manage model registry via the Gateway (add, remove, list models).",
    "admin:read": "Read gateway admin endpoints — backend list, usage summary.",
    "admin:usage": "Access detailed token consumption and usage statistics.",
    "backend-registry:read": "Query the llama-server Manager API to list registered backends.",
    "inference:read": "Send prompt requests and receive single-response completions.",
    "inference:stream": "Send prompt requests and receive streamed (SSE) completions.",
    "ui:chat": "Access the browser-based Web Chat UI proxy.",
}


# ── Jinja2 custom filter ──────────────────────────────────────────────────────


def _ttl_display(seconds: int) -> str:
    """Human-readable TTL for template rendering."""
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds % 3600 == 0:
        return f"{seconds // 3600}h"
    return f"{seconds // 60}m"


_LIMA_TZ = timezone(timedelta(hours=-5))  # America/Lima — UTC-5, no DST


def _lima_dt(dt: datetime | None) -> str:
    """Convert a UTC-aware datetime to Lima time (UTC-5) and format as 'YYYY-MM-DD HH:MM'."""
    if dt is None:
        return "—"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (
        dt.astimezone(_LIMA_TZ).strftime("%Y-%m-%d %H:%M:%S.")
        + f"{dt.astimezone(_LIMA_TZ).microsecond // 1000:03d}"
    )


_templates.env.filters["ttl"] = _ttl_display
_templates.env.filters["lima_dt"] = _lima_dt


# ── Session helpers ───────────────────────────────────────────────────────────


def _make_serializer(key: str, salt: str) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(key, salt=salt)


def _validate_session(request: Request) -> str | None:
    """Return signed session cookie value if valid; None otherwise.

    AC-15, AC-16: expired or tampered cookies return None → redirect to login.
    """
    cookie = request.cookies.get(_SESSION_COOKIE)
    if not cookie:
        return None
    settings = request.app.state.settings
    ser = _make_serializer(settings.auth_admin_api_key, "session")
    try:
        ser.loads(cookie, max_age=_SESSION_MAX_AGE)
        return cookie
    except (BadSignature, SignatureExpired):
        return None


def _login_redirect() -> RedirectResponse:
    r: RedirectResponse = RedirectResponse(url="/admin/ui/login", status_code=302)
    r.delete_cookie(_SESSION_COOKIE, path="/admin/ui")
    return r


# ── CSRF helpers ──────────────────────────────────────────────────────────────


def _make_csrf_token(settings: Any) -> str:
    """Generate a fresh signed CSRF token. Embedded in all mutating forms."""
    ser = _make_serializer(settings.auth_admin_api_key, "csrf")
    return ser.dumps({"v": 1})


def _verify_csrf(settings: Any, token: str) -> bool:
    """Validate a CSRF token. AC-spec (Security Considerations — CSRF)."""
    ser = _make_serializer(settings.auth_admin_api_key, "csrf")
    try:
        ser.loads(token, max_age=_SESSION_MAX_AGE)
        return True
    except (BadSignature, SignatureExpired):
        return False


# ── DB helper ─────────────────────────────────────────────────────────────────


async def _get_db() -> Any:
    async with get_session_factory()() as session:
        yield session


# ── Secret hashing (local copy — avoids cross-router coupling) ───────────────


def _hash_secret(plain: str) -> str:
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt(rounds=12)).decode()


def _parse_model_scopes(raw: str) -> list[str]:
    """ "llama3-8b qwen-coder-7b" -> ["model:llama3-8b", "model:qwen-coder-7b"]. RM-07."""
    return [f"model:{tok}" for tok in raw.split()]


def _model_scopes_display(scopes: list[str]) -> str:
    """Inverse of _parse_model_scopes, for pre-filling the edit form."""
    return " ".join(s.removeprefix("model:") for s in scopes if s.startswith("model:"))


# ── Root redirect ─────────────────────────────────────────────────────────────


@router.get("/")
async def admin_root(request: Request) -> RedirectResponse:
    """Redirect to dashboard if session valid, else login — AC-Q3."""
    if _validate_session(request):
        return RedirectResponse(url="/admin/ui/dashboard", status_code=302)
    return RedirectResponse(url="/admin/ui/login", status_code=302)


# ── Login ─────────────────────────────────────────────────────────────────────


@router.get("/login")
async def get_login(request: Request) -> Any:
    """AC-12: Render login page with brand palette and dark toggle."""
    return _templates.TemplateResponse(
        request,
        "admin_login.html",
        {"error": request.query_params.get("error")},
    )


@router.post("/login")
async def post_login(
    request: Request,
    api_key: str = Form(...),
) -> Any:
    """AC-13, AC-14: Validate admin API key; set signed session cookie on success.
    Implements: memory/specs/022-opentelemetry-sdk-instrumentation.md — G-25, AC-33, AC-34
    """
    with _tracer.start_as_current_span("admin.ui.login", kind=SpanKind.INTERNAL) as span:
        settings = request.app.state.settings
        # AC-14: constant-time comparison to prevent timing oracle
        if not secrets.compare_digest(api_key, settings.auth_admin_api_key):
            logger.warning(
                "admin_ui.login.failed",
                remote=request.client.host if request.client else "unknown",
            )
            # AC-34: auth_result='fail' + span ERROR status; key value NOT in attributes
            span.set_attribute("auth_result", "fail")
            span.set_status(StatusCode.ERROR, "invalid_admin_key")
            return _templates.TemplateResponse(
                request,
                "admin_login.html",
                {"error": "Invalid admin key. Please try again."},
                status_code=401,
            )

        ser = _make_serializer(settings.auth_admin_api_key, "session")
        token = ser.dumps("admin")
        response: Any = RedirectResponse(url="/admin/ui/dashboard", status_code=303)
        response.set_cookie(
            _SESSION_COOKIE,
            token,
            max_age=_SESSION_MAX_AGE,
            httponly=True,
            samesite="lax",
            path="/admin/ui",
        )
        logger.info("admin_ui.login.success")
        span.set_attribute("auth_result", "ok")
        return response


# ── Logout ────────────────────────────────────────────────────────────────────


@router.get("/logout")
async def logout(request: Request) -> RedirectResponse:
    """AC-25: Clear session cookie and redirect to login.
    Implements: memory/specs/022-opentelemetry-sdk-instrumentation.md — G-26, AC-35
    """
    with _tracer.start_as_current_span("admin.ui.logout", kind=SpanKind.INTERNAL) as span:
        r: RedirectResponse = RedirectResponse(url="/admin/ui/login", status_code=302)
        r.delete_cookie(_SESSION_COOKIE, path="/admin/ui")
        span.set_attribute("http.status_code", 302)
        return r


# ── Share status computation ──────────────────────────────────────────────────


def _share_status(st: CredentialShareToken) -> str:
    """Return the display status for a share token: active | used | revoked | expired."""
    if st.used_at is not None:
        return "used"
    if st.revoked_at is not None:
        return "revoked"
    exp = st.expires_at
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    if exp <= datetime.now(timezone.utc):
        return "expired"
    return "active"


async def _load_share_info(db: AsyncSession) -> "dict[str, dict[str, str]]":
    """Return {client_id: {status, id}} for the most recent share token per client."""
    tokens_result = await db.execute(
        select(CredentialShareToken).order_by(CredentialShareToken.created_at.desc())
    )
    all_tokens = tokens_result.scalars().all()
    info: dict[str, dict[str, str]] = {}
    for st in all_tokens:
        if st.client_id not in info:
            info[st.client_id] = {"status": _share_status(st), "id": st.id}
    return info


# ── Dashboard ─────────────────────────────────────────────────────────────────


@router.get("/dashboard")
async def get_dashboard(
    request: Request,
    db: AsyncSession = Depends(_get_db),
) -> Any:
    """AC-15, AC-16, AC-17: Render client table; requires valid session."""
    session = _validate_session(request)
    if not session:
        return _login_redirect()

    settings = request.app.state.settings
    result = await db.execute(select(OAuthClient).order_by(OAuthClient.created_at.desc()))
    clients = result.scalars().all()
    share_info = await _load_share_info(db)

    return _templates.TemplateResponse(
        request,
        "admin_dashboard.html",
        {
            "clients": clients,
            "roles": [r.value for r in ClientRole],
            "valid_scopes": sorted(VALID_SCOPES),
            "scope_descriptions": SCOPE_DESCRIPTIONS,
            "csrf_token": _make_csrf_token(settings),
            "create_error": None,
            "prefill": {},
            "share_info": share_info,
        },
    )


async def _render_dashboard(
    request: Request,
    db: AsyncSession,
    create_error: str | None = None,
    prefill: dict[str, str] | None = None,
) -> Any:
    """Shared render helper so POST handlers can return errors without redirect."""
    settings = request.app.state.settings
    result = await db.execute(select(OAuthClient).order_by(OAuthClient.created_at.desc()))
    clients = result.scalars().all()
    share_info = await _load_share_info(db)

    return _templates.TemplateResponse(
        request,
        "admin_dashboard.html",
        {
            "clients": clients,
            "roles": [r.value for r in ClientRole],
            "valid_scopes": sorted(VALID_SCOPES),
            "scope_descriptions": SCOPE_DESCRIPTIONS,
            "csrf_token": _make_csrf_token(settings),
            "create_error": create_error,
            "prefill": prefill or {},
            "share_info": share_info,
        },
    )


# ── Create client ─────────────────────────────────────────────────────────────


@router.post("/clients")
async def ui_create_client(
    request: Request,
    client_name: str = Form(...),
    role: str = Form(...),
    label: str = Form(""),
    allowed_scopes: list[str] = Form(default=[]),
    model_scopes: str = Form(""),
    csrf_token: str = Form(...),
    db: AsyncSession = Depends(_get_db),
) -> Any:
    """AC-19: Create a new client via the dashboard form."""
    import uuid as _uuid

    session = _validate_session(request)
    if not session:
        return _login_redirect()
    settings = request.app.state.settings
    if not _verify_csrf(settings, csrf_token):
        return _login_redirect()

    all_scopes = [*allowed_scopes, *_parse_model_scopes(model_scopes)]
    invalid = invalid_scopes(all_scopes)
    prefill = {
        "client_name": client_name,
        "role": role,
        "label": label,
        "model_scopes": model_scopes,
    }
    if not allowed_scopes:
        return await _render_dashboard(
            request,
            db,
            create_error="Debes seleccionar al menos un scope.",
            prefill=prefill,
        )
    if invalid:
        return await _render_dashboard(
            request,
            db,
            create_error=f"Scopes no válidos: {', '.join(sorted(invalid))}",
            prefill=prefill,
        )

    with _tracer.start_as_current_span("admin.ui.client.create", kind=SpanKind.INTERNAL) as span:
        plain_secret = f"pmt_live_{secrets.token_hex(24)}"
        ttl = settings.ttl_for_role(role)
        client = OAuthClient(
            client_id=str(_uuid.uuid4()),
            client_name=client_name,
            client_secret_hash=_hash_secret(plain_secret),
            role=ClientRole(role),
            allowed_scopes=" ".join(sorted(all_scopes)),
            token_ttl_seconds=ttl,
            label=label.strip() or None,
        )
        db.add(client)
        await db.commit()
        await db.refresh(client)

        logger.info("admin_ui.client.created", client_id=client.client_id)

        # Store secret in signed flash cookie → redirect to reveal page (AC-19, AC-23)
        flash_ser = _make_serializer(settings.auth_admin_api_key, "flash")
        flash_token = flash_ser.dumps(
            {"secret": plain_secret, "client_id": client.client_id, "action": "created"}
        )
        response: Any = RedirectResponse(url="/admin/ui/secret-revealed", status_code=303)
        response.set_cookie(
            _FLASH_COOKIE, flash_token, max_age=_FLASH_MAX_AGE, httponly=True, samesite="lax"
        )
        span.set_attribute("client_id", client.client_id)
        span.set_attribute("scopes", client.allowed_scopes)
        span.set_attribute("http.status_code", 303)
        return response


# ── Secret revealed ───────────────────────────────────────────────────────────


@router.get("/secret-revealed")
async def get_secret_revealed(request: Request) -> Any:
    """AC-23: Display newly created / rotated secret exactly once.

    Reads and immediately clears the flash cookie. Re-visiting this page
    after the cookie is cleared shows no secret.
    """
    session = _validate_session(request)
    if not session:
        return _login_redirect()

    settings = request.app.state.settings
    with _tracer.start_as_current_span("admin.ui.share.reveal", kind=SpanKind.INTERNAL) as span:
        flash_cookie = request.cookies.get(_FLASH_COOKIE)
        secret_data: dict[str, str] | None = None

        if flash_cookie:
            flash_ser = _make_serializer(settings.auth_admin_api_key, "flash")
            try:
                secret_data = flash_ser.loads(flash_cookie, max_age=_FLASH_MAX_AGE)
            except (BadSignature, SignatureExpired):
                secret_data = None

        # Build a short-lived signed share_intent token embedded in the form response body.
        # This lets the user generate a share link without needing the flash cookie to
        # survive past this page view. Salt differs from "flash" to prevent cross-use.
        share_intent: str | None = None
        if secret_data:
            intent_ser = _make_serializer(settings.auth_admin_api_key, "share-intent")
            share_intent = intent_ser.dumps(secret_data)

        response: Any = _templates.TemplateResponse(
            request,
            "admin_secret.html",
            {
                "secret": secret_data.get("secret") if secret_data else None,
                "client_id": secret_data.get("client_id") if secret_data else None,
                "action": secret_data.get("action", "created") if secret_data else None,
                "share_intent": share_intent,
                "csrf_token": _make_csrf_token(settings),
            },
        )
        # Consume the flash cookie — secret is now shown; further visits show nothing.
        response.delete_cookie(_FLASH_COOKIE)
        span.set_attribute("token_used", secret_data is not None)
        span.set_attribute("http.status_code", 200)
        return response


# ── Edit client (GET form page) ───────────────────────────────────────────────


@router.get("/clients/{client_id}/edit")
async def get_edit_client(
    client_id: str,
    request: Request,
    db: AsyncSession = Depends(_get_db),
) -> Any:
    """AC-20: Render edit form pre-filled with current client values."""
    session = _validate_session(request)
    if not session:
        return _login_redirect()

    result = await db.execute(select(OAuthClient).where(OAuthClient.client_id == client_id))
    client = result.scalar_one_or_none()
    if client is None:
        return RedirectResponse(url="/admin/ui/dashboard", status_code=302)

    settings = request.app.state.settings
    return _templates.TemplateResponse(
        request,
        "admin_edit.html",
        {
            "client": client,
            "valid_scopes": sorted(VALID_SCOPES),
            "scope_descriptions": SCOPE_DESCRIPTIONS,
            "model_scopes": _model_scopes_display(client.scopes),
            "csrf_token": _make_csrf_token(settings),
            "error": request.query_params.get("error"),
        },
    )


# ── Edit client (POST) ────────────────────────────────────────────────────────


@router.post("/clients/{client_id}/edit")
async def post_edit_client(
    client_id: str,
    request: Request,
    client_name: str = Form(...),
    label: str = Form(""),
    allowed_scopes: list[str] = Form(default=[]),
    model_scopes: str = Form(""),
    token_ttl_seconds: int = Form(...),
    csrf_token: str = Form(...),
    db: AsyncSession = Depends(_get_db),
) -> Any:
    """AC-20: Persist edits to client name, label, scopes, and TTL."""
    session = _validate_session(request)
    if not session:
        return _login_redirect()
    settings = request.app.state.settings
    if not _verify_csrf(settings, csrf_token):
        return _login_redirect()

    all_scopes = [*allowed_scopes, *_parse_model_scopes(model_scopes)]
    invalid = invalid_scopes(all_scopes)
    if invalid:
        return RedirectResponse(
            url=f"/admin/ui/clients/{client_id}/edit?error=Invalid+scopes", status_code=303
        )

    with _tracer.start_as_current_span("admin.ui.client.update", kind=SpanKind.INTERNAL) as span:
        result = await db.execute(select(OAuthClient).where(OAuthClient.client_id == client_id))
        client = result.scalar_one_or_none()
        if client is None:
            span.set_attribute("target_client_id", client_id)
            span.set_attribute("http.status_code", 302)
            return RedirectResponse(url="/admin/ui/dashboard", status_code=302)

        client.client_name = client_name
        client.label = label.strip() or None
        if all_scopes:
            client.allowed_scopes = " ".join(sorted(all_scopes))
        client.token_ttl_seconds = token_ttl_seconds
        client.updated_at = datetime.now(timezone.utc)
        await db.commit()

        logger.info("admin_ui.client.updated", client_id=client_id)
        span.set_attribute("target_client_id", client_id)
        span.set_attribute("updated_fields", "client_name,label,allowed_scopes,token_ttl_seconds")
        span.set_attribute("http.status_code", 303)
        return RedirectResponse(url="/admin/ui/dashboard", status_code=303)


# ── Deactivate ────────────────────────────────────────────────────────────────


@router.post("/clients/{client_id}/deactivate")
async def ui_deactivate_client(
    client_id: str,
    request: Request,
    csrf_token: str = Form(...),
    db: AsyncSession = Depends(_get_db),
) -> Any:
    """AC-21: Soft-deactivate a client via the dashboard."""
    session = _validate_session(request)
    if not session:
        return _login_redirect()
    settings = request.app.state.settings
    if not _verify_csrf(settings, csrf_token):
        return _login_redirect()

    with _tracer.start_as_current_span(
        "admin.ui.client.deactivate", kind=SpanKind.INTERNAL
    ) as span:
        span.set_attribute("target_client_id", client_id)
        result = await db.execute(select(OAuthClient).where(OAuthClient.client_id == client_id))
        client = result.scalar_one_or_none()
        if client is not None and client.is_active:
            client.is_active = False
            client.revoked_at = datetime.now(timezone.utc)
            client.updated_at = datetime.now(timezone.utc)
            await db.commit()
            if settings.auth_revocation_redis_url:
                try:
                    rc = aioredis.from_url(settings.auth_revocation_redis_url)
                    await rc.set(f"revoked:client:{client_id}", "1", ex=client.token_ttl_seconds)
                    await rc.aclose()
                except Exception as exc:
                    logger.error("admin_ui.deactivate.redis_error", error=str(exc))

        span.set_attribute("http.status_code", 303)
        return RedirectResponse(url="/admin/ui/dashboard", status_code=303)


# ── Reactivate ────────────────────────────────────────────────────────────────


@router.post("/clients/{client_id}/reactivate")
async def ui_reactivate_client(
    client_id: str,
    request: Request,
    csrf_token: str = Form(...),
    db: AsyncSession = Depends(_get_db),
) -> Any:
    """AC-22: Reactivate a previously deactivated client via the dashboard."""
    session = _validate_session(request)
    if not session:
        return _login_redirect()
    settings = request.app.state.settings
    if not _verify_csrf(settings, csrf_token):
        return _login_redirect()

    with _tracer.start_as_current_span(
        "admin.ui.client.reactivate", kind=SpanKind.INTERNAL
    ) as span:
        span.set_attribute("target_client_id", client_id)
        result = await db.execute(select(OAuthClient).where(OAuthClient.client_id == client_id))
        client = result.scalar_one_or_none()
        if client is not None and not client.is_active:
            client.is_active = True
            client.revoked_at = None
            client.updated_at = datetime.now(timezone.utc)
            await db.commit()
            if settings.auth_revocation_redis_url:
                try:
                    rc = aioredis.from_url(settings.auth_revocation_redis_url)
                    await rc.delete(f"revoked:client:{client_id}")
                    await rc.aclose()
                except Exception as exc:
                    logger.error("admin_ui.reactivate.redis_error", error=str(exc))

        span.set_attribute("http.status_code", 303)
        return RedirectResponse(url="/admin/ui/dashboard", status_code=303)


# ── Rotate secret ─────────────────────────────────────────────────────────────


@router.post("/clients/{client_id}/rotate-secret")
async def ui_rotate_secret(
    client_id: str,
    request: Request,
    csrf_token: str = Form(...),
    db: AsyncSession = Depends(_get_db),
) -> Any:
    """AC-23: Rotate secret via dashboard; display new secret once on reveal page."""
    session = _validate_session(request)
    if not session:
        return _login_redirect()
    settings = request.app.state.settings
    if not _verify_csrf(settings, csrf_token):
        return _login_redirect()

    with _tracer.start_as_current_span(
        "admin.ui.client.rotate_secret", kind=SpanKind.INTERNAL
    ) as span:
        span.set_attribute("target_client_id", client_id)
        result = await db.execute(select(OAuthClient).where(OAuthClient.client_id == client_id))
        client = result.scalar_one_or_none()
        if client is None or not client.is_active:
            span.set_attribute("http.status_code", 303)
            return RedirectResponse(url="/admin/ui/dashboard", status_code=303)

        plain_secret = f"pmt_live_{secrets.token_hex(24)}"
        client.client_secret_hash = _hash_secret(plain_secret)
        client.updated_at = datetime.now(timezone.utc)
        await db.commit()

        logger.info("admin_ui.client.secret_rotated", client_id=client_id)

        flash_ser = _make_serializer(settings.auth_admin_api_key, "flash")
        flash_token = flash_ser.dumps(
            {"secret": plain_secret, "client_id": client_id, "action": "rotated"}
        )
        response: Any = RedirectResponse(url="/admin/ui/secret-revealed", status_code=303)
        response.set_cookie(
            _FLASH_COOKIE, flash_token, max_age=_FLASH_MAX_AGE, httponly=True, samesite="lax"
        )
        span.set_attribute("http.status_code", 303)
        return response


# ── Hard delete ───────────────────────────────────────────────────────────────


@router.post("/clients/{client_id}/delete")
async def ui_delete_client(
    client_id: str,
    request: Request,
    confirm_id: str = Form(...),
    csrf_token: str = Form(...),
    db: AsyncSession = Depends(_get_db),
) -> Any:
    """AC-24: Hard-delete a client; requires confirm_id field matching client_id."""
    session = _validate_session(request)
    if not session:
        return _login_redirect()
    settings = request.app.state.settings
    if not _verify_csrf(settings, csrf_token):
        return _login_redirect()

    # Server-side confirmation: submitted confirm_id must match path param
    if not secrets.compare_digest(confirm_id, client_id):
        return RedirectResponse(url="/admin/ui/dashboard", status_code=303)

    with _tracer.start_as_current_span("admin.ui.client.delete", kind=SpanKind.INTERNAL) as span:
        span.set_attribute("target_client_id", client_id)
        result = await db.execute(select(OAuthClient).where(OAuthClient.client_id == client_id))
        client = result.scalar_one_or_none()
        if client is not None:
            ttl = client.token_ttl_seconds
            await db.delete(client)
            await db.commit()
            if settings.auth_revocation_redis_url:
                try:
                    rc = aioredis.from_url(settings.auth_revocation_redis_url)
                    await rc.set(f"revoked:client:{client_id}", "1", ex=ttl)
                    await rc.aclose()
                except Exception as exc:
                    logger.error("admin_ui.delete.redis_error", error=str(exc))

        logger.info("admin_ui.client.hard_deleted", client_id=client_id)
        span.set_attribute("http.status_code", 303)
        return RedirectResponse(url="/admin/ui/dashboard", status_code=303)


# ── Generate share link ───────────────────────────────────────────────────────


@router.post("/clients/{client_id}/share")
async def ui_generate_share_link(
    client_id: str,
    request: Request,
    csrf_token: str = Form(...),
    share_intent: str = Form(default=""),
    db: AsyncSession = Depends(_get_db),
) -> Any:
    """AC-1..AC-7, AC-26, AC-28: Generate a single-use credential share URL."""
    # AC-6: session required
    session = _validate_session(request)
    if not session:
        return _login_redirect()
    settings = request.app.state.settings
    # AC-5: CSRF required
    if not _verify_csrf(settings, csrf_token):
        return _login_redirect()

    # AC-7: client must exist
    result = await db.execute(select(OAuthClient).where(OAuthClient.client_id == client_id))
    client = result.scalar_one_or_none()
    if client is None:
        return RedirectResponse(url="/admin/ui/dashboard", status_code=303)

    with _tracer.start_as_current_span("admin.ui.share.create", kind=SpanKind.INTERNAL) as span:
        span.set_attribute("client_id", client_id)
        # AC-28: revoke any existing active token for this client before creating a new one
        now = datetime.now(timezone.utc)
        active_result = await db.execute(
            select(CredentialShareToken).where(
                CredentialShareToken.client_id == client_id,
                CredentialShareToken.used_at.is_(None),
                CredentialShareToken.revoked_at.is_(None),
            )
        )
        active_tokens = active_result.scalars().all()
        for old_token in active_tokens:
            old_expires = old_token.expires_at
            if old_expires.tzinfo is None:
                old_expires = old_expires.replace(tzinfo=timezone.utc)
            if old_expires > now:  # still active — revoke it
                old_token.revoked_at = now
                old_token.revoked_by = "admin:superseded"
                old_token.secret_plaintext_enc = None

        # We need the plain secret — it's only available at creation / rotation time.
        # Primary: read from the signed share_intent form field (embedded in secret-revealed page).
        # Fallback: read from flash cookie (direct POST without visiting secret-revealed page).
        plain_secret: str | None = None
        if share_intent:
            intent_ser = _make_serializer(settings.auth_admin_api_key, "share-intent")
            try:
                intent_data = intent_ser.loads(share_intent, max_age=_FLASH_MAX_AGE)
                if intent_data.get("client_id") == client_id:
                    plain_secret = intent_data.get("secret")
            except (BadSignature, SignatureExpired):
                pass

        if plain_secret is None:
            flash_cookie = request.cookies.get(_FLASH_COOKIE)
            if flash_cookie:
                flash_ser = _make_serializer(settings.auth_admin_api_key, "flash")
                try:
                    flash_data = flash_ser.loads(flash_cookie, max_age=_FLASH_MAX_AGE)
                    if flash_data.get("client_id") == client_id:
                        plain_secret = flash_data.get("secret")
                except (BadSignature, SignatureExpired):
                    pass

        if plain_secret is None:
            # No flash available — redirect to dashboard (user must rotate first)
            span.set_attribute("http.status_code", 303)
            return RedirectResponse(
                url="/admin/ui/dashboard?share_error=no_secret",
                status_code=303,
            )

        # AC-1: create share token
        raw_token = secrets.token_urlsafe(32)  # 256-bit URL-safe random token
        ttl_s = settings.share_token_ttl_seconds
        enc = encrypt_secret(settings.share_token_encryption_key, plain_secret)

        share = CredentialShareToken(
            id=str(_uuid_mod.uuid4()),
            token=raw_token,
            client_id=client_id,
            client_name=client.client_name,
            client_id_value=client_id,
            secret_plaintext_enc=enc,
            expires_at=now + timedelta(seconds=ttl_s),
        )
        db.add(share)
        await db.commit()

        # AC-13 (018): share_token_created event
        logger.info(
            "auth.share_token_created",
            token_id=share.id,
            token_prefix=raw_token[:8] + "…",
            client_id=client_id,
            expires_at=share.expires_at.isoformat(),
        )

        # Build the full share URL from the request's base URL
        base_url = str(request.base_url).rstrip("/")
        share_url = f"{base_url}/share/{raw_token}"

        # Determine TTL label for display
        ttl_label = _ttl_display(ttl_s)

        response: Any = _templates.TemplateResponse(
            request,
            "share_link_generated.html",
            {
                "share_url": share_url,
                "client_name": client.client_name,
                "client_id": client_id,
                "ttl_label": ttl_label,
                "expires_at": share.expires_at,
            },
        )
        span.set_attribute("http.status_code", 200)
        return response


# ── Revoke share link ─────────────────────────────────────────────────────────


@router.post("/share/{token_id}/revoke")
async def ui_revoke_share_link(
    token_id: str,
    request: Request,
    csrf_token: str = Form(...),
    db: AsyncSession = Depends(_get_db),
) -> Any:
    """AC-19, AC-20, AC-24: Revoke an active share token before it is consumed."""
    session = _validate_session(request)
    if not session:
        return _login_redirect()
    settings = request.app.state.settings
    if not _verify_csrf(settings, csrf_token):
        return _login_redirect()

    with _tracer.start_as_current_span("admin.ui.share.revoke", kind=SpanKind.INTERNAL) as span:
        span.set_attribute("token_id", token_id)
        result = await db.execute(
            select(CredentialShareToken).where(CredentialShareToken.id == token_id)
        )
        share = result.scalar_one_or_none()
        if share is None:
            span.set_attribute("http.status_code", 303)
            return RedirectResponse(url="/admin/ui/dashboard", status_code=303)

        # AC-21: already used → 409 — redirect with error param
        if share.used_at is not None:
            span.set_attribute("http.status_code", 303)
            return RedirectResponse(
                url="/admin/ui/dashboard?share_error=already_used", status_code=303
            )

        now = datetime.now(timezone.utc)
        share.revoked_at = now
        share.revoked_by = "admin"
        share.secret_plaintext_enc = None
        await db.commit()

        # AC-13 (018): share_token_revoked event
        logger.info(
            "auth.share_token_revoked",
            token_id=share.id,
            token_prefix=share.token[:8] + "…",
            client_id=share.client_id,
        )
        span.set_attribute("http.status_code", 303)
        return RedirectResponse(url="/admin/ui/dashboard", status_code=303)
