from __future__ import annotations

import smtplib
from email.message import EmailMessage

from .config import SmtpSettings
from .models import NotificationResult


class EmailNotifier:
    def __init__(self, settings: SmtpSettings):
        self.settings = settings

    def send(self, subject: str, body: str, recipients: list[str] | None = None) -> NotificationResult:
        actual_recipients = recipients if recipients is not None else self.settings.recipients
        missing = self._missing_fields(actual_recipients)
        if missing:
            return NotificationResult(
                sent=False,
                recipients=actual_recipients,
                subject=subject,
                error=f"SMTP is not configured. Missing: {', '.join(missing)}",
            )

        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = self.settings.from_address
        message["To"] = ", ".join(actual_recipients)
        message.set_content(body)

        try:
            if self.settings.use_ssl:
                with smtplib.SMTP_SSL(self.settings.host, self.settings.port) as smtp:
                    smtp.login(self.settings.username, self.settings.password)
                    smtp.send_message(message)
            else:
                with smtplib.SMTP(self.settings.host, self.settings.port) as smtp:
                    smtp.starttls()
                    smtp.login(self.settings.username, self.settings.password)
                    smtp.send_message(message)
        except Exception as exc:  # SMTP libraries expose several exception classes.
            return NotificationResult(
                sent=False,
                recipients=actual_recipients,
                subject=subject,
                error=f"Failed to send email notification: {exc}",
            )

        return NotificationResult(
            sent=True,
            recipients=actual_recipients,
            subject=subject,
        )

    def _missing_fields(self, recipients: list[str]) -> list[str]:
        missing: list[str] = []
        if not self.settings.host:
            missing.append("SMTP_HOST")
        if not self.settings.username:
            missing.append("SMTP_USERNAME")
        if not self.settings.password:
            missing.append("SMTP_PASSWORD")
        if not self.settings.from_address:
            missing.append("SMTP_FROM")
        if not recipients:
            missing.append("SMTP_TO")
        return missing
