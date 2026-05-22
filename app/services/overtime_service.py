"""Overtime service — Phase 2.5

Encapsulates auto-creation, approval, rejection, edit, and exception-driven
creation of OvertimeRecord plus its OvertimeAudit trail.
"""
from __future__ import annotations

from datetime import date, datetime, time, timezone
from typing import Iterable

from fastapi import HTTPException
from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.models import (
    AttendanceLog,
    CheckinRule,
    OvertimeAudit,
    OvertimeRecord,
    PublicHoliday,
)
from app.services.attendance_time import split_regular_overtime_minutes


# ── Constants ────────────────────────────────────────────────────────────────
ADJUSTMENT_NOTE_THRESHOLD_MIN = 30  # |approved - raw| > 30 → admin_note required
DEFAULT_SHIFT_START = time(8, 0)
DEFAULT_SHIFT_END = time(17, 0)


# ── Internal helpers ─────────────────────────────────────────────────────────
def _get_active_rule(db: Session) -> CheckinRule | None:
    return db.query(CheckinRule).filter(CheckinRule.active.is_(True)).first()


def _is_holiday(db: Session, work_date: date) -> bool:
    return db.query(PublicHoliday).filter(PublicHoliday.date == work_date).first() is not None


def _is_weekend(work_date: date) -> bool:
    # Monday=0 ... Sunday=6
    return work_date.weekday() >= 5


def _write_audit(
    db: Session,
    overtime_id: int,
    *,
    action: str,
    actor_id: int | None,
    from_status: str | None = None,
    to_status: str | None = None,
    from_minutes: int | None = None,
    to_minutes: int | None = None,
    note: str | None = None,
) -> OvertimeAudit:
    audit = OvertimeAudit(
        overtime_id=overtime_id,
        action=action,
        actor_id=actor_id,
        from_status=from_status,
        to_status=to_status,
        from_minutes=from_minutes,
        to_minutes=to_minutes,
        note=note,
    )
    db.add(audit)
    return audit


# ── Public API ───────────────────────────────────────────────────────────────
def auto_create_pending_ot(
    db: Session,
    attendance_log: AttendanceLog,
    *,
    checkin_log: AttendanceLog | None = None,
) -> OvertimeRecord | None:
    """Called after a normal OUT checkin. Creates a PENDING OT record if:
      - overtime_enabled is True (company-wide setting)
      - work_date is set
      - raw_minutes >= overtime_minimum_minutes threshold
      - no existing record for (employee_id, work_date)

    Returns the created record, or None if any condition fails.
    """
    if attendance_log.type != "OUT" or not attendance_log.work_date:
        return None

    rule = _get_active_rule(db)
    if rule is None or not rule.overtime_enabled:
        return None

    threshold = rule.overtime_minimum_minutes or 30

    # Resolve checkin pair
    if checkin_log is None:
        checkin_log = (
            db.query(AttendanceLog)
            .filter(
                AttendanceLog.employee_id == attendance_log.employee_id,
                AttendanceLog.work_date == attendance_log.work_date,
                AttendanceLog.type == "IN",
            )
            .order_by(AttendanceLog.time.asc())
            .first()
        )
    if checkin_log is None:
        return None

    shift_start = checkin_log.snapshot_start_time or rule.start_time or DEFAULT_SHIFT_START
    shift_end = checkin_log.snapshot_end_time or rule.end_time or DEFAULT_SHIFT_END

    _, overtime_min, _ = split_regular_overtime_minutes(
        attendance_log.work_date,
        checkin_log.time,
        attendance_log.time,
        shift_start,
        shift_end,
    )
    if overtime_min < threshold:
        return None

    # Idempotency: skip if already exists for this (employee, work_date)
    existing = (
        db.query(OvertimeRecord)
        .filter(
            OvertimeRecord.employee_id == attendance_log.employee_id,
            OvertimeRecord.work_date == attendance_log.work_date,
        )
        .first()
    )
    if existing is not None:
        return existing

    record = OvertimeRecord(
        employee_id=attendance_log.employee_id,
        work_date=attendance_log.work_date,
        attendance_log_id=attendance_log.id,
        raw_minutes=overtime_min,
        approved_minutes=None,
        status="PENDING",
        source="AUTO_CHECKOUT",
        shift_start_snapshot=shift_start,
        shift_end_snapshot=shift_end,
        is_weekend=_is_weekend(attendance_log.work_date),
        is_holiday=_is_holiday(db, attendance_log.work_date),
    )
    db.add(record)
    db.flush()  # populate record.id for audit FK

    _write_audit(
        db, record.id,
        action="CREATED",
        actor_id=None,
        to_status="PENDING",
        to_minutes=overtime_min,
        note="auto-created from checkout",
    )
    return record


