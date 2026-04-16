import json
import logging
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.core.db import SessionLocal
from app.models import AttendanceException, AttendanceExceptionNotification, Employee, User
from app.services.mail.base import ExceptionNotificationMail
from app.services.mail.factory import get_mail_sender
from app.services.mail.templates import (
    build_exception_notification_html,
    build_exception_notification_subject,
    build_exception_notification_text,
)

logger = logging.getLogger(__name__)


def _format_value(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _json_default(value: object) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def build_exception_notification_mail(
    *,
    event_type: str,
    to_email: str,
    exception: AttendanceException,
    employee: Employee,
    recipient_role: str,
    admin_user: User | None = None,
    extra_metadata: dict[str, Any] | None = None,
) -> ExceptionNotificationMail | None:
    recipient = to_email.strip()
    if not recipient:
        return None

    metadata: dict[str, Any] = {
        "exception_id": exception.id,
        "employee_id": employee.id,
        "employee_email": recipient if recipient_role == "EMPLOYEE" else None,
        "employee_name": employee.full_name,
        "recipient_role": recipient_role,
        "status": exception.status,
        "exception_type": exception.exception_type,
        "detected_at": _format_value(exception.detected_at),
        "expires_at": _format_value(exception.expires_at),
        "work_date": _format_value(exception.work_date),
        "source_checkin_log_id": exception.source_checkin_log_id,
        "admin_id": admin_user.id if admin_user else None,
        "admin_email": admin_user.email if admin_user else None,
        "admin_note": exception.admin_note,
    }
    if extra_metadata:
        metadata.update(extra_metadata)

    subject = build_exception_notification_subject(event_type)
    payload = ExceptionNotificationMail(
        to_email=recipient,
        event_type=event_type,
        subject=subject,
        text="",
        html="",
        metadata=metadata,
    )
    payload.text = build_exception_notification_text(payload)
    payload.html = build_exception_notification_html(payload)
    return payload


def create_exception_notification_record(
    db: Session,
    *,
    payload: ExceptionNotificationMail,
    exception_id: int,
    recipient_user_id: int | None,
    recipient_role: str,
    dedupe_key: str,
) -> AttendanceExceptionNotification | None:
    existing = (
        db.query(AttendanceExceptionNotification)
        .filter(AttendanceExceptionNotification.dedupe_key == dedupe_key)
        .first()
    )
    if existing is not None:
        return None

    notification = AttendanceExceptionNotification(
        exception_id=exception_id,
        event_type=payload.event_type,
        recipient_user_id=recipient_user_id,
        recipient_email=payload.to_email,
        recipient_role=recipient_role,
        dedupe_key=dedupe_key,
        status="QUEUED",
        metadata_json=json.dumps(payload.metadata, default=_json_default, ensure_ascii=False),
    )
    db.add(notification)
    db.flush()
    return notification


def send_exception_notification(payload: ExceptionNotificationMail) -> None:
    get_mail_sender().send_exception_notification(payload)


def _mark_notification_result(notification_id: int, *, error_message: str | None = None) -> None:
    db = SessionLocal()
    try:
        notification = (
            db.query(AttendanceExceptionNotification)
            .filter(AttendanceExceptionNotification.id == notification_id)
            .first()
        )
        if notification is None:
            return
        if error_message:
            notification.status = "FAILED"
            notification.failed_at = datetime.now(timezone.utc)
            notification.error_message = error_message[:1000]
        else:
            notification.status = "SENT"
            notification.sent_at = datetime.now(timezone.utc)
            notification.failed_at = None
            notification.error_message = None
        db.commit()
    finally:
        db.close()


# Keys must match the event_type strings used in app/api/reports.py exactly.
_PUSH_TITLES: dict[str, str] = {
    "exception_detected_employee": "Phát hiện ngoại lệ chấm công",
    "exception_detected_admin": "Phát hiện ngoại lệ chấm công",
    "exception_submitted_admin": "Nhân viên đã gửi giải trình",
    "exception_approved_employee": "Ngoại lệ đã được duyệt",
    "exception_rejected_employee": "Ngoại lệ bị từ chối",
    "exception_expired_employee": "Ngoại lệ đã hết hạn",
}

_PUSH_BODIES: dict[str, str] = {
    "exception_detected_employee": "Hệ thống phát hiện ngoại lệ chấm công. Vui lòng giải trình trong thời hạn quy định.",
    "exception_detected_admin": "Có ngoại lệ mới cần xử lý. Vui lòng kiểm tra.",
    "exception_submitted_admin": "Nhân viên vừa gửi giải trình. Vui lòng xem xét và phê duyệt.",
    "exception_approved_employee": "Ngoại lệ chấm công của bạn đã được phê duyệt.",
    "exception_rejected_employee": "Ngoại lệ chấm công của bạn bị từ chối. Vui lòng xem chi tiết.",
    "exception_expired_employee": "Ngoại lệ chấm công đã hết hạn mà chưa được giải trình.",
}


def send_exception_notification_background(
    payload: ExceptionNotificationMail,
    notification_id: int | None = None,
    fcm_token: str | None = None,
) -> None:
    # ── Email ─────────────────────────────────────────────────────────────────
    email_failed = False
    try:
        send_exception_notification(payload)
        if notification_id is not None:
            _mark_notification_result(notification_id)
    except Exception as exc:
        email_failed = True
        if notification_id is not None:
            _mark_notification_result(notification_id, error_message=str(exc))
        logger.exception(
            "Exception notification email send failed. event=%s to=%s",
            payload.event_type,
            payload.to_email,
        )

    # ── FCM push — always attempted, independent of email result ─────────────
    if fcm_token and fcm_token.strip():
        try:
            from app.services.fcm_service import send_push_notification

            title = _PUSH_TITLES.get(payload.event_type, "Thông báo chấm công")
            body = _PUSH_BODIES.get(payload.event_type, "")
            send_push_notification(fcm_token.strip(), title, body)
            if email_failed:
                logger.info(
                    "FCM push sent despite email failure. event=%s", payload.event_type
                )
        except Exception:
            logger.exception(
                "FCM push background send failed. event=%s", payload.event_type
            )
