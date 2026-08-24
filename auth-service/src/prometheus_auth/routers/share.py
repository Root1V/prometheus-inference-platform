# See memory/specs/016-credential-share-link.md — Public one-time credential delivery
# Implements: AC-8 through AC-14, AC-17, AC-18, AC-20
# Implements: memory/specs/018-observability-telemetry.md — AC-2, AC-13
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import CredentialShareToken, get_session_factory
from ..share_crypto import decrypt_secret
from ..telemetry import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/share", tags=["share"])

_TEMPLATE_DIR = Path(__file__).parent.parent / "templates"
_templates = Jinja2Templates(directory=str(_TEMPLATE_DIR))

# ── No-cache headers required on all responses from this router ───────────────
_SECURITY_HEADERS = {
    "Cache-Control": "no-store, private",
    "X-Robots-Tag": "noindex",
    "Referrer-Policy": "no-referrer",
}


async def _get_db() -> Any:
    async with get_session_factory()() as session:
        yield session


def _apply_headers(response: Any) -> Any:
    for k, v in _SECURITY_HEADERS.items():
        response.headers[k] = v
    return response


# ── One-time credential view ──────────────────────────────────────────────────


@router.get("/{token}")
async def view_share(
    token: str,
    request: Request,
    db: AsyncSession = Depends(_get_db),
) -> Any:
    """AC-8..14, AC-17, AC-18, AC-20 — serve credentials exactly once."""
    settings = request.app.state.settings

    result = await db.execute(
        select(CredentialShareToken).where(CredentialShareToken.token == token)
    )
    share = result.scalar_one_or_none()

    # AC-12: non-existent token → 404
    if share is None:
        resp = _templates.TemplateResponse(
            request, "share_gone.html", {"reason": "not_found"}, status_code=404
        )
        return _apply_headers(resp)

    now = datetime.now(timezone.utc)

    # AC-10: already used
    if share.used_at is not None or share.secret_plaintext_enc is None:
        resp = _templates.TemplateResponse(
            request, "share_gone.html", {"reason": "used"}, status_code=410
        )
        return _apply_headers(resp)

    # AC-20: revoked
    if share.revoked_at is not None:
        resp = _templates.TemplateResponse(
            request, "share_gone.html", {"reason": "revoked"}, status_code=410
        )
        return _apply_headers(resp)

    # AC-11: expired
    expires = share.expires_at
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if expires <= now:
        resp = _templates.TemplateResponse(
            request, "share_gone.html", {"reason": "expired"}, status_code=410
        )
        return _apply_headers(resp)

    # AC-17/18: decrypt — if decryption fails, treat as gone (should not happen unless tampered)
    try:
        plaintext_secret = decrypt_secret(
            settings.share_token_encryption_key, share.secret_plaintext_enc
        )
    except ValueError:
        resp = _templates.TemplateResponse(
            request, "share_gone.html", {"reason": "used"}, status_code=410
        )
        return _apply_headers(resp)

    # AC-9: stamp used_at, used_by_ip, clear plaintext — do this BEFORE rendering
    client_ip = request.client.host if request.client else "unknown"
    raw_ua = request.headers.get("user-agent", "")
    share.used_at = now
    share.used_by_ip = client_ip
    share.used_by_ua = raw_ua[:256]
    share.secret_plaintext_enc = None
    await db.commit()

    # AC-13 (018): share_token_used — no raw token string in log
    logger.info(
        "auth.share_token_used",
        token_id=share.id,
        token_prefix=share.token[:8] + "…",
        client_id=share.client_id,
        used_by_ip=client_ip,
    )

    # AC-8, AC-13, AC-14: render credential view with security headers
    resp = _templates.TemplateResponse(
        request,
        "share_view.html",
        {
            "client_name": share.client_name,
            "client_id": share.client_id_value,
            "client_secret": plaintext_secret,
        },
        status_code=200,
    )
    return _apply_headers(resp)
