import logging

import httpx
from fastapi import HTTPException, status

from app.core.config import settings

logger = logging.getLogger(__name__)
RECAPTCHA_VERIFY_URL = "https://www.google.com/recaptcha/api/siteverify"


def _allowed_hostnames() -> set[str]:
    raw = settings.RECAPTCHA_ALLOWED_HOSTNAMES.strip()
    if not raw:
        return set()
    return {item.strip().lower() for item in raw.split(",") if item.strip()}


def verify_login_recaptcha(token: str | None, remote_ip: str | None = None) -> None:
    """Verify reCAPTCHA token.

    Supports both:
    - reCAPTCHA v2 checkbox (no score/action fields)
    - reCAPTCHA v3 (with optional score/action checks)
    """
    if not settings.RECAPTCHA_ENABLED:
        return

    if not settings.RECAPTCHA_SECRET_KEY:
        logger.error("RECAPTCHA enabled but RECAPTCHA_SECRET_KEY is empty")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "code": "RECAPTCHA_NOT_CONFIGURED",
                "message": "reCAPTCHA chưa được cấu hình ở máy chủ",
            },
        )

    if token is None or not token.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "RECAPTCHA_REQUIRED",
                "message": "Thiếu mã xác minh reCAPTCHA",
            },
        )

    payload: dict[str, str] = {
        "secret": settings.RECAPTCHA_SECRET_KEY,
        "response": token.strip(),
    }
    if remote_ip:
        payload["remoteip"] = remote_ip

    try:
        with httpx.Client(timeout=settings.RECAPTCHA_VERIFY_TIMEOUT_SEC) as client:
            response = client.post(RECAPTCHA_VERIFY_URL, data=payload)
            response.raise_for_status()
            verify_data = response.json()
    except httpx.HTTPError:
        logger.exception("reCAPTCHA verification request failed")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "RECAPTCHA_UNAVAILABLE",
                "message": "Không thể xác minh reCAPTCHA lúc này",
            },
        )
    except ValueError:
        logger.exception("Invalid reCAPTCHA verification response payload")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "RECAPTCHA_INVALID_RESPONSE",
                "message": "Phản hồi reCAPTCHA không hợp lệ",
            },
        )

    success = bool(verify_data.get("success", False))
    hostname = str(verify_data.get("hostname") or "").lower()

    if not success:
        logger.warning(
            "reCAPTCHA failed. errors=%s",
            verify_data.get("error-codes") or [],
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "RECAPTCHA_FAILED",
                "message": "Xác minh reCAPTCHA thất bại",
            },
        )

    allowed_hosts = _allowed_hostnames()
    if allowed_hosts and hostname not in allowed_hosts:
        logger.warning(
            "reCAPTCHA hostname mismatch. allowed=%s got=%s",
            sorted(allowed_hosts),
            hostname,
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "RECAPTCHA_HOSTNAME_MISMATCH",
                "message": "Mã reCAPTCHA không hợp lệ cho tên miền hiện tại",
            },
        )

    # Optional v3-only checks (ignored for v2 checkbox response).
    action = str(verify_data.get("action") or "").strip()
    expected_action = settings.RECAPTCHA_EXPECTED_ACTION.strip()
    if expected_action and action and action != expected_action:
        logger.warning("reCAPTCHA action mismatch. expected=%s got=%s", expected_action, action)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "RECAPTCHA_ACTION_MISMATCH",
                "message": "Mã reCAPTCHA không hợp lệ cho thao tác đăng nhập",
            },
        )

    score_raw = verify_data.get("score", None)
    if score_raw is not None:
        try:
            score = float(score_raw)
        except (TypeError, ValueError):
            score = 0.0
        if score < settings.RECAPTCHA_MIN_SCORE:
            logger.warning(
                "reCAPTCHA score too low. score=%.3f min=%.3f",
                score,
                settings.RECAPTCHA_MIN_SCORE,
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "code": "RECAPTCHA_LOW_SCORE",
                    "message": "Điểm reCAPTCHA quá thấp, vui lòng thử lại",
                },
            )
