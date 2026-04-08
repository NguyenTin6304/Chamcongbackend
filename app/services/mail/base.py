from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class ResetPasswordMail:
    to_email: str
    reset_url: str
    reset_token: str
    expires_minutes: int


@dataclass(slots=True)
class ExceptionNotificationMail:
    to_email: str
    event_type: str
    subject: str
    text: str
    html: str
    metadata: dict[str, Any]


class MailSender:
    def send_reset_password(self, payload: ResetPasswordMail) -> None:
        raise NotImplementedError

    def send_exception_notification(self, payload: ExceptionNotificationMail) -> None:
        raise NotImplementedError
