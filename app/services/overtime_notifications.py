"""FCM notification helpers for the overtime workflow (Phase 2.5)."""
from __future__ import annotations

import threading

from sqlalchemy.orm import Session

from app.models import Employee, OvertimeRecord, User


def _format_minutes(value: int | None) -> str:
    if not value:
        return "0 phút"
    h, m = divmod(int(value), 60)
    if h == 0:
        return f"{m} phút"
    if m == 0:
        return f"{h} giờ"
    return f"{h} giờ {m} phút"


def _format_date(work_date) -> str:
    return work_date.strftime("%d/%m/%Y") if work_date else ""


def _push_async(token: str, title: str, body: str, data: dict | None = None) -> None:
    try:
        from app.services.fcm_service import send_push_notification
        send_push_notification(token, title, body, data=data or {"route": "/overtime"})
    except Exception as fcm_exc:
        # Notifications must never block the main workflow, but daemon threads
        # swallow tracebacks — log explicitly so failures are visible.
        import logging
        logging.getLogger(__name__).warning(
            "Overtime FCM push failed (title=%s): %s", title, fcm_exc,
        )


def fire_decision_fcm(db: Session, record: OvertimeRecord, event: str) -> None:
    """Push the employee about an admin decision on their OT record.

    event: "approved" | "rejected" | "edited"
    """
    if record is None:
        return

    employee = db.query(Employee).filter(Employee.id == record.employee_id).first()
    if employee is None or employee.user_id is None:
        return

    user = db.query(User).filter(User.id == employee.user_id).first()
    if user is None or not user.fcm_token or not user.fcm_token.strip():
        return
    token = user.fcm_token.strip()

    work_date_str = _format_date(record.work_date)
    if event == "approved":
        title = "Tăng ca đã được duyệt ✅"
        body = f"OT ngày {work_date_str}: được duyệt {_format_minutes(record.approved_minutes)}."
    elif event == "rejected":
        title = "Tăng ca không được duyệt ❌"
        suffix = f" — {record.admin_note}" if record.admin_note else ""
        body = f"OT ngày {work_date_str} không được duyệt{suffix}."
    elif event == "edited":
        title = "Tăng ca đã được điều chỉnh"
        body = f"OT n   gày {work_date_str} được cập nhật: {_format_minutes(record.approved_minutes)}."
    else:
        return

    threading.Thread(
        target=_push_async,
        args=(token, title, body, {"route": "/home/overtime"}),
        daemon=True,
    ).start()
