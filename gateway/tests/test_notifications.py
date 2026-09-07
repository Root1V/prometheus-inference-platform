"""Tests for RM-60 — budget-alert email delivery (prometheus_gateway/notifications.py)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from prometheus_gateway.config import Settings
from prometheus_gateway.notifications import send_budget_alert_email


def _settings(**overrides) -> Settings:
    base = dict(
        jwt_issuer="https://auth.test",
        jwt_audience="prometheus-gateway",
        jwt_public_key_file="/dev/null",
    )
    base.update(overrides)
    return Settings(**base)


def test_skips_silently_when_smtp_not_configured():
    settings = _settings()
    with patch("smtplib.SMTP") as mock_smtp:
        send_budget_alert_email(settings, "client-a", 80, 8.0, 10.0)
    mock_smtp.assert_not_called()


def test_sends_email_when_configured():
    settings = _settings(
        smtp_host="smtp.test",
        smtp_port=587,
        smtp_from_address="alerts@prometheus.test",
        billing_alert_email_to="ops@prometheus.test",
        smtp_username="user",
        smtp_password="pass",
        smtp_use_tls=True,
    )
    mock_server = MagicMock()
    with patch("smtplib.SMTP") as mock_smtp:
        mock_smtp.return_value.__enter__.return_value = mock_server
        send_budget_alert_email(settings, "client-a", 80, 8.0, 10.0)

    mock_smtp.assert_called_once_with("smtp.test", 587, timeout=10)
    mock_server.starttls.assert_called_once()
    mock_server.login.assert_called_once_with("user", "pass")
    assert mock_server.sendmail.call_count == 1
    call_args = mock_server.sendmail.call_args
    assert call_args[0][0] == "alerts@prometheus.test"
    assert call_args[0][1] == ["ops@prometheus.test"]
    assert "client-a" in call_args[0][2]
    assert "80" in call_args[0][2]


def test_skips_tls_and_login_when_not_configured():
    settings = _settings(
        smtp_host="smtp.test",
        smtp_from_address="alerts@prometheus.test",
        billing_alert_email_to="ops@prometheus.test",
        smtp_use_tls=False,
    )
    mock_server = MagicMock()
    with patch("smtplib.SMTP") as mock_smtp:
        mock_smtp.return_value.__enter__.return_value = mock_server
        send_budget_alert_email(settings, "client-a", 100, 10.0, 10.0)

    mock_server.starttls.assert_not_called()
    mock_server.login.assert_not_called()
    mock_server.sendmail.assert_called_once()


def test_send_failure_is_swallowed_not_raised():
    settings = _settings(
        smtp_host="smtp.test",
        smtp_from_address="alerts@prometheus.test",
        billing_alert_email_to="ops@prometheus.test",
    )
    with patch("smtplib.SMTP", side_effect=ConnectionError("smtp down")):
        send_budget_alert_email(settings, "client-a", 80, 8.0, 10.0)  # must not raise


def test_multiple_recipients_split_on_comma():
    settings = _settings(
        smtp_host="smtp.test",
        smtp_from_address="alerts@prometheus.test",
        billing_alert_email_to="ops@prometheus.test,billing@prometheus.test",
    )
    mock_server = MagicMock()
    with patch("smtplib.SMTP") as mock_smtp:
        mock_smtp.return_value.__enter__.return_value = mock_server
        send_budget_alert_email(settings, "client-a", 50, 5.0, 10.0)

    call_args = mock_server.sendmail.call_args
    assert call_args[0][1] == ["ops@prometheus.test", "billing@prometheus.test"]
