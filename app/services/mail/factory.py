import logging

from app.core.config import settings
from app.services.mail.base import ExceptionNotificationMail, MailSender, ResetPasswordMail
from app.services.mail.http_sender import HttpMailSender
from app.services.mail.noop import NoopMailSender
from app.services.mail.resend_api import ResendMailSender
from app.services.mail.zoho_smtp import ZohoSmtpMailSender

logger = logging.getLogger(__name__)


class FallbackMailSender(MailSender):
    def __init__(self, primary: MailSender, fallback: MailSender) -> None:
        self._primary = primary
        self._fallback = fallback

    def send_reset_password(self, payload: ResetPasswordMail) -> None:
        try:
            self._primary.send_reset_password(payload)
        except Exception as primary_error:
            logger.exception(
                "Primary mail sender failed. Switching to fallback sender. to=%s",
                payload.to_email,
            )
            try:
                self._fallback.send_reset_password(payload)
            except Exception:
                logger.exception("Fallback mail sender also failed. to=%s", payload.to_email)
                raise primary_error

    def send_exception_notification(self, payload: ExceptionNotificationMail) -> None:
        try:
            self._primary.send_exception_notification(payload)
        except Exception as primary_error:
            logger.exception(
                "Primary mail sender failed. Switching to fallback sender. event=%s to=%s",
                payload.event_type,
                payload.to_email,
            )
            try:
                self._fallback.send_exception_notification(payload)
            except Exception:
                logger.exception(
                    "Fallback mail sender also failed. event=%s to=%s",
                    payload.event_type,
                    payload.to_email,
                )
                raise primary_error


def _build_http_sender() -> MailSender | None:
    endpoint = settings.MAIL_HTTP_ENDPOINT.strip()
    if not endpoint:
        return None

    return HttpMailSender(
        endpoint=endpoint,
        api_key=settings.MAIL_HTTP_API_KEY,
        timeout_sec=settings.MAIL_HTTP_TIMEOUT_SEC,
        mail_from=settings.MAIL_FROM or settings.SMTP_USER,
    )


def _build_resend_sender() -> MailSender:
    return ResendMailSender(
        api_key=settings.RESEND_API_KEY,
        endpoint=settings.RESEND_ENDPOINT,
        mail_from=settings.MAIL_FROM or settings.SMTP_USER,
        timeout_sec=settings.RESEND_TIMEOUT_SEC,
        retry_attempts=settings.RESEND_RETRY_ATTEMPTS,
        retry_delay_sec=settings.RESEND_RETRY_DELAY_SEC,
    )


def _build_primary_sender() -> MailSender:
    provider = settings.MAIL_PROVIDER.lower().strip()

    if provider in {"zoho", "smtp"}:
        return ZohoSmtpMailSender(
            host=settings.SMTP_HOST,
            port=settings.SMTP_PORT,
            username=settings.SMTP_USER,
            password=settings.SMTP_PASS,
            mail_from=settings.MAIL_FROM or settings.SMTP_USER,
            use_tls=settings.SMTP_TLS,
            timeout_sec=settings.SMTP_TIMEOUT_SEC,
            retry_attempts=settings.SMTP_RETRY_ATTEMPTS,
            retry_delay_sec=settings.SMTP_RETRY_DELAY_SEC,
        )

    if provider == "resend":
        return _build_resend_sender()

    if provider == "http":
        sender = _build_http_sender()
        if sender is not None:
            return sender

    return NoopMailSender()


def get_mail_sender() -> MailSender:
    if not settings.MAIL_ENABLED:
        return NoopMailSender()

    primary = _build_primary_sender()

    fallback_provider = settings.MAIL_FALLBACK_PROVIDER.lower().strip()
    if fallback_provider == "http" and not isinstance(primary, HttpMailSender):
        fallback = _build_http_sender()
        if fallback is not None:
            return FallbackMailSender(primary=primary, fallback=fallback)

    return primary
