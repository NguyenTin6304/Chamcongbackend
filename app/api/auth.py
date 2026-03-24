import logging
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import get_db
from app.core.deps import get_current_user
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_refresh_token,
    hash_password,
    hash_token,
    verify_password,
)
from app.models import RefreshToken, User
from app.schemas.auth import (
    ForgotPasswordRequest,
    LoginRequest,
    MessageResponse,
    RefreshTokenRequest,
    RegisterRequest,
    RegisterResponse,
    ResetPasswordRequest,
    TokenResponse,
    UserMeResponse,
)
from app.services.auth.password_reset_service import PasswordResetService
from app.services.auth.recaptcha_service import verify_login_recaptcha
from app.services.mail.base import ResetPasswordMail
from app.services.mail.factory import get_mail_sender

router = APIRouter(prefix="/auth", tags=["auth"])
logger = logging.getLogger(__name__)

GENERIC_FORGOT_PASSWORD_MESSAGE = "Nếu email tồn tại, hệ thống đã gửi hướng dẫn đặt lại mật khẩu."


def _authenticate_user(db: Session, email: str, password: str) -> User:
    user = db.query(User).filter(User.email == email).first()
    if not user or not verify_password(password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sai mật khẩu hoặc email xin vui lòng thử lại",
        )
    return user


def _issue_tokens(db: Session, user: User, remember_me: bool) -> TokenResponse:
    access_token = create_access_token({"sub": str(user.id), "role": user.role})

    refresh_days = (
        settings.REFRESH_TOKEN_EXPIRE_DAYS
        if remember_me
        else settings.REFRESH_TOKEN_EXPIRE_DAYS_NO_REMEMBER
    )
    refresh_token, refresh_expires_at, refresh_jti = create_refresh_token(
        {"sub": str(user.id), "role": user.role},
        expires_days=refresh_days,
    )

    refresh_row = RefreshToken(
        user_id=user.id,
        jti=refresh_jti,
        token_hash=hash_token(refresh_token),
        remember_me=remember_me,
        expires_at=refresh_expires_at,
    )
    db.add(refresh_row)

    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        access_expires_in_minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES,
        refresh_expires_in_days=refresh_days,
    )


def _send_reset_password_mail_background(payload: ResetPasswordMail) -> None:
    try:
        get_mail_sender().send_reset_password(payload)
    except Exception:
        logger.exception("Background reset password email send failed. to=%s", payload.to_email)


def _get_request_ip(request: Request) -> str | None:
    if request.client is None:
        return None
    host = request.client.host
    if not host:
        return None
    return host


@router.post("/register", response_model=RegisterResponse, status_code=status.HTTP_201_CREATED)
def register(payload: RegisterRequest, db: Session = Depends(get_db)):
    try:
        user = User(
            email=payload.email,
            password_hash=hash_password(payload.password),
            role="USER",
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        return RegisterResponse(id=user.id, email=user.email, role=user.role)
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=400, detail="Email đã tồn tại hãy thử với email khác")


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, request: Request, db: Session = Depends(get_db)):
    verify_login_recaptcha(token=payload.recaptcha_token, remote_ip=_get_request_ip(request))
    user = _authenticate_user(db, payload.email, payload.password)
    tokens = _issue_tokens(db, user, remember_me=payload.remember_me)
    db.commit()
    return tokens


@router.post("/forgot-password", response_model=MessageResponse)
def forgot_password(
    payload: ForgotPasswordRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    try:
        service = PasswordResetService(db=db, mail_sender=get_mail_sender())
        mail_payload = service.prepare_password_reset(payload.email)
        db.commit()

        if mail_payload is not None:
            background_tasks.add_task(_send_reset_password_mail_background, mail_payload)
    except Exception:
        db.rollback()
        logger.exception("Forgot password failed internally")

    return MessageResponse(message=GENERIC_FORGOT_PASSWORD_MESSAGE)


@router.post("/reset-password", response_model=MessageResponse)
def reset_password(payload: ResetPasswordRequest, db: Session = Depends(get_db)):
    try:
        service = PasswordResetService(db=db, mail_sender=get_mail_sender())
        service.reset_password(payload.token, payload.new_password)
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "INVALID_RESET_TOKEN",
                "message": str(exc),
            },
        )
    except Exception:
        db.rollback()
        logger.exception("Reset password failed internally")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "code": "INTERNAL_SERVER_ERROR",
                "message": "Không thể đặt lại mật khẩu lúc này",
            },
        )

    return MessageResponse(message="Đặt lại mật khẩu thành công")


