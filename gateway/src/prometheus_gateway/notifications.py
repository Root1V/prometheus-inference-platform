"""Budget-alert email delivery — RM-60.

Plain stdlib smtplib: no email-sending infrastructure exists anywhere else in
this codebase, so this avoids a new dependency/vendor lock-in for a single
notification type. Unset SMTP config = silently skip (the in-app banner still
fires) — same "optional, off unless configured" convention as pricing_file.

Implements: docs/roadmap.md — RM-60 (#7).
"""

from __future__ import annotations

import smtplib
from email.mime.text import MIMEText
from typing import TYPE_CHECKING

from .telemetry import get_logger

if TYPE_CHECKING:
    from .config import Settings

logger = get_logger(__name__)


def send_budget_alert_email(
    settings: "Settings",
    client_id: str,
    threshold_percent: int,
    spend_usd: float,
    cap_usd: float,
) -> None:
    """Blocking — callers on the request path must run this via
    asyncio.to_thread() so it never blocks the event loop.
    """
    if (
        not settings.smtp_host
        or not settings.smtp_from_address
        or not settings.billing_alert_email_to
    ):
        logger.debug(
            "billing.alert_email_skipped", reason="smtp_not_configured", client_id=client_id
        )
        return

    msg = MIMEText(
        f"Client: {client_id}\n"
        f"Threshold crossed: {threshold_percent}%\n"
        f"Current spend: ${spend_usd:.2f}\n"
        f"Monthly cap: ${cap_usd:.2f}\n"
    )
    msg["Subject"] = (
        f"[Prometheus] Client {client_id} reached {threshold_percent}% of its monthly spend cap"
    )
    msg["From"] = settings.smtp_from_address
    msg["To"] = settings.billing_alert_email_to

    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as server:
            if settings.smtp_use_tls:
                server.starttls()
            if settings.smtp_username and settings.smtp_password:
                server.login(settings.smtp_username, settings.smtp_password)
            server.sendmail(
                settings.smtp_from_address,
                settings.billing_alert_email_to.split(","),
                msg.as_string(),
            )
    except Exception as exc:
        logger.warning("billing.alert_email_failed", client_id=client_id, error=str(exc))
