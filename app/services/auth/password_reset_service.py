from datetime import datetime, timedelta, timezone
from secrets import token_urlsafe
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import hash_password, hash_token
from app.models import PasswordResetToken, RefreshToken, User
from app.services.mail.base import MailSender, ResetPasswordMail


class PasswordResetService:
    def __init__(self, db: Session, mail_sender: MailSender) -> None:
        self._db = db
        self._mail_sender = mail_sender

    def prepare_password_reset(self, email: str) -> ResetPasswordMail | None:
        user = self._db.query(User).filter(func.lower(User.email) == email.lower().strip()).first()
        if not user:
            return None

        raw_token = self._issue_reset_token(user_id=user.id)
        reset_url = self._build_reset_url(raw_token)

        return ResetPasswordMail(
            to_email=user.email,
            reset_url=reset_url,
            reset_token=raw_token,
            expires_minutes=settings.PASSWORD_RESET_TOKEN_EXPIRE_MINUTES,
        )

    def send_password_reset_mail(self, payload: ResetPasswordMail) -> None:
        self._mail_sender.send_reset_password(payload)

    def request_password_reset(self, email: str) -> None:
        payload = self.prepare_password_reset(email)
        if payload is None:
            return
        self.send_password_reset_mail(payload)

    def reset_password(self, token: str, new_password: str) -> None:
        token = token.strip()
        reset_row = self._db.query(PasswordResetToken).filter(PasswordResetToken.token_hash == hash_token(token)).first()
        if not reset_row or reset_row.used_at is not None or self._is_expired(reset_row.expires_at):
            raise ValueError("Token đặt lại mật khẩu không hợp lệ hoặc đã hết hạn")

        user = self._db.query(User).filter(User.id == reset_row.user_id).first()
        if not user:
            raise ValueError("Token đặt lại mật khẩu không hợp lệ hoặc đã hết hạn")

        now = datetime.now(timezone.utc)
        user.password_hash = hash_password(new_password)

        reset_row.used_at = now

        (
            self._db.query(PasswordResetToken)
            .filter(
                PasswordResetToken.user_id == user.id,
                PasswordResetToken.used_at.is_(None),
                PasswordResetToken.id != reset_row.id,
            )
            .update({PasswordResetToken.used_at: now}, synchronize_session=False)
        )

        (
            self._db.query(RefreshToken)
            .filter(
                RefreshToken.user_id == user.id,
                RefreshToken.revoked_at.is_(None),
            )
            .update({RefreshToken.revoked_at: now}, synchronize_session=False)
        )

    def _issue_reset_token(self, user_id: int) -> str:
        now = datetime.now(timezone.utc)
        raw_token = token_urlsafe(48)
        token_hash = hash_token(raw_token)
        expires_at = now + timedelta(minutes=settings.PASSWORD_RESET_TOKEN_EXPIRE_MINUTES)

        (
            self._db.query(PasswordResetToken)
            .filter(
                PasswordResetToken.user_id == user_id,
                PasswordResetToken.used_at.is_(None),
            )
            .update({PasswordResetToken.used_at: now}, synchronize_session=False)
        )

        self._db.add(
            PasswordResetToken(
                user_id=user_id,
                token_hash=token_hash,
                expires_at=expires_at,
            )
        )
        return raw_token

    @staticmethod
    def _is_expired(expires_at: datetime) -> bool:
        now = datetime.now(timezone.utc)
        if expires_at.tzinfo is None:
            return expires_at <= now.replace(tzinfo=None)
        return expires_at <= now

    @staticmethod
    def _build_reset_url(raw_token: str) -> str:
        base_url = settings.RESET_PASSWORD_URL_BASE.strip() or "http://localhost:62601/#/reset-password"
        parsed = urlparse(base_url)

        # Flutter Web hash route: http://host/#/reset-password?token=...
        if parsed.fragment:
            fragment_path, _, fragment_query = parsed.fragment.partition("?")
            fragment_path = fragment_path.strip() or "/reset-password"
            if fragment_path == "/":
                fragment_path = "/reset-password"

            fragment_items = dict(parse_qsl(fragment_query, keep_blank_values=True))
            fragment_items["token"] = raw_token
            new_fragment = f"{fragment_path}?{urlencode(fragment_items)}"
            return urlunparse(parsed._replace(fragment=new_fragment))

        # Non-hash route fallback: http://host/reset-password?token=...
        path = parsed.path.strip() or "/reset-password"
        if path == "/":
            path = "/reset-password"
        query_items = dict(parse_qsl(parsed.query, keep_blank_values=True))
        query_items["token"] = raw_token
        new_query = urlencode(query_items)
        # Keep token in both query and hash fragment so link still works
        # when frontend uses hash routing in deployed SPA environments.
        new_fragment = f"{path}?{urlencode({'token': raw_token})}"
        return urlunparse(parsed._replace(path=path, query=new_query, fragment=new_fragment))


def cleanup_password_reset_tokens(
    db: Session,
    *,
    now_utc: datetime | None = None,
    used_retention_days: int = 1,
) -> int:
    """
    Delete stale password reset tokens to avoid unbounded table growth.

    - Expired tokens are deleted immediately.
    - Used tokens are kept for a short retention window for troubleshooting/audit.
    """
    now = now_utc or datetime.now(timezone.utc)
    used_retention_days = max(0, used_retention_days)
    used_cutoff = now - timedelta(days=used_retention_days)

    dialect = db.bind.dialect.name if db.bind is not None else ""
    if dialect == "sqlite":
        now_cmp = now.replace(tzinfo=None)
        used_cutoff_cmp = used_cutoff.replace(tzinfo=None)
    else:
        now_cmp = now
        used_cutoff_cmp = used_cutoff

    deleted_count = (
        db.query(PasswordResetToken)
        .filter(
            or_(
                PasswordResetToken.expires_at <= now_cmp,
                and_(
                    PasswordResetToken.used_at.is_not(None),
                    PasswordResetToken.used_at <= used_cutoff_cmp,
                ),
            )
        )
        .delete(synchronize_session=False)
    )
    return int(deleted_count or 0)
