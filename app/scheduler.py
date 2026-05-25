"""Background scheduler for periodic jobs.

Uses the same threading.Thread + threading.Event pattern as the
password-reset cleanup in main.py — no APScheduler dependency needed.

Jobs:
  - send_checkout_reminders: every 5 minutes, find checked-in-but-not-checked-out
    employees within the reminder window and fire FCM push notifications.
  - cleanup_old_face_images: daily at 02:00 VN, delete face images older than
    FACE_RETENTION_DAYS (default 30 days).
"""

import logging
import shutil
import threading
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from app.core.config import settings

logger = logging.getLogger(__name__)

VN_TZ = timezone(timedelta(hours=7))

# Reminder fires when (shift_end - now) is within this window (minutes).
REMINDER_WINDOW_MINUTES = 20
POLL_INTERVAL_SECONDS = 5 * 60  # run every 5 minutes

# In-memory dedup: keys are "employee_id:YYYY-MM-DD".
# Old dates are naturally stale and ignored by the window check, so no
# explicit reset is needed — the set stays small (one entry per employee per day).
_sent_reminders: set[str] = set()

_stop_event = threading.Event()
_reminder_thread: threading.Thread | None = None


def _now_vn() -> datetime:
    return datetime.now(timezone.utc).astimezone(VN_TZ)


def send_checkout_reminders() -> None:
    """Core job: query unchecked-out employees, send FCM if within reminder window."""
    if not settings.FCM_ENABLED:
        return

    from app.core.db import SessionLocal
    from app.models import AttendanceLog, Employee, User
    from app.services.fcm_service import send_push_notification

    now_vn = _now_vn()
    today: date = now_vn.date()

    try:
        with SessionLocal() as db:
            # IDs that already have a checkout today — fetched first, small set.
            checked_out_ids: set[int] = {
                row[0]
                for row in db.query(AttendanceLog.employee_id)
                .filter(
                    AttendanceLog.work_date == today,
                    AttendanceLog.type == "OUT",
                )
                .all()
            }

            # All IN logs for today, joined with employee + user for fcm_token.
            rows = (
                db.query(AttendanceLog, Employee, User)
                .join(Employee, Employee.id == AttendanceLog.employee_id)
                .outerjoin(User, User.id == Employee.user_id)
                .filter(
                    AttendanceLog.type == "IN",
                    AttendanceLog.work_date == today,
                    Employee.active.is_(True),
                    Employee.deleted_at.is_(None),
                )
                .all()
            )
    except Exception:
        logger.exception("checkout_reminder: DB query failed")
        return

    sent_count = 0
    for in_log, emp, user in rows:
        if emp.id in checked_out_ids:
            continue

        dedup_key = f"{emp.id}:{today.isoformat()}"
        if dedup_key in _sent_reminders:
            continue

        snapshot_end = in_log.snapshot_end_time
        if snapshot_end is None:
            continue

        # Build shift_end in VN tz.
        # Night shift (end < start) → end falls on the next calendar day.
        snapshot_start = in_log.snapshot_start_time
        shift_end_vn = datetime.combine(today, snapshot_end, tzinfo=VN_TZ)
        if snapshot_start and snapshot_end < snapshot_start:
            shift_end_vn = datetime.combine(
                today + timedelta(days=1), snapshot_end, tzinfo=VN_TZ
            )

        minutes_until_end = (shift_end_vn - now_vn).total_seconds() / 60
        if not (0 <= minutes_until_end <= REMINDER_WINDOW_MINUTES):
            continue

        # Mark as handled regardless of whether FCM succeeds, so we don't
        # spam the employee on the next poll.
        _sent_reminders.add(dedup_key)

        if not user or not user.fcm_token:
            continue

        def _clear_token(token: str) -> None:
            if user.fcm_token == token:
                user.fcm_token = None
                db.commit()

        ok = send_push_notification(
            user.fcm_token,
            "Nhắc checkout",
            "Còn 15 phút hết ca, đừng quên checkout!",
            data={"route": "/home"},
            on_unregistered=_clear_token,
        )
        if ok:
            sent_count += 1
            logger.info(
                "checkout_reminder: sent to employee_id=%s work_date=%s",
                emp.id,
                today,
            )

    if sent_count:
        logger.info("checkout_reminder: %s reminder(s) sent for %s", sent_count, today)


def cleanup_old_face_images() -> None:
    """Delete face image directories older than FACE_RETENTION_DAYS.

    Directory layout: <FACE_UPLOAD_DIR>/<YYYY-MM-DD>/<employee_id>/...
    We iterate the date-level directories and remove those whose date
    is older than the retention cutoff.
    """
    base_dir = Path(settings.FACE_UPLOAD_DIR)
    if not base_dir.exists():
        return

    cutoff_date = datetime.now(timezone.utc).date() - timedelta(days=settings.FACE_RETENTION_DAYS)
    removed = 0
    for date_dir in base_dir.iterdir():
        if not date_dir.is_dir():
            continue
        try:
            dir_date = date.fromisoformat(date_dir.name)
        except ValueError:
            continue
        if dir_date < cutoff_date:
            try:
                shutil.rmtree(date_dir)
                removed += 1
                logger.info("face_cleanup: removed %s", date_dir)
            except OSError:
                logger.exception("face_cleanup: failed to remove %s", date_dir)

    if removed:
        logger.info("face_cleanup: removed %s date directories (cutoff %s)", removed, cutoff_date)


# Track last cleanup date to run at most once per calendar day.
_last_cleanup_date: date | None = None


def _reminder_loop() -> None:
    global _last_cleanup_date
    while not _stop_event.is_set():
        try:
            send_checkout_reminders()
        except Exception:
            logger.exception("checkout_reminder: unhandled error in loop")

        # Run face cleanup once per calendar day (VN time).
        # Triggers at 02:00 or on the first loop tick after server restarts
        # past 02:00 so the cleanup is never skipped on restarts.
        try:
            now_vn = _now_vn()
            today_vn = now_vn.date()
            due = now_vn.hour >= 2 and _last_cleanup_date != today_vn
            if due:
                _last_cleanup_date = today_vn
                cleanup_old_face_images()
        except Exception:
            logger.exception("face_cleanup: unhandled error in loop")

        if _stop_event.wait(POLL_INTERVAL_SECONDS):
            break


def start_reminder_scheduler() -> None:
    global _reminder_thread
    if _reminder_thread is not None and _reminder_thread.is_alive():
        return
    _stop_event.clear()
    _reminder_thread = threading.Thread(
        target=_reminder_loop,
        name="checkout-reminder",
        daemon=True,
    )
    _reminder_thread.start()
    logger.info("checkout_reminder: scheduler started (poll every %ss)", POLL_INTERVAL_SECONDS)


def stop_reminder_scheduler() -> None:
    global _reminder_thread
    _stop_event.set()
    if _reminder_thread and _reminder_thread.is_alive():
        _reminder_thread.join(timeout=3)
    _reminder_thread = None
    logger.info("checkout_reminder: scheduler stopped")
