from app.services.mail.base import MailSender, ResetPasswordMail


class NoopMailSender(MailSender):
    def send_reset_password(self, payload: ResetPasswordMail) -> None:
        # Intentionally no-op for local/dev when MAIL_ENABLED=false.
        print(
            "[MAIL_DISABLED] reset password email skipped "
            f"to={payload.to_email} expires={payload.expires_minutes}m url={payload.reset_url}"
        )
