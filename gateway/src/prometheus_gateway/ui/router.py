"""Web Chat UI — browser session authentication & reverse proxy.

Implements: memory/specs/013-web-chat-ui-proxy.md
Implements: memory/specs/014-login-page-ux-redesign.md
See: .github/instructions/gateway.instructions.md
See: .github/instructions/auth.instructions.md
"""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from pathlib import Path
from typing import Annotated

import httpx
from fastapi import APIRouter, Form, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from jose import JWTError, jwt
from jose.exceptions import ExpiredSignatureError

from ..auth.jwks import fetch_jwks_keys
from ..config import Settings
from ..models.registry import ModelEntry, ModelRegistry
from ..telemetry import get_logger

# ── Template + static file configuration ─────────────────────────────────
# Implements: memory/specs/014-login-page-ux-redesign.md — AC-1, AC-2
_UI_DIR = Path(__file__).parent
_templates = Jinja2Templates(directory=str(_UI_DIR / "templates"))

logger = get_logger(__name__)

# Scope required for web chat UI access — memory/specs/013-web-chat-ui-proxy.md — AC-3, AC-6
_UI_SCOPE = "ui:chat"

# Headers stripped before forwarding to llama-server — AC-12
_STRIP_REQUEST_HEADERS = frozenset(
    {
        "cookie",
        "authorization",
        "x-forwarded-for",
        "host",
    }
)

# httpx / HTTP/1.1 hop-by-hop headers that must not be forwarded on
_STRIP_RESPONSE_HEADERS = frozenset(
    {
        "transfer-encoding",
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailers",
        "upgrade",
    }
)


# ── Per-IP login rate limiter (in-memory) ────────────────────────────────
# Implements: memory/specs/013-web-chat-ui-proxy.md — AC-11
# Module-level state; reset between tests via _reset_login_limiter_for_testing().

_login_attempts: dict[str, list[float]] = defaultdict(list)
_login_lock = asyncio.Lock()


async def _check_login_rate(client_ip: str, rpm: int) -> bool:
    """Return True if request is within the rate window, False if it should be rejected."""
    now = time.monotonic()
    async with _login_lock:
        cutoff = now - 60.0
        _login_attempts[client_ip] = [t for t in _login_attempts[client_ip] if t > cutoff]
        if len(_login_attempts[client_ip]) >= rpm:
            return False
        _login_attempts[client_ip].append(now)
        return True


def _reset_login_limiter_for_testing() -> None:
    """Clear rate-limiter state — test helper only."""
    _login_attempts.clear()


def _get_client_ip(request: Request) -> str:
    """Extract the direct client IP."""
    return request.client.host if request.client else "unknown"


# ── Session JWT validation ────────────────────────────────────────────────
# Implements: memory/specs/013-web-chat-ui-proxy.md — AC-7, AC-8
# Uses the same RS256 key material as JWTAuthMiddleware (memory/specs/002).


async def _validate_session(token: str, settings: Settings) -> dict[str, object] | None:
    """Validate the session cookie JWT.

    Returns the decoded payload if the token is valid and carries the ui:chat scope.
    Returns None on any failure (expired, invalid, wrong scope, key error).
    Never raises — callers redirect to login on None.
    """
    try:
        if settings.jwt_public_key_file:
            with open(settings.jwt_public_key_file) as fh:
                public_keys: list[str] = [fh.read().strip()]
        elif settings.jwt_jwks_url:
            public_keys = await fetch_jwks_keys(
                settings.jwt_jwks_url,
                tls_verify=settings.auth_service_tls_verify,
            )
        else:
            logger.warning("ui.validate_session.no_key_source")
            return None

        payload: dict[str, object] | None = None
        for key in public_keys:
            try:
                payload = jwt.decode(
                    token,
                    key,
                    algorithms=["RS256"],
                    audience=settings.jwt_audience,
                    issuer=settings.jwt_issuer,
                    options={"leeway": settings.jwt_clock_skew_seconds},
                )
                break
            except ExpiredSignatureError:
                logger.info("ui.validate_session.token_expired")
                return None  # expired → redirect to login (AC-8)
            except JWTError:
                continue  # try next key

        if payload is None:
            logger.warning("ui.validate_session.invalid_signature")
            return None

        # AC-6: scope check
        scope = str(payload.get("scope", ""))
        if _UI_SCOPE not in scope.split():
            logger.warning(
                "ui.validate_session.missing_scope",
                required=_UI_SCOPE,
                actual=scope,
                sub=payload.get("sub"),
            )
            return None

        return payload

    except Exception:
        logger.debug("ui.session.validation_error", exc_info=True)
        return None


# ── Open-redirect guard ───────────────────────────────────────────────────
# Implements: memory/specs/013-web-chat-ui-proxy.md — AC-10