def approve_overtime(
    db: Session,
    ot_id: int,
    *,
    approved_minutes: int,
    admin_id: int,
    admin_note: str | None,
) -> OvertimeRecord:
    record = db.get(OvertimeRecord, ot_id)
    if record is None:
        raise HTTPException(status_code=404, detail="overtime record not found")
    if record.status != "PENDING":
        raise HTTPException(status_code=400, detail=f"cannot approve record in status {record.status}")

    delta = abs(approved_minutes - record.raw_minutes)
    if delta > ADJUSTMENT_NOTE_THRESHOLD_MIN:
        if not admin_note or not admin_note.strip():
            raise HTTPException(
                status_code=400,
                detail=f"admin_note is required when adjusting more than {ADJUSTMENT_NOTE_THRESHOLD_MIN} minutes from raw",
            )

    prev_status = record.status
    record.status = "APPROVED"
    record.approved_minutes = approved_minutes
    record.admin_id = admin_id
    record.admin_note = (admin_note or "").strip() or None
    record.decided_at = datetime.now(timezone.utc)

    _write_audit(
        db, record.id,
        action="APPROVED",
        actor_id=admin_id,
        from_status=prev_status,
        to_status="APPROVED",
        from_minutes=record.raw_minutes,
        to_minutes=approved_minutes,
        note=record.admin_note,
    )
    return record


def reject_overtime(
    db: Session,
    ot_id: int,
    *,
    admin_id: int,
    admin_note: str,
) -> OvertimeRecord:
    record = db.get(OvertimeRecord, ot_id)
    if record is None:
        raise HTTPException(status_code=404, detail="overtime record not found")
    if record.status != "PENDING":
        raise HTTPException(status_code=400, detail=f"cannot reject record in status {record.status}")

    note = (admin_note or "").strip()
    if not note:
        raise HTTPException(status_code=400, detail="admin_note is required when rejecting")

    prev_status = record.status
    record.status = "REJECTED"
    record.approved_minutes = 0
    record.admin_id = admin_id
    record.admin_note = note
    record.decided_at = datetime.now(timezone.utc)

    _write_audit(
        db, record.id,
        action="REJECTED",
        actor_id=admin_id,
        from_status=prev_status,
        to_status="REJECTED",
        from_minutes=record.raw_minutes,
        to_minutes=0,
        note=note,
    )
    return record


def edit_approved_overtime(
    db: Session,
    ot_id: int,
    *,
    approved_minutes: int,
    admin_id: int,
    admin_note: str,
) -> OvertimeRecord:
    """Admin correction on an already-APPROVED record. Status remains APPROVED.

    admin_note is always required for edits (audit trail).
    """
    record = db.get(OvertimeRecord, ot_id)
    if record is None:
        raise HTTPException(status_code=404, detail="overtime record not found")
    if record.status != "APPROVED":
        raise HTTPException(
            status_code=400,
            detail="can only edit an APPROVED record; use approve/reject for PENDING",
        )

    note = (admin_note or "").strip()
    if not note:
        raise HTTPException(status_code=400, detail="admin_note is required when editing")

    from_minutes = record.approved_minutes
    record.approved_minutes = approved_minutes
    record.admin_id = admin_id
    record.admin_note = note
    record.decided_at = datetime.now(timezone.utc)

    _write_audit(
        db, record.id,
        action="EDITED",
        actor_id=admin_id,
        from_status="APPROVED",
        to_status="APPROVED",
        from_minutes=from_minutes,
        to_minutes=approved_minutes,
        note=note,
    )
    return record


