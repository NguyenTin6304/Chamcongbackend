from dataclasses import dataclass


@dataclass(slots=True)
class ResetPasswordMail:
    to_email: str
    reset_url: str
    reset_token: str
    expires_minutes: int


class MailSender:
    def send_reset_password(self, payload: ResetPasswordMail) -> None:
        raise NotImplementedError