def _safe_next(raw: str) -> str:
    """Return raw if it's a safe /ui/* relative path, else /ui/."""
    if isinstance(raw, str) and (raw.startswith("/ui/") or raw == "/ui"):
        return raw
    return "/ui/"


# ── Login page renderer ───────────────────────────────────────────────────
# Implements: memory/specs/014-login-page-ux-redesign.md — AC-1, AC-11
# Uses Jinja2Templates — all values are auto-escaped; no f-string HTML.


def _login_response(
    request: Request,
    models: list[ModelEntry],
    next_path: str,
    error: str | None = None,
) -> Response:
    """Render login.html via Jinja2Templates.

    All template variables are auto-escaped by Jinja2.
    No | safe filter is used anywhere in the template — AC-11.
    """
    return _templates.TemplateResponse(
        request,
        "login.html",
        {
            "models": models,
            "next_path": next_path,
            "error": error,
            "no_models_warning": len(models) == 0,
            "disabled": len(models) == 0,
        },
    )


# ── Router factory ────────────────────────────────────────────────────────


def create_ui_router(settings: Settings, registry: ModelRegistry) -> APIRouter:
    """Return the /ui APIRouter.  Called only when settings.ui_enabled is True.

    Implements: memory/specs/013-web-chat-ui-proxy.md
    Implements: memory/specs/014-login-page-ux-redesign.md

    Note: static files are mounted at /ui/static directly on the FastAPI app
    in main.py to ensure they are resolved before the catch-all proxy route.
    """
    router = APIRouter()

    def _discoverable() -> list[ModelEntry]:
        """Models eligible for the login combobox: discovery=True only.
        backend_url presence is not required — the manager controls visibility
        via the discovery flag, not the backend state.
        """
        return [m for m in registry.list_models() if m.discovery]

    # ── GET /ui/login ─────────────────────────────────────────────────────
    # Implements: memory/specs/013-web-chat-ui-proxy.md — AC-3
    # Implements: memory/specs/014-login-page-ux-redesign.md — AC-1

    @router.get("/login")
    async def get_login(request: Request, next: str = "/ui/") -> Response:  # noqa: A002
        next = _safe_next(next)
        return _login_response(request, _discoverable(), next)

    # ── POST /ui/login ────────────────────────────────────────────────────
    # Implements: memory/specs/013-web-chat-ui-proxy.md — AC-4, AC-5, AC-6, AC-10, AC-11, AC-13, AC-17

    @router.post("/login")
    async def post_login(
        request: Request,
        client_id: Annotated[str, Form()],
        client_secret: Annotated[str, Form()],
        model_id: Annotated[str, Form()],
        next: Annotated[str, Form()] = "/ui/",  # noqa: A002
    ) -> Response:
        # AC-11: per-IP rate limiting on login endpoint
        ip = _get_client_ip(request)
        if not await _check_login_rate(ip, settings.ui_login_rate_limit_rpm):
            return Response(
                status_code=429,
                headers={"Retry-After": "60"},
                content="Too many login attempts. Please wait before trying again.",
                media_type="text/plain",
            )

        next = _safe_next(next)  # AC-10: sanitise before any redirect  # noqa: A001
        models = _discoverable()
        model_ids = {m.id for m in models}

        # AC-17: reject model not in discoverable set
        if model_id not in model_ids:
            known = registry.get(model_id)
            if known and not known.discovery:
                err = f"Model '{model_id}' is not available for UI access."
            else:
                err = "Unknown or unavailable model. Please select one from the list."
            return _login_response(request, models, next, err)

        # Exchange credentials with auth-service (client_credentials grant)
        try:
            async with httpx.AsyncClient(
                timeout=10.0, verify=settings.auth_service_tls_verify
            ) as client:
                token_resp = await client.post(
                    settings.auth_service_token_url,  # type: ignore[arg-type]
                    data={
                        "grant_type": "client_credentials",
                        "client_id": client_id,
                        "client_secret": client_secret,
                        "scope": _UI_SCOPE,
                    },
                )
        except Exception:
            # AC-13: no internal URL or exception detail exposed to the browser
            logger.exception("ui.login.auth_service_unreachable")
            return _login_response(
                request,
                models,
                next,
                "Service temporarily unavailable. Please try again later.",
            )

        if token_resp.status_code != 200:
            # AC-5: invalid credentials
            logger.warning(
                "ui.login.invalid_credentials",
                client_id=client_id,
                auth_status=token_resp.status_code,
            )
            return _login_response(
                request,
                models,
                next,
                "Invalid credentials. Please check your Client ID and Secret.",
            )

        try:
            access_token: str = token_resp.json()["access_token"]
        except Exception:
            logger.exception("ui.login.bad_token_response")
            return _login_response(
                request,
                models,
                next,
                "Service temporarily unavailable. Please try again later.",
            )

        # AC-6: verify token carries ui:chat scope before setting cookie
        payload = await _validate_session(access_token, settings)
        if payload is None:
            return _login_response(
                request,
                models,
                next,
                f"Your client does not have UI access. "
                f"Ensure it is granted the '{_UI_SCOPE}' scope.",
            )

        # AC-4: redirect to /ui/<model_id>/ with HttpOnly Secure SameSite=Lax cookie
        redirect = RedirectResponse(url=f"/ui/{model_id}/", status_code=302)
        max_age: int | None = settings.ui_session_cookie_max_age or None
        redirect.set_cookie(
            key=settings.ui_session_cookie_name,
            value=access_token,
            httponly=True,
            secure=True,  # AC-14: always set — requires HTTPS (AC-16 warns if no TLS)
            samesite="lax",
            max_age=max_age,
            path="/ui/",
        )
        return redirect

    # ── POST /ui/logout ───────────────────────────────────────────────────
    # Implements: memory/specs/013-web-chat-ui-proxy.md — AC-9

    @router.post("/logout")
    async def logout() -> RedirectResponse:
        resp = RedirectResponse(url="/ui/login", status_code=302)
        resp.delete_cookie(key=settings.ui_session_cookie_name, path="/ui/")
        return resp

    # ── GET|POST|... /ui/{model_id}/{path} ────────────────────────────────
    # Implements: memory/specs/013-web-chat-ui-proxy.md — AC-2, AC-7, AC-8, AC-12, AC-18
    # Note: this catch-all is defined last so /login and /logout take priority.

    @router.api_route(
        "/{model_id}/{path:path}",
        methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "PATCH", "HEAD"],
    )
    async def proxy(
        model_id: str,
        path: str,
        request: Request,
    ) -> Response:
        token = request.cookies.get(settings.ui_session_cookie_name)
        encoded_next = f"/ui/{model_id}/{path}"

        # AC-2, AC-8: missing or invalid cookie → redirect to login
        if not token:
            return RedirectResponse(
                url=f"/ui/login?next={encoded_next}",
                status_code=302,
            )

        payload = await _validate_session(token, settings)
        if payload is None:
            return RedirectResponse(
                url=f"/ui/login?next={encoded_next}",
                status_code=302,
            )

        # AC-18: model must exist with discovery=True and an active backend
        entry = registry.get(model_id)
        if entry is None or not entry.discovery or not entry.backend_url:
            return JSONResponse(
                status_code=404,
                content={"detail": "Model not found or not available for UI access."},
            )

        # AC-12: strip sensitive headers
        forward_headers = {
            k: v for k, v in request.headers.items() if k.lower() not in _STRIP_REQUEST_HEADERS
        }

        backend_base = entry.backend_url.rstrip("/")
        backend_url = f"{backend_base}/{path}"
        if request.url.query:
            backend_url = f"{backend_url}?{request.url.query}"

        body = await request.body()

        # Use stream=True so SSE responses are forwarded without buffering
        http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(60.0, connect=5.0),
            follow_redirects=False,
        )
        try:
            backend_req = http_client.build_request(
                method=request.method,
                url=backend_url,
                headers=forward_headers,
                content=body,
            )
            backend_resp = await http_client.send(backend_req, stream=True)
        except httpx.HTTPError as exc:
            await http_client.aclose()
            logger.warning(
                "ui.proxy.backend_error",
                extra={"model_id": model_id, "error": str(exc)},
            )
            return JSONResponse(status_code=502, content={"detail": "Backend unreachable."})

        resp_headers = {
            k: v
            for k, v in backend_resp.headers.items()
            if k.lower() not in _STRIP_RESPONSE_HEADERS
        }
        content_type = backend_resp.headers.get("content-type", "")

        # SSE streaming — keep connection open, forward chunks immediately
        if "text/event-stream" in content_type:
            from collections.abc import AsyncGenerator

            async def _sse_stream() -> AsyncGenerator[bytes, None]:
                try:
                    async for chunk in backend_resp.aiter_bytes():
                        yield chunk
                finally:
                    await backend_resp.aclose()
                    await http_client.aclose()

            return StreamingResponse(
                _sse_stream(),
                status_code=backend_resp.status_code,
                headers=resp_headers,
                media_type=content_type,
            )

        # Non-streaming: buffer and return
        try:
            content = await backend_resp.aread()
        finally:
            await backend_resp.aclose()
            await http_client.aclose()

        return Response(
            content=content,
            status_code=backend_resp.status_code,
            headers=resp_headers,
            media_type=content_type or None,
        )

    return router
