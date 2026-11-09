"""Staff notifications (email / SMS). Always sent off the request path.

Keep message bodies short and factual: they leave the clinic's systems, so
they carry what the front desk needs to act (name, number, request) and
point to the dashboard for the rest. No transcripts.
"""

from __future__ import annotations

import asyncio
import logging
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage

import httpx

from core.config import ClinicConfig
from core.settings import Settings

log = logging.getLogger("backend.notify")


@dataclass(frozen=True)
class Notice:
    subject: str
    body: str
    urgent: bool = False


class Notifier:
    def __init__(self, settings: Settings, clinic: ClinicConfig) -> None:
        self.settings = settings
        self.clinic = clinic
        self._tasks: set[asyncio.Task] = set()
        self.sent: list[Notice] = []  # what the "log" channel delivered; read by tests

    def send_later(self, notice: Notice) -> None:
        """Fire and forget; failures are logged, never raised into the caller."""
        task = asyncio.create_task(self._send_safely(notice))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def drain(self) -> None:
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)

    async def _send_safely(self, notice: Notice) -> None:
        try:
            await self.send(notice)
        except Exception:
            log.exception("staff notification failed (channel=%s): %s", self.settings.notify_channel, notice.subject)

    async def send(self, notice: Notice) -> None:
        subject = f"{'[URGENT] ' if notice.urgent else ''}{notice.subject}"
        channel = self.settings.notify_channel
        if channel == "log":
            self.sent.append(notice)
            log.info("NOTIFY %s\n%s", subject, notice.body)
        elif channel == "sendgrid":
            await self._sendgrid(subject, notice.body)
        elif channel == "smtp":
            await asyncio.to_thread(self._smtp, subject, notice.body)
        elif channel == "twilio_sms":
            await self._twilio_sms(f"{subject}: {notice.body}"[:600])

    async def _sendgrid(self, subject: str, body: str) -> None:
        to = [{"email": e} for e in self.clinic.notifications.staff_emails]
        if not to:
            return
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post(
                "https://api.sendgrid.com/v3/mail/send",
                headers={"Authorization": f"Bearer {self.settings.sendgrid_api_key.get_secret_value()}"},
                json={
                    "personalizations": [{"to": to}],
                    "from": {"email": self.settings.notify_from_email, "name": self.clinic.name},
                    "subject": subject,
                    "content": [{"type": "text/plain", "value": body}],
                },
            )
            r.raise_for_status()

    def _smtp(self, subject: str, body: str) -> None:
        if not self.clinic.notifications.staff_emails:
            return
        s = self.settings
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = s.notify_from_email
        msg["To"] = ", ".join(self.clinic.notifications.staff_emails)
        msg.set_content(body)
        with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=10) as smtp:
            smtp.starttls()
            smtp.login(s.smtp_username, s.smtp_password.get_secret_value())
            smtp.send_message(msg)

    async def _twilio_sms(self, text: str) -> None:
        s = self.settings
        url = f"https://api.twilio.com/2010-04-01/Accounts/{s.twilio_account_sid}/Messages.json"
        async with httpx.AsyncClient(timeout=10, auth=(s.twilio_account_sid, s.twilio_auth_token.get_secret_value())) as c:
            for number in self.clinic.notifications.staff_sms:
                r = await c.post(url, data={"From": s.twilio_from_number, "To": number, "Body": text})
                r.raise_for_status()
