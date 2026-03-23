import json
import socket
import time
from urllib import error, request

from app.services.mail.base import MailSender, ResetPasswordMail
from app.services.mail.templates import (
    build_reset_password_html,
    build_reset_password_subject,
    build_reset_password_text,
)


class ResendMailSender(MailSender):
    def __init__(
        self,
        *,
        api_key: str,
        mail_from: str,
        endpoint: str = "https://api.resend.com/emails",
        timeout_sec: int = 8,
        retry_attempts: int = 2,
        retry_delay_sec: float = 1.0,
    ) -> None:
        self._api_key = api_key.strip()
        self._mail_from = mail_from.strip()
        self._endpoint = endpoint.strip() or "https://api.resend.com/emails"
        self._timeout_sec = max(1, int(timeout_sec))
        self._retry_attempts = max(1, int(retry_attempts))
        self._retry_delay_sec = max(0.0, float(retry_delay_sec))

    def send_reset_password(self, payload: ResetPasswordMail) -> None:
        if not self._api_key:
            raise RuntimeError("RESEND_API_KEY is empty")
        if not self._mail_from:
            raise RuntimeError("MAIL_FROM is empty")

        body = {
            "from": self._mail_from,
            "to": [payload.to_email],
            "subject": build_reset_password_subject(),
            "text": build_reset_password_text(payload),
            "html": build_reset_password_html(payload),
        }
        data = json.dumps(body).encode("utf-8")

        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": f"Bearer {self._api_key}",
            "User-Agent": "ChamCongApp-Backend/1.0",
        }

        req = request.Request(self._endpoint, data=data, headers=headers, method="POST")

        last_error: Exception | None = None
        last_http_body: str = ""

        for attempt in range(1, self._retry_attempts + 1):
            try:
                with request.urlopen(req, timeout=self._timeout_sec) as resp:
                    status = getattr(resp, "status", None) or resp.getcode()
                    if int(status) // 100 != 2:
                        raise RuntimeError(f"Resend API returned status {status}")
                return
            except error.HTTPError as exc:
                last_error = exc
                try:
                    raw = exc.read()
                    last_http_body = raw.decode("utf-8", errors="ignore")[:1000]
                except Exception:
                    last_http_body = ""
                if attempt < self._retry_attempts and self._retry_delay_sec > 0:
                    time.sleep(self._retry_delay_sec)
            except (TimeoutError, socket.timeout, OSError, error.URLError) as exc:
                last_error = exc
                if attempt < self._retry_attempts and self._retry_delay_sec > 0:
                    time.sleep(self._retry_delay_sec)

        if last_error is not None:
            if isinstance(last_error, error.HTTPError):
                detail = f"Resend API error: {last_error.code}"
                if last_http_body:
                    detail = f"{detail} - {last_http_body}"
                raise RuntimeError(detail) from last_error
            raise RuntimeError(f"Resend API unreachable: {last_error}") from last_error