def create_or_approve_from_exception(
    db: Session,
    *,
    attendance_log: AttendanceLog,
    checkin_log: AttendanceLog | None,
    actual_checkout_time: datetime | None,
    approved_minutes: int,
    admin_id: int,
    admin_note: str | None,
) -> OvertimeRecord | None:
    """Called from MISSED_CHECKOUT/AUTO_CLOSED approval to combine OT decision.

    Behavior:
      - If overtime_enabled is False → return None (no OT record created)
      - If actual_checkout_time is at or before shift_end → return None
      - Otherwise: create a record APPROVED directly (skip PENDING) since
        admin already implicitly confirmed by entering actual_checkout_time
      - If a record already exists for (employee, work_date), update it instead
    """
    if not attendance_log.work_date or actual_checkout_time is None:
        return None

    rule = _get_active_rule(db)
    if rule is None or not rule.overtime_enabled:
        return None

    if checkin_log is None:
        checkin_log = (
            db.query(AttendanceLog)
            .filter(
                AttendanceLog.employee_id == attendance_log.employee_id,
                AttendanceLog.work_date == attendance_log.work_date,
                AttendanceLog.type == "IN",
            )
            .order_by(AttendanceLog.time.asc())
            .first()
        )
    if checkin_log is None:
        return None

    shift_start = checkin_log.snapshot_start_time or rule.start_time or DEFAULT_SHIFT_START
    shift_end = checkin_log.snapshot_end_time or rule.end_time or DEFAULT_SHIFT_END

    _, raw_overtime_min, _ = split_regular_overtime_minutes(
        attendance_log.work_date,
        checkin_log.time,
        actual_checkout_time,
        shift_start,
        shift_end,
    )
    if raw_overtime_min <= 0:
        return None

    note = (admin_note or "").strip() or None

    existing = (
        db.query(OvertimeRecord)
        .filter(
            OvertimeRecord.employee_id == attendance_log.employee_id,
            OvertimeRecord.work_date == attendance_log.work_date,
        )
        .first()
    )

    if existing is not None:
        prev_status = existing.status
        from_minutes = existing.approved_minutes
        existing.raw_minutes = raw_overtime_min
        existing.approved_minutes = approved_minutes
        existing.status = "APPROVED"
        existing.source = "EXCEPTION_APPROVAL"
        existing.admin_id = admin_id
        existing.admin_note = note
        existing.decided_at = datetime.now(timezone.utc)
        _write_audit(
            db, existing.id,
            action="EDITED" if prev_status == "APPROVED" else "APPROVED",
            actor_id=admin_id,
            from_status=prev_status,
            to_status="APPROVED",
            from_minutes=from_minutes,
            to_minutes=approved_minutes,
            note=f"from exception approval: {note or ''}".strip(),
        )
        return existing

    record = OvertimeRecord(
        employee_id=attendance_log.employee_id,
        work_date=attendance_log.work_date,
        attendance_log_id=attendance_log.id,
        raw_minutes=raw_overtime_min,
        approved_minutes=approved_minutes,
        status="APPROVED",
        source="EXCEPTION_APPROVAL",
        admin_id=admin_id,
        admin_note=note,
        decided_at=datetime.now(timezone.utc),
        shift_start_snapshot=shift_start,
        shift_end_snapshot=shift_end,
        is_weekend=_is_weekend(attendance_log.work_date),
        is_holiday=_is_holiday(db, attendance_log.work_date),
    )
    db.add(record)
    db.flush()

    _write_audit(
        db, record.id,
        action="APPROVED",
        actor_id=admin_id,
        to_status="APPROVED",
        from_minutes=raw_overtime_min,
        to_minutes=approved_minutes,
        note=f"created and approved from exception: {note or ''}".strip(),
    )
    return record


# ── Bulk approve helpers ─────────────────────────────────────────────────────
def round_up_to_30(minutes: int) -> int:
    if minutes <= 0:
        return 0
    return ((minutes + 29) // 30) * 30


def bulk_approve(
    db: Session,
    *,
    ids: Iterable[int],
    strategy: str,
    admin_id: int,
    admin_note: str | None,
) -> tuple[int, list[int]]:
    """Approve multiple PENDING records. Returns (approved_count, skipped_ids).

    strategy:
      - "as_is": approved_minutes = raw_minutes
      - "round_up_30": approved_minutes = round_up_to_30(raw_minutes)
    """
    id_list = list(ids)
    if not id_list:
        return 0, []

    # Pre-fetch all records in a single query (avoids N+1 SELECTs).
    records = (
        db.query(OvertimeRecord)
        .filter(OvertimeRecord.id.in_(id_list))
        .all()
    )
    record_by_id = {r.id: r for r in records}

    approved = 0
    skipped: list[int] = []
    for ot_id in id_list:
        record = record_by_id.get(ot_id)
        if record is None or record.status != "PENDING":
            skipped.append(ot_id)
            continue
        target = round_up_to_30(record.raw_minutes) if strategy == "round_up_30" else record.raw_minutes
        approve_overtime(
            db, ot_id,
            approved_minutes=target,
            admin_id=admin_id,
            admin_note=admin_note,
        )
        approved += 1
    return approved, skipped


# ── Read helpers used by reports.py ──────────────────────────────────────────
def fetch_payable_minutes_map(
    db: Session,
    *,
    employee_ids: list[int] | None = None,
    from_date: date | None = None,
    to_date: date | None = None,
) -> dict[tuple[int, date], int]:
    """Returns {(employee_id, work_date): approved_minutes} for APPROVED records.

    Used by reports.py to compute payable_overtime_minutes from approval state
    instead of deriving from exception status.
    """
    q = select(
        OvertimeRecord.employee_id,
        OvertimeRecord.work_date,
        OvertimeRecord.approved_minutes,
    ).where(OvertimeRecord.status == "APPROVED")
    if employee_ids:
        q = q.where(OvertimeRecord.employee_id.in_(employee_ids))
    if from_date:
        q = q.where(OvertimeRecord.work_date >= from_date)
    if to_date:
        q = q.where(OvertimeRecord.work_date <= to_date)

    result: dict[tuple[int, date], int] = {}
    for emp_id, work_date, approved in db.execute(q):
        result[(int(emp_id), work_date)] = int(approved or 0)
    return result
