from app.core.config import settings
from app.services.mail.base import MailSender
from app.services.mail.noop import NoopMailSender
from app.services.mail.zoho_smtp import ZohoSmtpMailSender


def get_mail_sender() -> MailSender:
    if not settings.MAIL_ENABLED:
        return NoopMailSender()

    provider = settings.MAIL_PROVIDER.lower().strip()
    if provider in {"zoho", "smtp"}:
        return ZohoSmtpMailSender(
            host=settings.SMTP_HOST,
            port=settings.SMTP_PORT,
            username=settings.SMTP_USER,
            password=settings.SMTP_PASS,
            mail_from=settings.MAIL_FROM or settings.SMTP_USER,
            use_tls=settings.SMTP_TLS,
        )

    return NoopMailSender()