@router.post("/login-form", response_model=TokenResponse, deprecated=True)
def login_form(form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    # Legacy compatibility endpoint: form-data (username/password).
    if settings.RECAPTCHA_ENABLED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "RECAPTCHA_REQUIRED",
                "message": "reCAPTCHA đang bật. Hãy dùng endpoint /auth/login",
            },
        )

    user = _authenticate_user(db, form_data.username, form_data.password)
    tokens = _issue_tokens(db, user, remember_me=True)
    db.commit()
    return tokens


@router.post("/login-json", response_model=TokenResponse, deprecated=True)
def login_json(payload: LoginRequest, request: Request, db: Session = Depends(get_db)):
    # Deprecated alias of /auth/login for backward compatibility.
    verify_login_recaptcha(token=payload.recaptcha_token, remote_ip=_get_request_ip(request))
    user = _authenticate_user(db, payload.email, payload.password)
    tokens = _issue_tokens(db, user, remember_me=payload.remember_me)
    db.commit()
    return tokens


@router.post("/refresh", response_model=TokenResponse)
def refresh(payload: RefreshTokenRequest, db: Session = Depends(get_db)):
    try:
        decoded = decode_refresh_token(payload.refresh_token)
        user_id = int(decoded.get("sub"))
        jti = str(decoded.get("jti"))
    except Exception:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token")

    refresh_row = (
        db.query(RefreshToken)
        .filter(
            RefreshToken.user_id == user_id,
            RefreshToken.jti == jti,
            RefreshToken.revoked_at.is_(None),
        )
        .first()
    )
    if not refresh_row:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Refresh token revoked or not found")

    now = datetime.now(timezone.utc)
    expires_at = refresh_row.expires_at
    if expires_at.tzinfo is None:
        now_cmp = now.replace(tzinfo=None)
    else:
        now_cmp = now

    if expires_at <= now_cmp:
        refresh_row.revoked_at = now_cmp
        db.commit()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Refresh token expired")

    if refresh_row.token_hash != hash_token(payload.refresh_token):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token")

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        refresh_row.revoked_at = now_cmp
        db.commit()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")

    tokens = _issue_tokens(db, user, remember_me=refresh_row.remember_me)

    new_refresh_row = (
        db.query(RefreshToken)
        .filter(RefreshToken.token_hash == hash_token(tokens.refresh_token or ""))
        .first()
    )
    refresh_row.revoked_at = now_cmp
    if new_refresh_row:
        refresh_row.replaced_by_jti = new_refresh_row.jti

    db.commit()
    return tokens


@router.post("/logout")
def logout(payload: RefreshTokenRequest, db: Session = Depends(get_db)):
    try:
        decoded = decode_refresh_token(payload.refresh_token)
        user_id = int(decoded.get("sub"))
        jti = str(decoded.get("jti"))
    except Exception:
        # Idempotent logout response.
        return {"ok": True}

    refresh_row = (
        db.query(RefreshToken)
        .filter(
            RefreshToken.user_id == user_id,
            RefreshToken.jti == jti,
            RefreshToken.revoked_at.is_(None),
        )
        .first()
    )
    if refresh_row:
        refresh_row.revoked_at = datetime.now(timezone.utc)
        db.commit()

    return {"ok": True}


@router.post("/logout-all")
def logout_all(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    now = datetime.now(timezone.utc)
    active_tokens = (
        db.query(RefreshToken)
        .filter(
            RefreshToken.user_id == current_user.id,
            RefreshToken.revoked_at.is_(None),
        )
        .all()
    )

    for row in active_tokens:
        row.revoked_at = now

    db.commit()
    return {"ok": True, "revoked_count": len(active_tokens)}


@router.get("/me", response_model=UserMeResponse)
def me(current_user: User = Depends(get_current_user)):
    return UserMeResponse(id=current_user.id, email=current_user.email, role=current_user.role)
