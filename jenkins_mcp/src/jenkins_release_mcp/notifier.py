from __future__ import annotations

import smtplib
from email.message import EmailMessage

from .config import SmtpSettings
from .models import NotificationResult


class EmailNotifier:
    def __init__(self, settings: SmtpSettings):
        self.settings = settings

    def send(self, subject: str, body: str) -> NotificationResult:
        missing = self.settings.missing_fields()
        if missing:
            return NotificationResult(
                sent=False,
                recipients=self.settings.recipients,
                subject=subject,
                error=f"SMTP is not configured. Missing: {', '.join(missing)}",
            )

        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = self.settings.from_address
        message["To"] = ", ".join(self.settings.recipients)
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
                recipients=self.settings.recipients,
                subject=subject,
                error=f"Failed to send email notification: {exc}",
            )

        return NotificationResult(
            sent=True,
            recipients=self.settings.recipients,
            subject=subject,
        )
