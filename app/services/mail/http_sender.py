import json
from urllib import error, request

from app.services.mail.base import ExceptionNotificationMail, MailSender, ResetPasswordMail
from app.services.mail.templates import (
    build_exception_notification_html,
    build_exception_notification_subject,
    build_exception_notification_text,
    build_reset_password_html,
    build_reset_password_subject,
    build_reset_password_text,
)


class HttpMailSender(MailSender):
    def __init__(
        self,
        *,
        endpoint: str,
        api_key: str = "",
        timeout_sec: int = 8,
        mail_from: str = "",
    ) -> None:
        self._endpoint = endpoint
        self._api_key = api_key
        self._timeout_sec = max(1, int(timeout_sec))
        self._mail_from = mail_from

    def _post(self, body: dict[str, object]) -> None:
        data = json.dumps(body).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        req = request.Request(self._endpoint, data=data, headers=headers, method="POST")
        try:
            with request.urlopen(req, timeout=self._timeout_sec) as resp:
                status = getattr(resp, "status", None) or resp.getcode()
                if int(status) // 100 != 2:
                    raise RuntimeError(f"HTTP mail provider returned status {status}")
        except error.HTTPError as exc:
            raise RuntimeError(f"HTTP mail provider error: {exc.code}") from exc
        except error.URLError as exc:
            raise RuntimeError(f"HTTP mail provider unreachable: {exc.reason}") from exc

    def send_reset_password(self, payload: ResetPasswordMail) -> None:
        self._post(
            {
                "type": "reset_password",
                "to": payload.to_email,
                "from": self._mail_from,
                "subject": build_reset_password_subject(),
                "text": build_reset_password_text(payload),
                "html": build_reset_password_html(payload),
            }
        )

    def send_exception_notification(self, payload: ExceptionNotificationMail) -> None:
        self._post(
            {
                "type": payload.event_type,
                "to": payload.to_email,
                "from": self._mail_from,
                "subject": build_exception_notification_subject(payload.event_type),
                "text": build_exception_notification_text(payload),
                "html": build_exception_notification_html(payload),
                "metadata": payload.metadata,
            }
        )
