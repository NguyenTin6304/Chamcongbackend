import json
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.models import AttendanceException, Employee, User
from app.services.attendance_exception_audit import record_attendance_exception_audit
from app.services.attendance_exception_notifications import (
    build_exception_notification_mail,
    create_exception_notification_record,
    send_exception_notification,
)
from app.services.attendance_exception_workflow import (
    EXPIRED,
    PENDING_EMPLOYEE,
    ensure_allowed_exception_transition,
)
from app.services.attendance_time import normalize_utc


def _employee_user_for_exception(db: Session, exception: AttendanceException) -> tuple[Employee, User] | None:
    employee = db.query(Employee).filter(Employee.id == exception.employee_id).first()
    if employee is None or employee.user_id is None:
        return None
    user = db.query(User).filter(User.id == employee.user_id).first()
    if user is None or not user.email:
        return None
    return employee, user


def _send_employee_notification_once(
    db: Session,
    *,
    event_type: str,
    exception: AttendanceException,
    employee: Employee,
    user: User,
    dedupe_key: str,
    extra_metadata: dict[str, object] | None = None,
) -> bool:
    payload = build_exception_notification_mail(
        event_type=event_type,
        to_email=user.email,
        exception=exception,
        employee=employee,
        recipient_role="EMPLOYEE",
        extra_metadata=extra_metadata,
    )
    if payload is None:
        return False

    notification = create_exception_notification_record(
        db,
        payload=payload,
        exception_id=exception.id,
        recipient_user_id=user.id,
        recipient_role="EMPLOYEE",
        dedupe_key=dedupe_key,
    )
    if notification is None:
        return False

    try:
        send_exception_notification(payload)
    except Exception as exc:
        notification.status = "FAILED"
        notification.failed_at = datetime.now(timezone.utc)
        notification.error_message = str(exc)[:1000]
    else:
        notification.status = "SENT"
        notification.sent_at = datetime.now(timezone.utc)
    return True


def expire_overdue_exceptions(db: Session, *, now: datetime | None = None) -> int:
    now_utc = normalize_utc(now) if now is not None else datetime.now(timezone.utc)
    candidates = (
        db.query(AttendanceException)
        .filter(AttendanceException.status == PENDING_EMPLOYEE)
        .filter(AttendanceException.expires_at.isnot(None))
        .filter(AttendanceException.expires_at <= now_utc)
        .all()
    )

    expired_count = 0
    for exception in candidates:
        previous_status = exception.status
        exception.status = ensure_allowed_exception_transition(exception.status, EXPIRED)
        record_attendance_exception_audit(
            db,
            exception_id=exception.id,
            event_type="system_expired",
            previous_status=previous_status,
            next_status=exception.status,
            actor_type="SYSTEM",
            actor_email="SYSTEM",
            metadata={
                "expired_at": now_utc.isoformat(),
                "expires_at": normalize_utc(exception.expires_at).isoformat() if exception.expires_at else None,
                "source": "auto_expire_job",
            },
        )
        employee_user = _employee_user_for_exception(db, exception)
        if employee_user is not None:
            employee, user = employee_user
            _send_employee_notification_once(
                db,
                event_type="exception_expired_employee",
                exception=exception,
                employee=employee,
                user=user,
                dedupe_key=f"exception:{exception.id}:exception_expired_employee",
                extra_metadata={"expired_at": now_utc.isoformat(), "source": "auto_expire_job"},
            )
        expired_count += 1

    db.commit()
    return expired_count


def send_expire_reminders(db: Session, *, now: datetime | None = None, reminder_window_hours: int = 24) -> int:
    now_utc = normalize_utc(now) if now is not None else datetime.now(timezone.utc)
    window_end = now_utc + timedelta(hours=reminder_window_hours)
    candidates = (
        db.query(AttendanceException)
        .filter(AttendanceException.status == PENDING_EMPLOYEE)
        .filter(AttendanceException.expires_at.isnot(None))
        .filter(AttendanceException.expires_at > now_utc)
        .filter(AttendanceException.expires_at <= window_end)
        .all()
    )

    sent_count = 0
    for exception in candidates:
        employee_user = _employee_user_for_exception(db, exception)
        if employee_user is None:
            continue
        employee, user = employee_user
        sent = _send_employee_notification_once(
            db,
            event_type="exception_expire_reminder_employee",
            exception=exception,
            employee=employee,
            user=user,
            dedupe_key=f"exception:{exception.id}:exception_expire_reminder_employee",
            extra_metadata={
                "reminder_window_hours": reminder_window_hours,
                "source": "expire_reminder_job",
            },
        )
        if sent:
            sent_count += 1

    db.commit()
    return sent_count
