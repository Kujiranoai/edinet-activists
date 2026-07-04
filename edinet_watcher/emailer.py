from __future__ import annotations

import smtplib
from email.message import EmailMessage
from pathlib import Path

from .config import Settings


class Emailer:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def configured(self) -> bool:
        return all(
            [
                self.settings.smtp_host,
                self.settings.email_from,
                self.settings.email_to,
            ]
        )

    def send_draft(self, *, subject: str, body: str, draft_path: Path, report_path: Path) -> None:
        if not self.configured():
            raise RuntimeError("SMTP_HOST, EMAIL_FROM, and EMAIL_TO are required for email delivery")

        message = EmailMessage()
        message["From"] = self.settings.email_from
        message["To"] = self.settings.email_to
        message["Subject"] = subject
        message.set_content(body)

        message.add_attachment(
            draft_path.read_text(encoding="utf-8"),
            subtype="markdown",
            filename=draft_path.name,
        )
        message.add_attachment(
            report_path.read_text(encoding="utf-8"),
            subtype="json",
            filename=report_path.name,
        )

        with smtplib.SMTP(self.settings.smtp_host, self.settings.smtp_port) as smtp:
            smtp.starttls()
            if self.settings.smtp_user and self.settings.smtp_password:
                smtp.login(self.settings.smtp_user, self.settings.smtp_password)
            smtp.send_message(message)
