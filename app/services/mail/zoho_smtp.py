import smtplib
import socket
import ssl
import time
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from app.services.mail.base import ExceptionNotificationMail, MailSender, ResetPasswordMail
from app.services.mail.templates import (
    build_exception_notification_html,
    build_exception_notification_subject,
    build_exception_notification_text,
    build_reset_password_html,
    build_reset_password_subject,
    build_reset_password_text,
)


class ZohoSmtpMailSender(MailSender):
    def __init__(
        self,
        *,
        host: str,
        port: int,
        username: str,
        password: str,
        mail_from: str,
        use_tls: bool,
        timeout_sec: int = 8,
        retry_attempts: int = 2,
        retry_delay_sec: float = 1.0,
    ) -> None:
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._mail_from = mail_from
        self._use_tls = use_tls
        self._timeout_sec = max(1, int(timeout_sec))
        self._retry_attempts = max(1, int(retry_attempts))
        self._retry_delay_sec = max(0.0, float(retry_delay_sec))

    def _send(self, *, to_email: str, subject: str, text: str, html: str) -> None:
        msg = MIMEMultipart("alternative")
        msg["From"] = self._mail_from
        msg["To"] = to_email
        msg["Subject"] = subject
        msg.attach(MIMEText(text, "plain", "utf-8"))
        msg.attach(MIMEText(html, "html", "utf-8"))

        context = ssl.create_default_context()
        last_error: Exception | None = None

        for attempt in range(1, self._retry_attempts + 1):
            try:
                with smtplib.SMTP(self._host, self._port, timeout=self._timeout_sec) as server:
                    server.ehlo()
                    if self._use_tls:
                        server.starttls(context=context)
                        server.ehlo()
                    server.login(self._username, self._password)
                    server.sendmail(self._mail_from, to_email, msg.as_string())
                return
            except (TimeoutError, socket.timeout, OSError, smtplib.SMTPException) as exc:
                last_error = exc
                if attempt < self._retry_attempts and self._retry_delay_sec > 0:
                    time.sleep(self._retry_delay_sec)

        if last_error is not None:
            raise last_error

    def send_reset_password(self, payload: ResetPasswordMail) -> None:
        self._send(
            to_email=payload.to_email,
            subject=build_reset_password_subject(),
            text=build_reset_password_text(payload),
            html=build_reset_password_html(payload),
        )

    def send_exception_notification(self, payload: ExceptionNotificationMail) -> None:
        self._send(
            to_email=payload.to_email,
            subject=build_exception_notification_subject(payload.event_type),
            text=build_exception_notification_text(payload),
            html=build_exception_notification_html(payload),
        )
